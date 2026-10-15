"""Authenticated data hub, explicit OCR adapter and immutable batch packages.

Package generation consumes only the core's accepted revision snapshot. It never
uses Workshop's global exports. OCR is opt-in and makes no provider request when
the adapter has not been configured.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import queue
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
import zipfile

try:
    from .chapter_pipeline import NoRedirect, now, uid, js
except ImportError:
    from chapter_pipeline import NoRedirect, now, uid, js

ROLES = {'external': 0, 'internal': 1, 'admin': 2}
MAX_OCR_RESPONSE = 32 * 1024 * 1024
MAX_OCR_FILE = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024


class PackageFiles(dict):
    def __init__(self, error_class):
        super().__init__()
        self.total, self.Error = 0, error_class

    def __setitem__(self, key, value):
        total = self.total - len(self.get(key, b'')) + len(value)
        if total > MAX_PACKAGE_BYTES:
            raise self.Error('单个数据包展开内容超过 256 MiB，请拆分处理批次。', 413)
        self.total = total
        super().__setitem__(key, value)


class PlatformData:
    def __init__(self, workshop, chapters, core, error_class):
        self.workshop, self.chapters, self.core, self.Error = workshop, chapters, core, error_class
        self.lock = workshop.lock
        self.root = workshop.root / 'packages'
        self.root.mkdir(exist_ok=True)
        self.tasks, self.stopping = queue.Queue(), threading.Event()
        self.init_db()
        self.worker = threading.Thread(target=self.work_loop, daemon=True, name='ocr-worker')
        self.worker.start()

    def db(self):
        return self.workshop.connect()

    def require(self, actor, minimum='internal'):
        if not actor:
            raise self.Error('请先登录。', 401)
        if actor.get('role') not in ROLES or ROLES[actor['role']] < ROLES[minimum]:
            raise self.Error('当前账号没有此操作权限。', 403)

    def audit(self, actor, event, detail, batch_id=None, db=None):
        self.core.audit(actor, event, detail, batch_id=batch_id, db=db)

    def init_db(self):
        with self.lock, self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS pd_asset_links (
              asset_id TEXT NOT NULL,document_id TEXT NOT NULL DEFAULT '',batch_id TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,PRIMARY KEY(asset_id,document_id,batch_id));
            CREATE TABLE IF NOT EXISTS pd_ocr_config (
              id INTEGER PRIMARY KEY CHECK(id=1),endpoint TEXT NOT NULL,key TEXT NOT NULL);
            INSERT OR IGNORE INTO pd_ocr_config VALUES(1,'','');
            CREATE TABLE IF NOT EXISTS pd_ocr_tasks (
              id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,status TEXT NOT NULL,error TEXT,
              document_id TEXT,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,
              actor TEXT NOT NULL,config TEXT NOT NULL,options TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pd_packages (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,batch_id TEXT,
              published INTEGER NOT NULL DEFAULT 0,minimum_role TEXT NOT NULL,
              created_at TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,
              payload TEXT NOT NULL,legacy_version_id TEXT UNIQUE);
            ''')
            db.execute("UPDATE pd_ocr_tasks SET status='error',error='服务重启中断 OCR，请重新提交任务。',finished_at=? WHERE status IN ('queued','processing')", (now(),))
            self.migrate_versions(db)

    def migrate_versions(self, db):
        for row in db.execute('SELECT * FROM versions WHERE id NOT IN (SELECT legacy_version_id FROM pd_packages WHERE legacy_version_id IS NOT NULL)').fetchall():
            path = self.workshop.local_path(row['path'])
            if not path.is_file():
                continue
            package_id = uid('package')
            archive = path.read_bytes()
            target = self.root / (package_id + '.zip')
            files = []
            try:
                sanitized = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(archive)) as zipped, zipfile.ZipFile(sanitized, 'w', zipfile.ZIP_DEFLATED) as output:
                    for info in zipped.infolist():
                        if PurePosixPath(info.filename).is_absolute() or '..' in PurePosixPath(info.filename).parts:
                            raise zipfile.BadZipFile('unsafe archive path')
                        data = zipped.read(info.filename)
                        if info.filename.endswith('.json'):
                            data = js(self.core.redact(json.loads(data), {'role': 'internal'})).encode()
                        elif info.filename.endswith('.jsonl'):
                            data = ('\n'.join(js(self.core.redact(json.loads(line), {'role': 'internal'})) for line in data.decode().splitlines() if line.strip()) + '\n').encode()
                        output.writestr(info.filename, data)
                        files.append({'path': info.filename, 'size': len(data)})
                archive = sanitized.getvalue()
                target.write_bytes(archive)
            except (zipfile.BadZipFile, ValueError, UnicodeError):
                target.unlink(missing_ok=True)
                continue
            payload = {'metadata': {'audit_status': 'historical', 'revision_eligible': False,
                       'review_note': '历史导出；没有对应的已验收批次快照。', 'record_count': row['count'],
                       'source_document_count': 0, 'chunk_count': 0, 'image_count': 0, 'file_count': len(files),
                       'processed_by_day': [], 'sample_pass_rate': None},
                       'source_documents': [], 'previews': [], 'files': files,
                       'snapshot': None, 'records': [], 'legacy_version_id': row['id']}
            db.execute('INSERT INTO pd_packages VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (package_id, row['name'], row['type'], None, 0, 'internal', row['created_at'],
                        target.relative_to(self.workshop.root).as_posix(), hashlib.sha256(archive).hexdigest(), js(payload), row['id']))

    def close(self):
        self.stopping.set()
        self.tasks.put(None)
        with self.lock, self.db() as db:
            db.execute("UPDATE pd_ocr_tasks SET status='error',error='服务停止中断 OCR，请重新提交任务。',finished_at=? WHERE status IN ('queued','processing')", (now(),))

    def batch_map(self, db):
        """Read only public batch IDs and document membership from the core table."""
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pc_batches'").fetchone():
            return {}
        columns = {row['name'] for row in db.execute('PRAGMA table_info(pc_batches)')}
        if 'document_ids' not in columns:
            return {}
        result = {}
        for row in db.execute('SELECT id,document_ids FROM pc_batches'):
            for document_id in json.loads(row['document_ids']):
                result.setdefault(document_id, []).append(row['id'])
        return result

    def data_hub(self, actor):
        self.require(actor)
        rows = []
        with self.lock, self.db() as db:
            memberships = self.batch_map(db)
            links = [dict(row) for row in db.execute('SELECT * FROM pd_asset_links')]
            documents = {row['id']: self.chapters.document(db, row['id']) for row in db.execute('SELECT id FROM cp_documents')}
            for row in db.execute('SELECT * FROM assets ORDER BY created_at DESC,rowid DESC'):
                asset = self.workshop.asset(db, row['id'])
                related = [link for link in links if link['asset_id'] == row['id']]
                provenance = json.loads(row['provenance'] or '{}')
                doc_ids = sorted({link['document_id'] for link in related if link['document_id']} | ({provenance['source_document_id']} if provenance.get('source_document_id') else set()))
                batch_ids = sorted({link['batch_id'] for link in related if link['batch_id']} | {bid for did in doc_ids for bid in memberships.get(did, [])})
                stage = 'processed' if provenance.get('source_document_id') or provenance.get('origin') == 'chapter_pipeline' else 'raw'
                rows.append({'id': row['id'], 'kind': 'asset', 'stage': stage, 'format': row['kind'], 'name': row['name'],
                             'status': row['status'], 'size': row['size'], 'created_at': row['created_at'],
                             'asset_id': row['id'], 'document_ids': doc_ids, 'batch_ids': batch_ids,
                             'preview': row['raw_text'][:1000], 'url': '/api/files/' + row['id'], 'provenance': provenance,
                             'license': row['license'], 'is_demo': bool(row['is_demo'])})
            for document_id, document in documents.items():
                common = {'document_id': document_id, 'document_ids': [document_id], 'document_title': document['title'],
                          'batch_ids': memberships.get(document_id, []), 'created_at': document['created_at'],
                          'license': document['license'], 'is_demo': document['is_demo'],
                          'provenance': {'source_document_id': document_id, 'source_metadata': document['source_metadata']}}
                for pdf in db.execute('SELECT * FROM cp_pdfs WHERE document_id=?', (document_id,)):
                    rows.append(dict(common, id=pdf['id'], kind='pdf', stage='raw', format='pdf', name=pdf['name'], status='original',
                                     size=pdf['size'], preview='', url='/api/chapters/pdfs/' + document_id + '/' + pdf['id']))
                page_images = {}
                for page in db.execute('SELECT number,images FROM cp_pages WHERE document_id=?', (document_id,)):
                    for image in json.loads(page['images']):
                        page_images.setdefault(image['id'], []).append(page['number'])
                for image in db.execute('SELECT * FROM cp_images WHERE document_id=? ORDER BY path', (document_id,)):
                    path = self.workshop.local_path(image['storage_path'])
                    image_url = '/api/chapters/images/' + document_id + '/' + image['id']
                    source_pages = sorted(set(page_images.get(image['id'], [])))
                    rows.append(dict(common, id=image['id'], image_id=image['id'], kind='image', stage='raw',
                                     format=PurePosixPath(image['path']).suffix.lstrip('.').lower(), mime=image['mime'],
                                     name=PurePosixPath(image['path']).name, status='original' if path.is_file() else 'missing',
                                     size=path.stat().st_size if path.is_file() else 0, preview='', preview_kind='image',
                                     url=image_url, preview_url=image_url, image_url=image_url, source_path=image['path'],
                                     source_pages=source_pages, page=source_pages[0] if source_pages else None))
                for page in db.execute('SELECT * FROM cp_pages WHERE document_id=? ORDER BY number', (document_id,)):
                    rows.append(dict(common, id=page['id'], kind='page', stage='ocr', format='md', name=document['title'] + ' · 第 ' + str(page['number']) + ' 页',
                                     status='original', size=len(page['text'].encode()), preview=page['text'][:1500], page=page['number'],
                                     source_path=page['path'], url=None))
                for chapter in db.execute('SELECT * FROM cp_chapters WHERE document_id=? AND active=1 ORDER BY ordinal', (document_id,)):
                    if not chapter['result']:
                        continue
                    rows.append(dict(common, id=chapter['id'], kind='chapter', stage='processed', format='md', name=chapter['title'],
                                     status=chapter['status'], revision=chapter['revision'], size=len(chapter['corpus'].encode()),
                                     preview=chapter['corpus'][:1500], chapter_id=chapter['id'], url=None))
        stats = {'total': len(rows), **{kind: sum(row['kind'] == kind for row in rows) for kind in ('asset', 'pdf', 'image', 'page', 'chapter')}}
        return {'rows': rows, 'stats': stats, 'links': links, 'documents': list(documents.values()),
                'batches': [{'id': bid, 'document_ids': [did for did, bids in memberships.items() if bid in bids]} for bid in sorted({bid for bids in memberships.values() for bid in bids})]}

    def ingest_asset(self, body, actor):
        self.require(actor)
        with self.lock:
            with self.db() as db:
                asset = self.workshop.asset(db, body.get('asset_id'))
                if asset['kind'] not in {'md', 'markdown', 'txt'}:
                    raise self.Error('此入口仅接受 Markdown 或纯文本；PDF/图像请创建 OCR 任务。')
                row = db.execute('SELECT * FROM assets WHERE id=?', (asset['id'],)).fetchone()
                raw = self.workshop.local_path(row['path']).read_bytes()
                self.chapters.text(raw)
                self.chapters.library.validate_target(db, body)
            imported = self.chapters.import_package({'name': asset['name'], 'files': [{'path': 'pages/page_1.md', 'content_base64': base64.b64encode(raw).decode()}],
                        'purpose': asset['purpose'], 'license': asset['license'], **{key: body[key] for key in ('source_id', 'record_id') if key in body}})
            with self.db() as db:
                db.execute('INSERT OR IGNORE INTO pd_asset_links VALUES(?,?,?,?)', (asset['id'], imported['document']['id'], '', now()))
                row = db.execute('SELECT source_metadata FROM cp_documents WHERE id=?', (imported['document']['id'],)).fetchone()
                source_metadata = json.loads(row['source_metadata'])
                source_metadata.setdefault('source_asset_ids', [])
                if asset['id'] not in source_metadata['source_asset_ids']:
                    source_metadata['source_asset_ids'].append(asset['id'])
                db.execute('UPDATE cp_documents SET source_metadata=? WHERE id=?', (js(source_metadata), imported['document']['id']))
        self.audit(actor, 'data_ingest', {'asset_id': asset['id'], 'document_id': imported['document']['id']})
        return imported

    def link_asset(self, body, actor):
        self.require(actor)
        asset_id, document_id, batch_id = body.get('asset_id'), body.get('document_id', ''), body.get('batch_id', '')
        if not isinstance(asset_id, str) or not isinstance(document_id, str) or not isinstance(batch_id, str):
            raise self.Error('资产、文献与批次 ID 须为字符串。')
        if not document_id and not batch_id:
            raise self.Error('请选择关联文献或处理批次。')
        with self.lock, self.db() as db:
            self.workshop.asset(db, asset_id)
            if document_id:
                self.chapters.document(db, document_id)
            if batch_id:
                batch = db.execute('SELECT document_ids FROM pc_batches WHERE id=?', (batch_id,)).fetchone()
                if not batch:
                    raise self.Error('批次不存在。', 404)
                if document_id and document_id not in json.loads(batch['document_ids']):
                    raise self.Error('文献不属于所选批次。', 409)
            db.execute('INSERT OR IGNORE INTO pd_asset_links VALUES(?,?,?,?)', (asset_id, document_id, batch_id, now()))
        self.audit(actor, 'data_link', {'asset_id': asset_id, 'document_id': document_id, 'batch_id': batch_id}, batch_id=batch_id or None)
        return {'link': {'asset_id': asset_id, 'document_id': document_id, 'batch_id': batch_id}}

    def ocr_config(self, actor, private=False):
        self.require(actor, 'admin')
        with self.db() as db:
            row = dict(db.execute('SELECT * FROM pd_ocr_config WHERE id=1').fetchone())
        return row if private else {'endpoint': row['endpoint'], 'has_key': bool(row['key']), 'configured': bool(row['endpoint']),
                                   'contract': 'POST {filename,content_base64}; JSON {pages:[{page,markdown}],images:[{path,content_base64}]}'}

    def configure_ocr(self, body, actor):
        old = self.ocr_config(actor, private=True)
        endpoint = body.get('endpoint', old['endpoint'])
        key = body.get('key', old['key'])
        if not isinstance(endpoint, str) or len(endpoint) > 1000:
            raise self.Error('OCR 接口地址无效。')
        endpoint = endpoint.strip()
        if endpoint:
            parsed = urlsplit(endpoint)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise self.Error('OCR 地址须为 HTTP(S)，不能包含凭据或查询参数。')
        if key is None:
            key = ''
        if not isinstance(key, str) or len(key) > 8192 or '\r' in key or '\n' in key:
            raise self.Error('OCR 密钥格式无效。')
        with self.lock, self.db() as db:
            db.execute('UPDATE pd_ocr_config SET endpoint=?,key=? WHERE id=1', (endpoint, key))
        self.audit(actor, 'ocr_config', {'configured': bool(endpoint), 'endpoint': endpoint})
        return {'config': self.ocr_config(actor)}

    @staticmethod
    def task_public(row):
        options = json.loads(row['options'])
        return {key: row[key] for key in ('id', 'asset_id', 'status', 'error', 'document_id', 'created_at', 'started_at', 'finished_at')} | {
            key: options[key] for key in ('record_id', 'source_id', 'batch_id') if key in options}

    def ocr_tasks(self, actor, task_id=None):
        self.require(actor)
        with self.db() as db:
            if task_id:
                row = db.execute('SELECT * FROM pd_ocr_tasks WHERE id=?', (task_id,)).fetchone()
                if not row:
                    raise self.Error('OCR 任务不存在。', 404)
                return {'task': self.task_public(row)}
            return {'tasks': [self.task_public(row) for row in db.execute('SELECT * FROM pd_ocr_tasks ORDER BY created_at DESC,rowid DESC')]}

    def create_ocr(self, body, actor):
        self.require(actor)
        asset_id = body.get('asset_id')
        if not isinstance(asset_id, str):
            raise self.Error('请选择待识别的原始资产。')
        with self.lock, self.db() as db:
            asset = self.workshop.asset(db, asset_id)
            if asset['kind'] not in {'pdf', 'png', 'jpg', 'jpeg', 'webp'}:
                raise self.Error('OCR 仅接受已上传 PDF、PNG、JPEG 或 WebP 原件。')
            row = db.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
            if row['size'] > MAX_OCR_FILE:
                raise self.Error('OCR 单文件不能超过 16 MiB。', 413)
            raw = self.workshop.local_path(row['path']).read_bytes()
            if asset['kind'] == 'pdf':
                self.chapters.pdf_metadata(raw)
            elif not (raw.startswith(b'\x89PNG\r\n\x1a\n') if asset['kind'] == 'png' else raw.startswith(b'\xff\xd8\xff') if asset['kind'] in {'jpg', 'jpeg'} else raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'):
                raise self.Error('OCR 原件内容与声明的图像类型不符。')
            config = dict(db.execute('SELECT * FROM pd_ocr_config WHERE id=1').fetchone())
            options = {key: body[key] for key in ('record_id', 'source_id', 'batch_id') if key in body}
            self.chapters.library.validate_target(db, options)
            if 'batch_id' in options:
                if not isinstance(options['batch_id'], str):
                    raise self.Error('批次 ID 无效。')
                if not db.execute('SELECT 1 FROM pc_batches WHERE id=?', (options['batch_id'],)).fetchone():
                    raise self.Error('批次不存在。', 404)
            task_id, created_at = uid('ocr'), now()
            status = 'queued' if config['endpoint'] else 'blocked'
            error = None if config['endpoint'] else '尚未配置 OCR 服务；原始文件已保留，未执行识别。'
            db.execute('INSERT INTO pd_ocr_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (task_id, asset_id, status, error, None, created_at, None, None, js(actor), js(config), js(options)))
        self.audit(actor, 'ocr_requested', {'task_id': task_id, 'asset_id': asset_id, 'status': status})
        if status == 'queued':
            self.tasks.put(task_id)
        return self.ocr_tasks(actor, task_id)

    def ocr_files(self, result, raw_name, raw):
        if not isinstance(result, dict) or not isinstance(result.get('pages'), list) or not 1 <= len(result['pages']) <= 1000:
            raise self.Error('OCR 返回须包含 1–1000 页 pages 数组。')
        images = result.get('images', [])
        if not isinstance(images, list) or len(images) > 1000:
            raise self.Error('OCR 图像列表无效。')
        files, used, text_size = [], set(), 0
        image_paths = set()
        for image in images:
            if not isinstance(image, dict):
                raise self.Error('OCR 图像条目无效。')
            path = self.chapters.path(image.get('path'))
            extension = PurePosixPath(path).suffix.lower()
            if not path.startswith('images/') or extension not in {'.png', '.jpg', '.jpeg', '.webp'} or path in image_paths:
                raise self.Error('OCR 图像须为 images/ 中唯一的 PNG/JPEG/WebP 文件。')
            data = self.chapters.decode(image.get('content_base64'), 5 * 1024 * 1024)
            valid = data.startswith(b'\x89PNG\r\n\x1a\n') if extension == '.png' else data.startswith(b'\xff\xd8\xff') if extension in {'.jpg', '.jpeg'} else data.startswith(b'RIFF') and data[8:12] == b'WEBP'
            if not valid:
                raise self.Error('OCR 图像内容与文件类型不符。')
            image_paths.add(path)
            files.append({'path': path, 'content_base64': base64.b64encode(data).decode()})
        for page in result['pages']:
            if not isinstance(page, dict):
                raise self.Error('OCR 页条目无效。')
            number, markdown = page.get('page'), page.get('markdown')
            if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 100000 or number in used or not isinstance(markdown, str) or not markdown.strip():
                raise self.Error('OCR 页须有唯一正整数 page 和非空 markdown。')
            text_size += len(markdown.encode())
            if text_size > 16 * 1024 * 1024:
                raise self.Error('OCR 文本超过 16 MiB。', 413)
            if re.search(r'<\s*(?:script|iframe|object|embed|img|svg)\b', markdown, re.I):
                raise self.Error('OCR Markdown 包含不允许的 HTML。')
            if re.search(r'!\[[^\]]*\](?!\()', markdown):
                raise self.Error('OCR 图片仅支持本地行内 Markdown 链接。')
            path = f'pages/page_{number:05d}.md'
            for target in re.findall(r'!\[[^\]]*\]\(([^\s)]+)\)', markdown):
                if not self.chapters.resolve_image(path, target, image_paths):
                    raise self.Error('OCR 页引用了缺失、外部或越界图像。')
            used.add(number)
            files.append({'path': path, 'content_base64': base64.b64encode(markdown.encode()).decode()})
        if PurePosixPath(raw_name).suffix.lower() == '.pdf':
            files.append({'path': 'original.pdf', 'content_base64': base64.b64encode(raw).decode()})
        return files

    def work_loop(self):
        while not self.stopping.is_set():
            task_id = self.tasks.get()
            if task_id is None:
                break
            try:
                self.run_ocr(task_id)
            except Exception:
                # Exceptions are handled per task; no credentials or raw provider
                # response is recorded in user-visible state.
                with self.lock, self.db() as db:
                    db.execute("UPDATE pd_ocr_tasks SET status='error',error='OCR 任务异常，请检查服务配置后重试。',finished_at=? WHERE id=? AND status IN ('queued','processing')", (now(), task_id))

    def run_ocr(self, task_id):
        with self.lock, self.db() as db:
            row = db.execute('SELECT * FROM pd_ocr_tasks WHERE id=?', (task_id,)).fetchone()
            if not row or row['status'] != 'queued':
                return
            task = dict(row)
            db.execute("UPDATE pd_ocr_tasks SET status='processing',started_at=? WHERE id=?", (now(), task_id))
            asset = dict(db.execute('SELECT * FROM assets WHERE id=?', (task['asset_id'],)).fetchone())
        config, options, actor = json.loads(task['config']), json.loads(task['options']), json.loads(task['actor'])
        try:
            raw = self.workshop.local_path(asset['path']).read_bytes()
            headers = {'Content-Type': 'application/json'}
            if config['key']:
                headers['Authorization'] = 'Bearer ' + config['key']
            request = Request(config['endpoint'], data=js({'filename': asset['name'], 'content_base64': base64.b64encode(raw).decode()}).encode(), headers=headers, method='POST')
            with build_opener(NoRedirect()).open(request, timeout=60) as response:
                content = response.read(MAX_OCR_RESPONSE + 1)
            if len(content) > MAX_OCR_RESPONSE:
                raise self.Error('OCR 响应超过 32 MiB。', 413)
            try:
                parsed = json.loads(content)
            except (ValueError, UnicodeError):
                raise self.Error('OCR 服务未返回有效 JSON。')
            files = self.ocr_files(parsed, asset['name'], raw)
            with self.lock:
                if self.stopping.is_set():
                    return
                with self.db() as db:
                    if db.execute('SELECT status FROM pd_ocr_tasks WHERE id=?', (task_id,)).fetchone()['status'] != 'processing':
                        return
                imported = self.chapters.import_package({'name': asset['name'] + ' · OCR', 'files': files,
                           'purpose': asset['purpose'], 'license': asset['license'],
                           **{key: options[key] for key in ('record_id', 'source_id') if key in options}})
                document_id = imported['document']['id']
                with self.db() as db:
                    db.execute('INSERT OR IGNORE INTO pd_asset_links VALUES(?,?,?,?)', (task['asset_id'], document_id, '', now()))
                    if options.get('batch_id'):
                        db.execute('INSERT OR IGNORE INTO pd_asset_links VALUES(?,?,?,?)', (task['asset_id'], '', options['batch_id'], now()))
                    source_metadata = json.loads(db.execute('SELECT source_metadata FROM cp_documents WHERE id=?', (document_id,)).fetchone()['source_metadata'])
                    source_metadata.setdefault('ocr_imports', []).append({'task_id': task_id, 'source_asset_id': task['asset_id'], 'endpoint': config['endpoint'], 'processed_at': now()})
                    db.execute('UPDATE cp_documents SET source_metadata=? WHERE id=?', (js(source_metadata), document_id))
                    db.execute("UPDATE pd_ocr_tasks SET status='completed',document_id=?,error=NULL,finished_at=? WHERE id=?", (document_id, now(), task_id))
            self.audit(actor, 'ocr_completed', {'task_id': task_id, 'asset_id': task['asset_id'], 'document_id': document_id})
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            message = 'OCR 服务请求失败' + ('（HTTP ' + str(error.code) + '）' if isinstance(error, HTTPError) else '') + '；未导入识别结果。'
            with self.lock, self.db() as db:
                db.execute("UPDATE pd_ocr_tasks SET status='error',error=?,finished_at=? WHERE id=? AND status='processing'", (message, now(), task_id))
        except self.Error as error:
            with self.lock, self.db() as db:
                db.execute("UPDATE pd_ocr_tasks SET status='error',error=?,finished_at=? WHERE id=? AND status='processing'", (str(error), now(), task_id))

    def external_clearance(self, row):
        payload = json.loads(row['payload'])
        documents = payload['source_documents']
        if payload['metadata']['audit_status'] != 'accepted' or not documents:
            return False
        with self.db() as db:
            for document in documents:
                current = db.execute('SELECT license,purpose FROM cp_documents WHERE id=?', (document['id'],)).fetchone()
                if document.get('license') != 'self_owned' or not current or current['license'] != 'self_owned' or current['purpose'] not in {'both', 'kb' if row['type'] == 'kb' else 'training'}:
                    return False
                for linked in db.execute('SELECT a.license,a.purpose FROM pd_asset_links l JOIN assets a ON a.id=l.asset_id WHERE l.document_id=?', (document['id'],)):
                    if linked['license'] != 'self_owned' or linked['purpose'] not in {'both', 'kb' if row['type'] == 'kb' else 'training'}:
                        return False
        return True

    def allowed(self, row, actor):
        return bool(actor and actor.get('role') in ROLES and ROLES[actor['role']] >= ROLES[row['minimum_role']]
                    and (row['published'] or actor['role'] in {'internal', 'admin'})
                    and (actor['role'] != 'external' or self.external_clearance(row)))

    def package_row(self, db, package_id, actor):
        self.require(actor, 'external')
        row = db.execute('SELECT * FROM pd_packages WHERE id=?', (package_id,)).fetchone()
        if not row or not self.allowed(row, actor):
            raise self.Error('数据包不存在或当前账号没有访问权限。', 404)
        return row

    def package_public(self, row, detail=False):
        payload = json.loads(row['payload'])
        result = {key: row[key] for key in ('id', 'name', 'type', 'batch_id', 'minimum_role', 'created_at', 'sha256')}
        result.update({'published': bool(row['published']), 'download_url': '/api/packages/' + row['id'] + '/download',
                       'metadata': payload['metadata'], 'source_documents': payload['source_documents']})
        if detail:
            result.update({key: payload[key] for key in ('previews', 'files')})
            result['review_snapshot'] = payload['snapshot']
        return result

    def packages(self, actor):
        self.require(actor, 'external')
        with self.lock, self.db() as db:
            return {'packages': [self.package_public(row) for row in db.execute('SELECT * FROM pd_packages ORDER BY created_at DESC,rowid DESC') if self.allowed(row, actor)]}

    def package_detail(self, package_id, actor):
        with self.db() as db:
            return {'package': self.package_public(self.package_row(db, package_id, actor), True)}

    def publish(self, package_id, body, actor):
        self.require(actor, 'admin')
        with self.lock, self.db() as db:
            row = self.package_row(db, package_id, actor)
            published, minimum = body.get('published', bool(row['published'])), body.get('minimum_role', row['minimum_role'])
            if not isinstance(published, bool) or not isinstance(minimum, str) or minimum not in ROLES:
                raise self.Error('发布状态或最低访问角色无效。')
            if published and json.loads(row['payload'])['metadata']['audit_status'] != 'accepted':
                raise self.Error('历史导出没有验收批次凭据，请重新按已验收批次生成后发布。', 409)
            if published and minimum == 'external' and not self.external_clearance(row):
                raise self.Error('包内文献没有对外发布所需授权；仅内部科研或授权未确认的内容不能对外开放。', 409)
            db.execute('UPDATE pd_packages SET published=?,minimum_role=? WHERE id=?', (int(published), minimum, package_id))
        self.audit(actor, 'package_publish', {'package_id': package_id, 'published': published, 'minimum_role': minimum})
        return self.package_detail(package_id, actor)

    def download(self, package_id, actor):
        with self.db() as db:
            row = self.package_row(db, package_id, actor)
            path, expected = self.workshop.local_path(row['path']), row['sha256']
            filename = row['id'] + '.zip'
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise self.Error('数据包校验失败，请从备份恢复原始版本。', 409)
        self.audit(actor, 'package_download', {'package_id': package_id})
        return {'_binary': data, 'content_type': 'application/zip', 'filename': filename, 'inline': False}

    @staticmethod
    def sample_stats(snapshot):
        samples = snapshot.get('sample', [])
        outcomes = [item.get('outcome') for item in samples]
        passed = sum(value in {'pass', 'passed', 'approved'} for value in outcomes)
        reviewed = sum(value in {'pass', 'passed', 'approved', 'fail', 'failed', 'rejected', 'issue'} for value in outcomes)
        return {'passed': passed, 'reviewed': reviewed, 'sampled': len(samples), 'total_chapters': len(snapshot['chapters']),
                'rate': passed / reviewed if reviewed else None, 'label': '仅抽检样本通过率；不能视为整批准确率'}

    def chunk_markdown(self, text, size):
        """Respect paragraphs and image tokens, with labelled table fallbacks."""
        units = []
        def split_long(value, strategy, warnings):
            for token in re.split(r'(!\[[^\]]*\]\([^)]*\))', value):
                if not token:
                    continue
                if token.startswith('!['):
                    if len(token) > size:
                        raise self.Error('单个 Markdown 图片链接超过知识块上限，请提高 chunk_size。', 409)
                    units.append((token, strategy, warnings))
                else:
                    for start in range(0, len(token), size):
                        units.append((token[start:start + size], strategy, warnings))
        for block in re.split(r'\n\s*\n', text):
            if not block.strip():
                continue
            if len(block) <= size:
                units.append((block, 'paragraph', []))
                continue
            lines = block.splitlines()
            table = len(lines) >= 2 and all(line.strip().startswith('|') for line in lines)
            if table:
                header = lines[0] + '\n' + lines[1] + '\n'
                for line in lines[2:]:
                    if len(header + line) <= size:
                        units.append((header + line, 'table_row_with_header', ['超长表格按行拆分并重复表头。']))
                    else:
                        plain = '表格行：' + '；'.join(cell.strip() for cell in line.strip('|').split('|'))
                        split_long(plain, 'oversized_table_text_fallback', ['单行表格超过块上限，转为纯文本分段；原表保留于章节 Markdown。'])
                if len(lines) <= 2:
                    split_long(block, 'oversized_table_text_fallback', ['表头超过块上限，原表保留于章节 Markdown。'])
                continue
            # Images are indivisible; long prose is split at sentence/newline
            # boundaries, then at the cap only when one sentence exceeds it.
            for token in re.split(r'(!\[[^\]]*\]\([^)]*\))', block):
                if not token:
                    continue
                if token.startswith('!['):
                    if len(token) > size:
                        raise self.Error('单个 Markdown 图片链接超过知识块上限，请提高 chunk_size。', 409)
                    units.append((token, 'image_token', []))
                else:
                    for sentence in re.split(r'(?<=[。！？!?\n])', token):
                        for start in range(0, len(sentence), size):
                            if sentence[start:start + size].strip():
                                units.append((sentence[start:start + size], 'sentence_or_character_cap', []))
        chunks, current, strategies, warnings = [], '', set(), set()
        def flush():
            if current.strip():
                chunks.append({'text': current, 'chunk_strategy': sorted(strategies), 'warnings': sorted(warnings)})
        for unit, strategy, warning in units:
            combined = current + ('\n\n' if current else '') + unit
            if current and len(combined) > size:
                flush()
                current, strategies, warnings = '', set(), set()
            current += ('\n\n' if current else '') + unit
            strategies.add(strategy)
            warnings.update(warning)
        flush()
        return chunks

    def create_package(self, body, actor):
        self.require(actor)
        kind, minimum = body.get('type', 'pretrain'), body.get('minimum_role', 'internal')
        name = body.get('name') or '批次数据包'
        if not isinstance(kind, str) or kind not in {'pretrain', 'sft', 'preference', 'image', 'kb'} or not isinstance(minimum, str) or minimum not in ROLES:
            raise self.Error('数据包类型或最低角色无效。')
        if ROLES[minimum] > ROLES[actor['role']]:
            raise self.Error('不能创建高于当前账号角色的数据包。', 403)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise self.Error('数据包名称须为 1–160 字符。')
        size = body.get('chunk_size', 500)
        if not isinstance(size, int) or isinstance(size, bool) or not 100 <= size <= 4000:
            raise self.Error('知识块大小须为 100–4000 字符。')
        with self.lock:
            if not isinstance(body.get('batch_id'), str):
                raise self.Error('请指定已验收的处理批次。')
            snapshot = self.core.redact(self.core.accepted_batch_snapshot(body.get('batch_id')), {'role': 'internal'})
            document_ids = snapshot['document_ids']
            details = {document_id: self.core.redact(self.chapters.detail(document_id), {'role': 'internal'}) for document_id in document_ids}
            purpose = 'kb' if kind == 'kb' else 'training'
            for detail in details.values():
                doc = detail['document']
                if doc['license'] not in ({'self_owned', 'research_only'} if kind == 'kb' else {'self_owned'}) or doc['purpose'] not in {purpose, 'both'}:
                    raise self.Error('批次含用途或授权不符的文献；请先处理授权登记后重新生成。', 409)
            package_id, created_at = uid('package'), now()
            records, files, image_count = [], PackageFiles(self.Error), 0
            split_map = self.workshop.split_map(document_ids)
            for document_id, detail in details.items():
                doc_root = 'documents/' + document_id + '/'
                images = self.chapters.page_images(detail['pages'])
                def offline(text, prefix=''):
                    if isinstance(text, dict):
                        return {key: offline(value, prefix) for key, value in text.items()}
                    if isinstance(text, list):
                        return [offline(value, prefix) for value in text]
                    if not isinstance(text, str):
                        return text
                    for image_id, image in images.items():
                        text = text.replace(image['url'], prefix + 'images/' + image_id + PurePosixPath(image['path']).suffix.lower())
                    return text
                for image_id, image in images.items():
                    path, _ = self.chapters.image_file(document_id, image_id)
                    files[doc_root + 'images/' + image_id + PurePosixPath(image['path']).suffix.lower()] = path.read_bytes()
                    image_count += 1
                files[doc_root + 'source.json'] = js(detail['document']).encode()
                page_contexts = {}
                for page in detail['pages']:
                    def page_image(match):
                        target = self.chapters.resolve_image(page['path'], match[2], {image['path'] for image in images.values()})
                        image = next((image for image in images.values() if image['path'] == target), None)
                        return '![' + match[1] + '](../images/' + image['id'] + PurePosixPath(image['path']).suffix.lower() + ')' if image else match[0]
                    page_text = re.sub(r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)', page_image, page['text'])
                    files[doc_root + 'pages/page_' + str(page['number']) + '.md'] = offline(page_text, '../').encode()
                    page_contexts[page['number']] = offline(page_text, doc_root).replace('../images/', doc_root + 'images/')
                doc_records = []
                for chapter in (c for c in snapshot['chapters'] if c['document_id'] == document_id):
                    result, corpus = chapter['result'], chapter['corpus']
                    source_pages = [p['number'] for p in detail['pages'] if p['id'] in chapter['page_ids']]
                    metadata = {'batch_id': snapshot['id'], 'document_id': document_id, 'source_document_id': document_id,
                                'chapter_id': chapter['chapter_id'], 'revision': chapter['revision'], 'source_pages': source_pages,
                                'source_title': detail['document']['title'], 'is_demo': bool(result.get('is_demo')),
                                'processing_origin': result.get('processing_origin') or ('chapter_demo' if result.get('is_demo') else 'chapter_model'),
                                'model': result.get('model'), 'license': detail['document']['license'],
                                'source_metadata': detail['document']['source_metadata']}
                    rendered = offline(corpus, '../')
                    document_markdown = rendered
                    interpretation = result.get('interpretation', {})
                    if interpretation.get('text'):
                        document_markdown += '\n\n---\n\n## 章节解读（派生解释，非原文事实）\n\n' + offline(interpretation['text'], '../') + '\n\n[解读来源页：' + ', '.join(map(str, interpretation.get('source_pages', []))) + ']\n'
                    source = detail['document']['source_metadata']
                    if source:
                        document_markdown += '\n\n## 文献来源与许可\n\n' + '\n\n'.join(str(source[key]) for key in ('citation', 'url', 'license_url', 'license_notice', 'processing_note') if source.get(key)) + '\n'
                    files[doc_root + 'chapters/' + chapter['chapter_id'] + '.md'] = document_markdown.encode()
                    files[doc_root + 'chapters/' + chapter['chapter_id'] + '.json'] = js({'corpus': rendered, 'result': offline(result, '../'), 'metadata': metadata}).encode()
                    generated = []
                    if kind == 'pretrain':
                        generated = [{'text': offline(corpus, doc_root)}]
                    elif kind == 'kb':
                        text = offline(corpus, doc_root)
                        generated = [dict(chunk, id=uid('chunk')) for chunk in self.chunk_markdown(text, size)]
                    elif kind == 'sft':
                        for sample in result.get('sft_samples', []):
                            if not all(isinstance(sample.get(key), str) and sample[key].strip() for key in ('question', 'answer')):
                                continue
                            sample_pages = sample.get('source_pages', source_pages)
                            context = '\n\n'.join('[原始页 ' + str(number) + ']\n' + page_contexts[number] for number in sample_pages if number in page_contexts)
                            context += '\n\n已验收批次快照中的图表与事实文字：\n' + offline(corpus, doc_root)
                            generated.append({'messages': [
                                {'role': 'system', 'content': '只根据给定原始页和批次快照中的图表文字作答，说明缺失信息和来源页。'},
                                {'role': 'user', 'content': '原始资料：\n' + context + '\n\n问题：' + sample['question']},
                                {'role': 'assistant', 'content': sample['answer'] + ' [来源页：' + ', '.join(map(str, sample_pages)) + ']'}],
                                'sample_source_pages': sample_pages})
                    elif kind == 'preference':
                        for sample in result.get('preference_samples', []):
                            if all(isinstance(sample.get(key), str) and sample[key].strip() for key in ('prompt', 'chosen', 'rejected')) and sample['chosen'] != sample['rejected']:
                                generated.append({key: sample[key] for key in ('prompt', 'chosen', 'rejected')})
                    elif kind == 'image':
                        for figure in result.get('figures', []):
                            image_id = figure.get('image_id')
                            if image_id in images and isinstance(figure.get('caption'), str) and figure['caption'].strip():
                                generated.append({'image': doc_root + 'images/' + image_id + PurePosixPath(images[image_id]['path']).suffix.lower(), 'text': figure['caption'], 'sample_source_pages': figure.get('source_pages', source_pages)})
                    for record in generated:
                        record['metadata'] = dict(metadata, source_pages=record.pop('sample_source_pages', source_pages))
                        if kind != 'kb':
                            record['split'] = split_map[document_id]
                        records.append(record)
                        doc_records.append(record)
                def relative_record(value):
                    if isinstance(value, dict):
                        return {key: relative_record(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [relative_record(item) for item in value]
                    return value.replace(doc_root + 'images/', 'images/') if isinstance(value, str) else value
                files[doc_root + 'records.jsonl'] = ('\n'.join(js(relative_record(record)) for record in doc_records) + ('\n' if doc_records else '')).encode()
            if not records:
                raise self.Error('此批次没有对应类型的已审核样本；系统不会虚构 SFT、偏好或图文标注。', 409)
            source_documents = [{key: detail['document'][key] for key in ('id', 'title', 'page_count', 'image_count', 'created_at', 'license', 'is_demo')} for detail in details.values()]
            processed_days = {}
            with self.db() as db:
                for document_id in document_ids:
                    timestamps = []
                    for chapter in (c for c in snapshot['chapters'] if c['document_id'] == document_id):
                        event = db.execute("SELECT created_at FROM cp_history WHERE chapter_id=? AND revision=? AND event IN ('generated','manual_revised','prepared_example') ORDER BY created_at DESC,rowid DESC LIMIT 1", (chapter['chapter_id'], chapter['revision'])).fetchone()
                        if event:
                            timestamps.append(event['created_at'])
                    if timestamps:
                        day = max(timestamps)[:10]
                        processed_days.setdefault(day, set()).add(document_id)
            metadata = {'audit_status': 'accepted', 'revision_eligible': True, 'revision_eligibility_basis': 'creation_snapshot', 'source_document_count': len(details),
                        'record_count': len(records), 'chunk_count': len(records) if kind == 'kb' else 0, 'image_count': image_count,
                        'sample_pass_rate': self.sample_stats(snapshot),
                        'processed_by_day': [{'date': day, 'source_document_count': len(ids)} for day, ids in sorted(processed_days.items())],
                        'processing_date_basis': '各文献所含当前章节修订的最近生成/人工修改事件',
                        'publication_day': created_at[:10], 'chunk_strategy': 'paragraph_heading_image_preserving_with_explicit_table_fallback' if kind == 'kb' else None,
                        'split_policy': 'source_document_90_5_5', 'snapshot': True}
            if kind == 'kb':
                files['chunks.jsonl'] = ('\n'.join(js(record) for record in records) + '\n').encode()
            else:
                for split in ('train', 'validation', 'test'):
                    selected = [{key: value for key, value in record.items() if key != 'split'} for record in records if record['split'] == split]
                    files[split + '.jsonl'] = ('\n'.join(js(record) for record in selected) + ('\n' if selected else '')).encode()
            files['review_snapshot.json'] = js(snapshot).encode()
            metadata['file_count'] = len(files) + 1
            manifest = {'id': package_id, 'name': name.strip(), 'type': kind, 'batch_id': snapshot['id'], 'created_at': created_at,
                        'metadata': metadata, 'source_documents': source_documents,
                        'note': '包内容与审核快照不可变；抽检通过率只描述已审核样本，不代表整批准确率。'}
            files['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path, data in files.items():
                    archive.writestr(path, data)
            archive_bytes = output.getvalue()
            path = self.root / (package_id + '.zip')
            path.write_bytes(archive_bytes)
            payload = {'metadata': metadata, 'source_documents': source_documents, 'previews': records[:8], 'records': records,
                       'files': [{'path': key, 'size': len(value)} for key, value in files.items()], 'snapshot': snapshot}
            try:
                with self.db() as db:
                    db.execute('INSERT INTO pd_packages VALUES(?,?,?,?,?,?,?,?,?,?,?)', (package_id, name.strip(), kind, snapshot['id'], 0, minimum, created_at,
                               path.relative_to(self.workshop.root).as_posix(), hashlib.sha256(archive_bytes).hexdigest(), js(payload), None))
            except Exception:
                path.unlink(missing_ok=True)
                raise
        self.audit(actor, 'package_created', {'package_id': package_id, 'type': kind, 'count': len(records)}, batch_id=snapshot['id'])
        return self.package_detail(package_id, actor)

    def search_packages(self, body, actor):
        self.require(actor, 'external')
        query = self.query(body)
        terms, results = self.workshop.terms(query), []
        with self.db() as db:
            if body.get('package_id'):
                if not isinstance(body['package_id'], str):
                    raise self.Error('数据包 ID 无效。')
                rows = [self.package_row(db, body['package_id'], actor)]
            else:
                rows = [row for row in db.execute("SELECT * FROM pd_packages WHERE type='kb'") if self.allowed(row, actor)]
            for row in rows:
                if row['type'] != 'kb':
                    continue
                for record in json.loads(row['payload'])['records']:
                    text = record.get('text', '')
                    score = len(terms & self.workshop.terms(text))
                    if score:
                        metadata = record['metadata']
                        results.append({'text': text, 'score': score, 'package_id': row['id'], 'package_name': row['name'],
                                        'document_id': metadata['document_id'], 'chapter_id': metadata['chapter_id'],
                                        'source_pages': metadata['source_pages'], 'provenance': metadata})
        results.sort(key=lambda item: item['score'], reverse=True)
        return {'results': results[:50], 'query': query, 'retrieval': 'keyword', 'note': '关键词检索结果，不生成答案。'}

    def query(self, body):
        query = body.get('query')
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
            raise self.Error('请输入 1–300 字符检索词。')
        return query.strip()

    def search(self, body, actor):
        self.require(actor)
        query = self.query(body)
        terms, results = self.workshop.terms(query), []
        scope = body.get('scope', 'all')
        if not isinstance(scope, str) or scope not in {'all', 'literature', 'pages', 'chapters', 'markdown'}:
            raise self.Error('检索范围无效。')
        with self.lock, self.db() as db:
            package_ids = {}
            for package in db.execute('SELECT * FROM pd_packages'):
                if self.allowed(package, actor):
                    for document in json.loads(package['payload'])['source_documents']:
                        package_ids.setdefault(document['id'], []).append(package['id'])
            for row in db.execute('SELECT * FROM cp_documents'):
                document = self.chapters.document(db, row['id'])
                provenance = {'source_document_id': row['id'], 'source_metadata': document['source_metadata'], 'library_contexts': document['library_contexts'], 'package_ids': package_ids.get(row['id'], [])}
                if scope in {'all', 'literature'}:
                    results.append({'kind': 'literature', 'id': row['id'], 'document_id': row['id'], 'title': row['title'], 'text': row['title'], 'provenance': provenance})
                if scope in {'all', 'pages'}:
                    for page in db.execute('SELECT * FROM cp_pages WHERE document_id=?', (row['id'],)):
                        results.append({'kind': 'page', 'id': page['id'], 'document_id': row['id'], 'page': page['number'], 'title': row['title'], 'text': page['text'], 'provenance': dict(provenance, source_path=page['path'])})
                if scope in {'all', 'chapters'}:
                    for chapter in db.execute("SELECT * FROM cp_chapters WHERE document_id=? AND active=1 AND corpus!=''", (row['id'],)):
                        results.append({'kind': 'chapter', 'id': chapter['id'], 'document_id': row['id'], 'chapter_id': chapter['id'], 'title': chapter['title'], 'text': chapter['corpus'], 'status': chapter['status'], 'revision': chapter['revision'], 'provenance': provenance})
            if scope in {'all', 'markdown'}:
                for asset in db.execute("SELECT * FROM assets WHERE kind IN ('md','markdown','txt')"):
                    text = asset['raw_text']
                    if not text:
                        try:
                            with self.workshop.local_path(asset['path']).open('rb') as source:
                                text = source.read(2 * 1024 * 1024).decode('utf-8-sig')
                        except (OSError, UnicodeError):
                            text = ''
                    links = [dict(link) for link in db.execute('SELECT * FROM pd_asset_links WHERE asset_id=?', (asset['id'],))]
                    provenance = json.loads(asset['provenance'] or '{}') | {'asset_id': asset['id'], 'links': links,
                                 'package_ids': sorted({pid for link in links for pid in package_ids.get(link['document_id'], [])})}
                    results.append({'kind': 'markdown', 'id': asset['id'], 'asset_id': asset['id'], 'title': asset['name'], 'text': text, 'provenance': provenance})
        found = []
        for result in results:
            result['package_ids'] = result['provenance'].get('package_ids', [])
            score = len(terms & self.workshop.terms(result['text']))
            if score:
                result['score'] = score
                position = result['text'].casefold().find(query.casefold())
                start = max(0, position - 100)
                result['text'] = result['text'][start:start + 1200]
                found.append(result)
        found.sort(key=lambda item: item['score'], reverse=True)
        return {'results': found[:100], 'query': query, 'retrieval': 'keyword', 'total': len(found)}

    def delete_asset(self, asset_id, actor):
        """删除一条资料资产，连带关联的 blocks/reviews/chunks 和磁盘文件。"""
        with self.lock, self.db() as db:
            row = db.execute('SELECT id,name,path FROM assets WHERE id=?', (asset_id,)).fetchone()
            if not row:
                raise self.Error('资料不存在。', 404)
            # 清除所有依赖关系（外键表按依赖层级从子到父删除）
            db.execute('DELETE FROM chunks WHERE asset_id=?', (asset_id,))
            db.execute('DELETE FROM reviews WHERE asset_id=?', (asset_id,))
            db.execute('DELETE FROM blocks WHERE asset_id=?', (asset_id,))
            db.execute('DELETE FROM assets WHERE id=?', (asset_id,))
            # 删除磁盘文件（仅 data/raw 目录下的原始文件；导出包在 versions 表不跟着删）
            try:
                p = self.root / row['path']
                if p.exists() and str(p.resolve()).startswith(str((self.root / 'raw').resolve())):
                    p.unlink()
            except Exception:
                pass
            self.audit(actor, 'delete_asset', {'name': row['name'], 'asset_id': asset_id}, db=db)
            return {'deleted': asset_id, 'name': row['name']}

    def dispatch(self, method, path, body, actor):
        if path == '/api/data-hub' and method == 'GET':
            return self.data_hub(actor)
        # 删除资料资产（连带 blocks/reviews/chunks 和磁盘文件）
        match = re.fullmatch(r'/api/data-hub/assets/(asset_[a-f0-9]+)', path)
        if match and method == 'DELETE':
            return self.delete_asset(match[1], actor)
        if path in {'/api/data-links', '/api/data-hub/link'} and method == 'POST':
            return self.link_asset(body, actor)
        if path == '/api/data-hub/ingest' and method == 'POST':
            return self.ingest_asset(body, actor)
        if path == '/api/search' and method == 'POST':
            return self.search(body, actor)
        if path == '/api/ocr/config':
            return {'config': self.ocr_config(actor)} if method == 'GET' else self.configure_ocr(body, actor)
        if path == '/api/ocr/tasks':
            return self.ocr_tasks(actor) if method == 'GET' else self.create_ocr(body, actor)
        match = re.fullmatch(r'/api/ocr/tasks/(ocr_[a-f0-9]+)', path)
        if match and method == 'GET':
            return self.ocr_tasks(actor, match[1])
        if path == '/api/packages/search' and method == 'POST':
            return self.search_packages(body, actor)
        if path == '/api/packages':
            return self.packages(actor) if method == 'GET' else self.create_package(body, actor)
        match = re.fullmatch(r'/api/packages/(package_[a-f0-9]+)(?:/(publish|download))?', path)
        if match:
            if match[2] == 'publish' and method == 'POST':
                return self.publish(match[1], body, actor)
            if match[2] == 'download' and method == 'GET':
                return self.download(match[1], actor)
            if match[2] is None and method == 'GET':
                return self.package_detail(match[1], actor)
        raise self.Error('平台数据接口不存在。', 404)

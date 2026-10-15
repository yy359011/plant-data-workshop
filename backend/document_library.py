"""Local source -> record -> publication catalogue; never fetches remote URLs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
from urllib.parse import urlsplit
import uuid


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class DocumentLibrary:
    BUILTINS = {
        'pothos': ('source_open_plants', '开放植物文献', '植物分类学论文与图版资料，保留原论文出处、作者和许可。', 'record_pothos_deleonii', 'Pothos deleonii', 'Pothos deleonii'),
        'botanical': ('source_plant_research', '植物研究资料', '植物实验记录、图表及研究资料。', 'record_arabidopsis', '拟南芥', 'Arabidopsis thaliana'),
        'local': ('source_local_import', '本地导入', '本地文献与逐页资料包，可按研究对象归类。', 'record_unclassified', '待归类', ''),
    }

    def __init__(self, workshop, chapters, error_class):
        self.workshop, self.chapters, self.Error = workshop, chapters, error_class
        self.lock = workshop.lock
        self.init_db()

    def db(self):
        return self.workshop.connect()

    def init_db(self):
        with self.lock, self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS dl_sources (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,
              kind TEXT NOT NULL,endpoint TEXT NOT NULL,status TEXT NOT NULL,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dl_records (
              id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES dl_sources(id),
              name TEXT NOT NULL,scientific_name TEXT NOT NULL,summary TEXT NOT NULL,
              metadata TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dl_links (
              record_id TEXT NOT NULL REFERENCES dl_records(id),
              document_id TEXT NOT NULL REFERENCES cp_documents(id),created_at TEXT NOT NULL,
              PRIMARY KEY(record_id,document_id));
            CREATE INDEX IF NOT EXISTS dl_records_source ON dl_records(source_id);
            CREATE INDEX IF NOT EXISTS dl_links_document ON dl_links(document_id);
            ''')
            self.ensure_builtins(db)
            # Only previously unclassified publications receive a default link.
            # Existing links, source names and all publication contents stay intact.
            rows = db.execute('SELECT id,is_demo,source_metadata FROM cp_documents WHERE NOT EXISTS (SELECT 1 FROM dl_links WHERE document_id=cp_documents.id)').fetchall()
            for row in rows:
                source_metadata = json.loads(row['source_metadata'])
                category = 'pothos' if str(source_metadata.get('doi', '')).lower().removeprefix('https://doi.org/').removeprefix('doi:').strip() == '10.3897/phytokeys.247.130721' else 'botanical' if row['is_demo'] else 'local'
                self.default_link(db, row['id'], category)
            if rows:
                self.workshop.log(db, 'library_migration', f'将 {len(rows)} 篇已有文献登记到文献目录；原始内容、加工修订与已有归属保持不变。')

    def ensure_builtins(self, db):
        stamp = now()
        for source_id, name, description, record_id, record_name, scientific_name in self.BUILTINS.values():
            db.execute('INSERT OR IGNORE INTO dl_sources VALUES(?,?,?,?,?,?,?,?)', (source_id, name, description, 'local', '', 'ready', stamp, stamp))
            db.execute('INSERT OR IGNORE INTO dl_records VALUES(?,?,?,?,?,?,?,?)', (record_id, source_id, record_name, scientific_name, '', '{}', stamp, stamp))

    def valid_id(self, value, kind):
        if not isinstance(value, str) or not re.fullmatch(kind + r'_[a-z0-9_]{1,80}', value):
            raise self.Error('数据源或条目 ID 无效。')
        return value

    def source(self, db, source_id):
        self.valid_id(source_id, 'source')
        row = db.execute('SELECT * FROM dl_sources WHERE id=?', (source_id,)).fetchone()
        if not row:
            raise self.Error('数据源不存在。', 404)
        result = dict(row)
        result['record_count'] = db.execute('SELECT COUNT(*) FROM dl_records WHERE source_id=?', (source_id,)).fetchone()[0]
        result['document_count'] = db.execute('SELECT COUNT(DISTINCT l.document_id) FROM dl_links l JOIN dl_records r ON r.id=l.record_id WHERE r.source_id=?', (source_id,)).fetchone()[0]
        return result

    def record(self, db, record_id):
        self.valid_id(record_id, 'record')
        row = db.execute('SELECT * FROM dl_records WHERE id=?', (record_id,)).fetchone()
        if not row:
            raise self.Error('物种或数据条目不存在。', 404)
        result = dict(row)
        result['metadata'] = json.loads(result['metadata'])
        result['document_count'] = db.execute('SELECT COUNT(*) FROM dl_links WHERE record_id=?', (record_id,)).fetchone()[0]
        return result

    def contexts(self, db, document_id):
        result = []
        for row in db.execute('SELECT r.id,r.source_id FROM dl_links l JOIN dl_records r ON r.id=l.record_id WHERE l.document_id=? ORDER BY l.created_at,l.rowid', (document_id,)).fetchall():
            result.append({'source': self.source(db, row['source_id']), 'record': self.record(db, row['id']), 'source_id': row['source_id'], 'record_id': row['id']})
        return result

    def state(self):
        with self.lock, self.db() as db:
            sources = [self.source(db, row['id']) for row in db.execute('SELECT id FROM dl_sources ORDER BY created_at,rowid').fetchall()]
            records = [self.record(db, row['id']) for row in db.execute('SELECT id FROM dl_records ORDER BY created_at,rowid').fetchall()]
            links = [dict(row) for row in db.execute('SELECT l.*,r.source_id FROM dl_links l JOIN dl_records r ON r.id=l.record_id ORDER BY l.created_at,l.rowid')]
            documents = [self.chapters.document(db, row['id']) for row in db.execute('SELECT id FROM cp_documents ORDER BY created_at DESC,rowid DESC').fetchall()]
        return {'sources': sources, 'records': records, 'links': links, 'documents': documents}

    def record_detail(self, record_id):
        with self.lock, self.db() as db:
            record = self.record(db, record_id)
            source = self.source(db, record['source_id'])
            documents = [self.chapters.document(db, row['document_id']) for row in db.execute('SELECT document_id FROM dl_links WHERE record_id=? ORDER BY created_at,rowid', (record_id,)).fetchall()]
        return {'record': record, 'source': source, 'documents': documents}

    def text(self, value, label, limit, required=False):
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise self.Error(label + '格式无效或过长。')
        value = value.strip()
        if required and not value:
            raise self.Error(label + '不能为空。')
        return value

    def source_values(self, body, old=None):
        old = old or {'description': '', 'endpoint': ''}
        name = self.text(body.get('name', old.get('name')), '数据源名称', 200, True)
        description = self.text(body.get('description', old['description']), '数据源说明', 4000)
        kind = body.get('kind', old.get('kind', 'local'))
        if not isinstance(kind, str) or kind not in {'local', 'api'}:
            raise self.Error('数据源类型须为 local 或 api。')
        endpoint_value = body.get('endpoint', old['endpoint'])
        endpoint = self.text('' if endpoint_value is None else endpoint_value, '接口地址', 1000)
        if endpoint:
            parsed = urlsplit(endpoint)
            if kind != 'api' or parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise self.Error('待接入接口须填写 HTTP(S) 地址，不能包含账号、密钥或查询参数；本地数据源无需接口地址。')
        status = 'pending' if kind == 'api' else 'ready'
        if 'status' in body and body['status'] != status:
            raise self.Error('接口数据源当前仅保存待接入配置，不能登记为已连接。')
        return name, description, kind, endpoint, status

    def create_source(self, body):
        values, source_id, stamp = self.source_values(body), 'source_' + uuid.uuid4().hex[:16], now()
        with self.lock, self.db() as db:
            db.execute('INSERT INTO dl_sources VALUES(?,?,?,?,?,?,?,?)', (source_id, *values, stamp, stamp))
            self.workshop.log(db, 'library_source', '创建数据源：' + values[0] + ('；接口待接入，未执行网络请求。' if values[2] == 'api' else '。'))
            return {'source': self.source(db, source_id)}

    def update_source(self, source_id, body):
        with self.lock, self.db() as db:
            old = self.source(db, source_id)
            values = self.source_values(body, old)
            db.execute('UPDATE dl_sources SET name=?,description=?,kind=?,endpoint=?,status=?,updated_at=? WHERE id=?', (*values, now(), source_id))
            self.workshop.log(db, 'library_source', '更新数据源登记：' + values[0] + '；未执行远程连接。')
            return {'source': self.source(db, source_id)}

    def metadata(self, value):
        if not isinstance(value, dict):
            raise self.Error('条目 metadata 须为 JSON 对象。')
        def valid(item, depth=0):
            if depth > 10:
                return False
            if item is None or isinstance(item, (str, bool, int)):
                return True
            if isinstance(item, float):
                return math.isfinite(item)
            if isinstance(item, list):
                return all(valid(v, depth + 1) for v in item)
            if isinstance(item, dict):
                return all(isinstance(k, str) and valid(v, depth + 1) for k, v in item.items())
            return False
        if not valid(value):
            raise self.Error('条目 metadata 包含非法或过深的 JSON 值。')
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > 32 * 1024:
            raise self.Error('条目 metadata 不能超过 32 KiB。', 413)
        return encoded

    def record_values(self, body, old=None):
        old = old or {'scientific_name': '', 'summary': '', 'metadata': {}}
        return (self.text(body.get('name', old.get('name')), '条目名称', 200, True),
                self.text(body.get('scientific_name', old['scientific_name']), '学名', 300),
                self.text(body.get('summary', old['summary']), '条目概述', 4000),
                self.metadata(body.get('metadata', old['metadata'])))

    def create_record(self, body):
        values, record_id, stamp = self.record_values(body), 'record_' + uuid.uuid4().hex[:16], now()
        with self.lock, self.db() as db:
            source_id = self.source(db, body.get('source_id'))['id']
            db.execute('INSERT INTO dl_records VALUES(?,?,?,?,?,?,?,?)', (record_id, source_id, *values, stamp, stamp))
            self.workshop.log(db, 'library_record', '创建物种或数据条目：' + values[0] + '。')
            return {'record': self.record(db, record_id)}

    def update_record(self, record_id, body):
        with self.lock, self.db() as db:
            old = self.record(db, record_id)
            if 'source_id' in body and body['source_id'] != old['source_id']:
                raise self.Error('条目不能直接更换数据源；请在目标源新建条目后关联文献。', 409)
            values = self.record_values(body, old)
            db.execute('UPDATE dl_records SET name=?,scientific_name=?,summary=?,metadata=?,updated_at=? WHERE id=?', (*values, now(), record_id))
            self.workshop.log(db, 'library_record', '更新条目登记：' + values[0] + '。')
            return {'record': self.record(db, record_id)}

    def validate_target(self, db, body):
        if 'record_id' not in body:
            if 'source_id' in body:
                raise self.Error('选择数据源后还需选择物种或数据条目。')
            return None
        record = self.record(db, body['record_id'])
        if 'source_id' in body and body['source_id'] != record['source_id']:
            raise self.Error('条目不属于所选数据源。', 409)
        return record

    def link_in_db(self, db, record_id, document_id):
        record = self.record(db, record_id)
        self.chapters.document(db, document_id)
        created = db.execute('INSERT OR IGNORE INTO dl_links VALUES(?,?,?)', (record_id, document_id, now())).rowcount == 1
        row = db.execute('SELECT * FROM dl_links WHERE record_id=? AND document_id=?', (record_id, document_id)).fetchone()
        return dict(row, source_id=record['source_id']), created

    def default_link(self, db, document_id, category='local'):
        record_id = self.BUILTINS[category][3]
        return self.link_in_db(db, record_id, document_id)

    def link(self, body, remove=False):
        with self.lock, self.db() as db:
            record = self.validate_target(db, body)
            if record is None:
                raise self.Error('请选择物种或数据条目。')
            document_id = body.get('document_id')
            self.chapters.document(db, document_id)
            if remove:
                db.execute('DELETE FROM dl_links WHERE record_id=? AND document_id=?', (record['id'], document_id))
                self.workshop.log(db, 'library_unlink', '移除一项文献归属；文献原件与加工记录保留。')
                return {'document': self.chapters.document(db, document_id)}
            link, created = self.link_in_db(db, record['id'], document_id)
            if created:
                self.workshop.log(db, 'library_link', '文献关联到条目：' + record['name'] + '；原始文献不重复复制。')
            return {'link': link, 'duplicate': not created, 'document': self.chapters.document(db, document_id)}

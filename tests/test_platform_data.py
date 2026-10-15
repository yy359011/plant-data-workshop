"""Real local HTTP contract tests for OCR, package ACLs and batch provenance."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import posixpath
import re
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

import test_chapter_pipeline as pipeline


class OCRHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append({'body': body, 'headers': dict(self.headers)})
        self.server.entered.set()
        self.server.release.wait(5)
        response = json.dumps(self.server.reply, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class PlatformDataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='plant-platform-data-')
        self.server = pipeline.SERVER.create_server(host='127.0.0.1', port=0, data_dir=self.directory.name, seed=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.core, self.data = self.server.platform, self.server.platform_data
        first = self.core.setup({'username': 'admin_test', 'display_name': '测试管理员', 'password': 'only-for-local-tests-92'})
        self.actors, self.cookies = {'admin': first['user']}, {'admin': first['_cookie'].split(';')[0]}
        for role in ('internal', 'external'):
            created = self.core.create_user({'username': role + '_test', 'display_name': role, 'role': role, 'password': 'only-for-local-tests-93'}, self.actors['admin'])
            login = self.core.login({'username': role + '_test', 'password': 'only-for-local-tests-93'})
            self.actors[role], self.cookies[role] = login['user'], login['_cookie'].split(';')[0]
        self.ocr = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        if self.ocr:
            self.ocr.release.set()
            self.ocr.shutdown()
            self.ocr.server_close()
            self.ocr_thread.join(3)
        self.directory.cleanup()

    def request(self, method, path, payload=None, role='admin'):
        headers = {'Content-Type': 'application/json'}
        if role:
            headers['Cookie'] = self.cookies[role]
        request = Request(self.base + path, data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(), method=method, headers=headers)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            data = response.read()
            return response.status, json.loads(data) if 'application/json' in response.headers.get('Content-Type', '') else data

    def ok(self, method, path, payload=None, role='admin'):
        status, result = self.request(method, path, payload, role)
        self.assertTrue(200 <= status < 300, (status, result))
        return result

    def fail_request(self, method, path, payload=None, role='admin', status=None):
        code, result = self.request(method, path, payload, role)
        self.assertTrue(400 <= code < 500, (code, result))
        if status:
            self.assertEqual(code, status, result)
        return result

    def asset(self, name, text):
        raw = text.encode() if isinstance(text, str) else text
        return self.server.workshop.import_one(name, raw, 'both', 'self_owned')

    def fixture_document(self):
        files = []
        for number in range(1, 5):
            text = f'# 合成测试章节 {number}\n\n拟南芥叶片记录 {number}。BATCH_ONLY_MARKER。'
            if number == 1:
                text += '\n\n拟南芥幼苗合成演示设 8 h、12 h、16 h 光照；每组 3 份虚构样本，叶面积单位为 cm²。\n\n![测试色块](../images/leaf.png "源图标题")'
            files.append({'path': f'pages/page_{number}.md', 'content_base64': base64.b64encode(text.encode()).decode()})
        files.append({'path': 'images/leaf.png', 'content_base64': base64.b64encode(pipeline.PNG_BYTES).decode()})
        document = self.server.chapters.import_package({'name': '仅供回归的合成文献', 'files': files, 'purpose': 'both', 'license': 'self_owned'}, is_demo=True)['document']
        job = self.server.chapters.run({'document_id': document['id'], 'mode': 'demo'})['job']
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            latest = next(item for item in self.server.chapters.state()['jobs'] if item['id'] == job['id'])
            if latest['status'] in {'completed', 'error'}:
                self.assertEqual(latest['status'], 'completed', latest)
                # A persisted administrator prompt is realistic job metadata;
                # its body must never appear in packages for any role.
                with self.server.workshop.connect() as db:
                    for row in db.execute('SELECT id,result FROM cp_chapters WHERE document_id=?', (document['id'],)).fetchall():
                        result = json.loads(row['result'])
                        result['prompt_snapshot'] = {'id': 'prompt_fixture', 'name': '测试处理提示词', 'version': 1, 'type': 'literature',
                                                     'content': 'PRIVATE_PROMPT_MARKER', 'standards': [{'content': 'PRIVATE_STANDARD_MARKER'}]}
                        db.execute('UPDATE cp_chapters SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), row['id']))
                return self.server.chapters.detail(document['id'])
            time.sleep(.02)
        self.fail('Fixture chapter processing timed out')

    def accepted_batch(self):
        document = self.fixture_document()
        detail = self.core.create_batch({'name': '抽检包回归', 'document_ids': [document['document']['id']], 'sampling_rate': .25}, self.actors['admin'])
        batch_id = detail['batch']['id']
        self.fail_request('POST', '/api/packages', {'batch_id': batch_id, 'type': 'pretrain'}, status=409)
        sampled = self.core.sample_batch(batch_id, self.actors['admin'])
        for sample in sampled['samples']:
            self.core.review_sample(batch_id, {'chapter_id': sample['chapter_id'], 'expected_revision': sample['revision'], 'outcome': 'pass'}, self.actors['admin'])
        self.core.accept_batch(batch_id, self.actors['admin'])
        return document, batch_id, sampled

    def package(self, batch_id, kind='pretrain', minimum='internal'):
        return self.ok('POST', '/api/packages', {'batch_id': batch_id, 'type': kind, 'name': '测试包 ' + kind, 'minimum_role': minimum})['package']

    def start_ocr(self, reply, delayed=False):
        self.ocr = ThreadingHTTPServer(('127.0.0.1', 0), OCRHandler)
        self.ocr.reply, self.ocr.requests = reply, []
        self.ocr.entered, self.ocr.release = threading.Event(), threading.Event()
        if not delayed:
            self.ocr.release.set()
        self.ocr_thread = threading.Thread(target=self.ocr.serve_forever, daemon=True)
        self.ocr_thread.start()
        return f'http://127.0.0.1:{self.ocr.server_port}/ocr'

    def wait_ocr(self, task):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            latest = self.ok('GET', '/api/ocr/tasks/' + task['id'])['task']
            if latest['status'] in {'completed', 'error', 'blocked'}:
                return latest
            time.sleep(.02)
        self.fail('OCR task did not finish')

    def test_batch_scope_snapshot_and_sample_denominator_are_honest(self):
        unrelated = self.asset('未选择.md', 'UNRELATED_GLOBAL_MARKER')
        self.server.workshop.process([unrelated['id']])
        document, batch_id, sampled = self.accepted_batch()
        package = self.package(batch_id)
        hub = self.ok('GET', '/api/data-hub')
        images = [row for row in hub['rows'] if row['kind'] == 'image']
        self.assertEqual(hub['stats']['image'], 1)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['document_id'], document['document']['id'])
        self.assertEqual(images[0]['batch_ids'], [batch_id])
        self.assertEqual(images[0]['source_pages'], [1])
        self.assertEqual(images[0]['stage'], 'raw')
        self.assertEqual(images[0]['preview_url'], images[0]['url'])
        self.assertEqual(self.ok('GET', images[0]['preview_url']), pipeline.PNG_BYTES)
        search = self.ok('POST', '/api/search', {'query': 'BATCH_ONLY_MARKER'})['results']
        self.assertTrue(search)
        self.assertTrue(all(package['id'] in item['package_ids'] for item in search if item.get('document_id') == document['document']['id']))
        rate = package['metadata']['sample_pass_rate']
        self.assertEqual(rate['passed'], len(sampled['samples']))
        self.assertEqual(rate['reviewed'], len(sampled['samples']))
        self.assertLess(rate['sampled'], rate['total_chapters'])
        self.assertEqual(rate['rate'], 1)
        self.assertIn('仅抽检', rate['label'])
        self.assertEqual(package['metadata']['source_document_count'], 1)
        self.assertEqual(package['source_documents'][0]['id'], document['document']['id'])
        original = self.ok('GET', package['download_url'])
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            text = '\n'.join(archive.read(name).decode() for name in archive.namelist() if name.endswith(('.md', '.json', '.jsonl')))
            self.assertIn('BATCH_ONLY_MARKER', text)
            self.assertNotIn('UNRELATED_GLOBAL_MARKER', text)
            self.assertNotIn('PRIVATE_PROMPT_MARKER', text)
            self.assertNotIn('PRIVATE_STANDARD_MARKER', text)
            self.assertIn('prompt_fixture', text)
            snapshot = json.loads(archive.read('review_snapshot.json'))
            self.assertEqual(snapshot['id'], batch_id)
            self.assertFalse(snapshot['all_human_reviewed'])
            for name in archive.namelist():
                if name.endswith('.md'):
                    markdown = archive.read(name).decode()
                    for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', markdown):
                        self.assertIn(posixpath.normpath(posixpath.join(posixpath.dirname(name), target)), archive.namelist())
                elif name.endswith('/records.jsonl'):
                    for line in archive.read(name).decode().splitlines():
                        for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', json.loads(line)['text']):
                            self.assertIn(posixpath.normpath(posixpath.join(posixpath.dirname(name), target)), archive.namelist())
        sft = self.package(batch_id, 'sft')
        self.assertTrue(sft['previews'])
        self.assertTrue(all(item.get('messages') and item['metadata']['document_id'] == document['document']['id'] for item in sft['previews']))
        question = next(item['content'] for item in sft['previews'][0]['messages'] if item['role'] == 'user')
        self.assertIn('[原始页 1]', question)
        self.assertIn('BATCH_ONLY_MARKER', question)
        self.assertIn('批次快照中的图表与事实文字', question)
        self.assertIn('来源页', sft['previews'][0]['messages'][-1]['content'])
        image_package = self.package(batch_id, 'image')
        with zipfile.ZipFile(io.BytesIO(self.ok('GET', image_package['download_url']))) as archive:
            doc_records = f"documents/{document['document']['id']}/records.jsonl"
            image_record = json.loads(archive.read(doc_records).decode().splitlines()[0])
            self.assertTrue(image_record['image'].startswith('images/'))
            self.assertIn(posixpath.join(posixpath.dirname(doc_records), image_record['image']), archive.namelist())
        self.fail_request('POST', '/api/packages', {'batch_id': batch_id, 'type': 'preference'}, status=409)
        chapter = document['chapters'][0]
        self.server.chapters.revise({'chapter_id': chapter['id'], 'expected_revision': chapter['revision'], 'corpus': chapter['corpus'] + '\n\n新版本不应覆盖旧包。'})
        self.fail_request('POST', '/api/packages', {'batch_id': batch_id, 'type': 'pretrain'}, status=409)
        self.assertEqual(self.ok('GET', package['download_url']), original)
        self.assertEqual(self.ok('GET', '/api/packages/' + package['id'])['package']['review_snapshot'], package['review_snapshot'])

    def test_package_roles_and_published_flag_gate_list_detail_download_and_search(self):
        document, batch_id, _ = self.accepted_batch()
        package = self.package(batch_id, 'kb', 'external')
        path = '/api/packages/' + package['id']
        for old_path in ('/api/training/export', '/api/kb/publish', '/api/chapters/materialize'):
            self.fail_request('POST', old_path, {'document_id': document['document']['id']}, status=409)
        self.fail_request('GET', '/api/packages', role=None, status=401)
        self.assertEqual(self.ok('GET', '/api/packages', role='external')['packages'], [])
        for target in (path, package['download_url']):
            self.fail_request('GET', target, role='external')
        self.fail_request('POST', '/api/packages/search', {'query': '拟南芥', 'package_id': package['id']}, role='external')
        self.fail_request('POST', path + '/publish', {'published': True}, role='internal', status=403)
        self.ok('POST', path + '/publish', {'published': True})
        self.assertEqual(len(self.ok('GET', '/api/packages', role='external')['packages']), 1)
        self.assertEqual(self.ok('GET', path, role='external')['package']['id'], package['id'])
        self.assertTrue(self.ok('GET', package['download_url'], role='external').startswith(b'PK'))
        found = self.ok('POST', '/api/packages/search', {'query': '拟南芥'}, role='external')['results']
        self.assertTrue(found)
        self.assertTrue(all(item['document_id'] == document['document']['id'] and item['package_id'] == package['id'] for item in found))
        self.ok('POST', path + '/publish', {'minimum_role': 'admin'})
        for role in ('external', 'internal'):
            self.assertEqual(self.ok('GET', '/api/packages', role=role)['packages'], [])
            self.fail_request('GET', path, role=role)
            self.fail_request('GET', package['download_url'], role=role)
            self.assertEqual(self.ok('POST', '/api/packages/search', {'query': '拟南芥'}, role=role)['results'], [])

    def test_raw_markdown_ingest_and_global_search_preserve_asset_document_links(self):
        asset = self.asset('待处理原文.md', '# 叶形数据\n\nRAW_MD_SEARCH_MARKER 拟南芥叶长。')
        raw_result = self.ok('POST', '/api/search', {'query': 'RAW_MD_SEARCH_MARKER'})['results']
        self.assertTrue(any(item.get('asset_id') == asset['id'] for item in raw_result))
        self.fail_request('GET', '/api/data-hub', role='external', status=403)
        ingested = self.ok('POST', '/api/data-hub/ingest', {'asset_id': asset['id']})
        document_id = ingested['document']['id']
        repeated = self.ok('POST', '/api/data-hub/ingest', {'asset_id': asset['id']})
        self.assertEqual(repeated['document']['id'], document_id)
        hub = self.ok('GET', '/api/data-hub')
        asset_row = next(row for row in hub['rows'] if row['id'] == asset['id'])
        self.assertEqual(asset_row['document_ids'], [document_id])
        self.assertTrue(any(row['kind'] == 'page' and row['document_id'] == document_id for row in hub['rows']))
        search = self.ok('POST', '/api/search', {'query': 'RAW_MD_SEARCH_MARKER'})['results']
        self.assertTrue(any(item['kind'] == 'page' and item['provenance']['source_document_id'] == document_id for item in search))

    def test_unconfigured_ocr_is_persisted_blocked_without_creating_a_document(self):
        asset = self.asset('待识别.png', pipeline.PNG_BYTES)
        task = self.ok('POST', '/api/ocr/tasks', {'asset_id': asset['id'], 'record_id': 'record_unclassified', 'source_id': 'source_local_import'}, role='internal')['task']
        self.assertEqual(task['status'], 'blocked')
        self.assertEqual(task['record_id'], 'record_unclassified')
        self.assertEqual(task['source_id'], 'source_local_import')
        self.assertIn('未执行识别', task['error'])
        self.assertEqual(self.server.chapters.state()['documents'], [])
        self.assertEqual(self.ok('GET', '/api/ocr/tasks')['tasks'][0]['id'], task['id'])
        self.fail_request('POST', '/api/ocr/config', {'endpoint': ''}, role='internal', status=403)

    def test_configured_ocr_calls_only_mock_and_keeps_ui_pollable(self):
        asset = self.asset('真实传输测试.png', pipeline.PNG_BYTES)
        reply = {'pages': [{'page': 1, 'markdown': '# 叶片\n\nOCR_TRANSPORT_MARKER\n\n![测试](../images/leaf.png)'}],
                 'images': [{'path': 'images/leaf.png', 'content_base64': base64.b64encode(pipeline.PNG_BYTES).decode()}]}
        endpoint = self.start_ocr(reply, delayed=True)
        config = self.ok('POST', '/api/ocr/config', {'endpoint': endpoint, 'key': 'MOCK_OCR_SECRET'})
        self.assertNotIn('MOCK_OCR_SECRET', json.dumps(config))
        task = self.ok('POST', '/api/ocr/tasks', {'asset_id': asset['id']})['task']
        try:
            self.assertTrue(self.ocr.entered.wait(3))
            self.assertEqual(self.ok('GET', '/api/ocr/tasks/' + task['id'])['task']['status'], 'processing')
            self.assertTrue(self.ok('GET', '/api/data-hub')['rows'])
        finally:
            self.ocr.release.set()
        complete = self.wait_ocr(task)
        self.assertEqual(complete['status'], 'completed', complete)
        self.assertEqual(base64.b64decode(self.ocr.requests[0]['body']['content_base64']), pipeline.PNG_BYTES)
        detail = self.ok('GET', '/api/chapters/documents/' + complete['document_id'])
        self.assertIn('OCR_TRANSPORT_MARKER', detail['pages'][0]['text'])
        self.assertTrue(detail['pages'][0]['images'])
        self.assertEqual(detail['document']['source_metadata']['ocr_imports'][0]['source_asset_id'], asset['id'])
        public = json.dumps(self.ok('GET', '/api/ocr/tasks')) + json.dumps(self.ok('GET', '/api/data-hub'))
        self.assertNotIn('MOCK_OCR_SECRET', public)

    def test_interrupted_ocr_state_is_persisted_as_error_without_provider_replay(self):
        asset = self.asset('中断任务.png', pipeline.PNG_BYTES)
        task = self.ok('POST', '/api/ocr/tasks', {'asset_id': asset['id']})['task']
        # Simulate the on-disk state left by a process crash. Initialization
        # must not silently repeat a paid OCR request after restart.
        with self.server.workshop.connect() as db:
            db.execute("UPDATE pd_ocr_tasks SET status='processing' WHERE id=?", (task['id'],))
        self.data.init_db()
        saved = self.ok('GET', '/api/ocr/tasks/' + task['id'])['task']
        self.assertEqual(saved['status'], 'error')
        self.assertIn('重启中断', saved['error'])
        self.assertEqual(self.server.chapters.state()['documents'], [])

    def test_invalid_ocr_response_fails_without_partial_document_or_external_images(self):
        asset = self.asset('错误响应.png', pipeline.PNG_BYTES)
        endpoint = self.start_ocr({'pages': [{'page': 1, 'markdown': '![外链](https://example.invalid/private.png)'}], 'images': []})
        self.ok('POST', '/api/ocr/config', {'endpoint': endpoint})
        task = self.wait_ocr(self.ok('POST', '/api/ocr/tasks', {'asset_id': asset['id']})['task'])
        self.assertEqual(task['status'], 'error')
        self.assertEqual(self.server.chapters.state()['documents'], [])
        self.assertEqual(list((Path(self.directory.name) / 'chapter_packages').rglob('*.md')), [])

    def test_chunking_preserves_image_tokens_and_labels_oversized_table_fallback(self):
        image = '![叶图](documents/document_123/images/image_456.png)'
        table = '| 参数 | 数据 |\n| --- | --- |\n| 大行 | ' + '测量数据' * 40 + ' |'
        chunks = self.data.chunk_markdown('## 标题\n\n' + '拟南芥叶片。' * 40 + '\n\n' + image + '\n\n' + table, 100)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk['text']) <= 100 for chunk in chunks))
        self.assertEqual(sum(image in chunk['text'] for chunk in chunks), 1)
        self.assertTrue(any('oversized_table_text_fallback' in chunk['chunk_strategy'] and chunk['warnings'] for chunk in chunks))

    def test_legacy_version_migrates_as_historical_without_batch_review_claim(self):
        asset = self.asset('旧版资料.md', 'LEGACY_EXPORT_MARKER 叶片原始记录。')
        self.server.workshop.process([asset['id']])
        self.server.workshop.training_export({'type': 'pretrain', 'name': '旧导出'})
        self.data.init_db()
        packages = self.ok('GET', '/api/packages')['packages']
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertEqual(package['metadata']['audit_status'], 'historical')
        self.assertFalse(package['metadata']['revision_eligible'])
        self.assertIsNone(package['batch_id'])
        self.assertIsNone(package['metadata']['sample_pass_rate'])
        self.assertFalse(package['published'])
        self.assertEqual(self.ok('GET', '/api/packages', role='external')['packages'], [])
        self.fail_request('POST', '/api/packages/' + package['id'] + '/publish', {'published': True, 'minimum_role': 'external'}, status=409)
        self.data.init_db()
        self.assertEqual(len(self.ok('GET', '/api/packages')['packages']), 1)

    def test_source_license_limits_override_external_package_role(self):
        document, batch_id, _ = self.accepted_batch()
        doc_id = document['document']['id']
        self.server.chapters.update_document(doc_id, {'license': 'research_only'})
        internal = self.package(batch_id, 'kb')
        self.fail_request('POST', '/api/packages', {'batch_id': batch_id, 'type': 'pretrain'}, status=409)
        self.fail_request('POST', '/api/packages/' + internal['id'] + '/publish', {'published': True, 'minimum_role': 'external'}, status=409)
        self.server.chapters.update_document(doc_id, {'license': 'self_owned'})
        external = self.package(batch_id, 'kb', 'external')
        self.ok('POST', '/api/packages/' + external['id'] + '/publish', {'published': True})
        self.assertTrue(self.ok('POST', '/api/packages/search', {'query': '拟南芥'}, role='external')['results'])
        self.server.chapters.update_document(doc_id, {'license': 'unknown'})
        self.assertEqual(self.ok('GET', '/api/packages', role='external')['packages'], [])
        self.fail_request('GET', external['download_url'], role='external')
        self.fail_request('GET', '/api/packages/' + external['id'], role='external')
        self.assertEqual(self.ok('POST', '/api/packages/search', {'query': '拟南芥'}, role='external')['results'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Account/ACL, immutable prompts and real sampled acceptance invariants."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.server import APIError, create_server


class PlatformCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = create_server(port=0, data_dir=self.tmp.name, seed=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.cookie = None
        status, data, headers = self.http('/api/platform/setup', {'username': 'admin', 'password': 'administrator12'})
        self.assertEqual(status, 200, data)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.admin = data['user']
        self.core = self.server.platform
        self.cp = self.server.chapters

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.tmp.cleanup()

    def http(self, path, body=None, cookie='default'):
        headers = {'Content-Type': 'application/json'}
        selected = self.cookie if cookie == 'default' else cookie
        if selected:
            headers['Cookie'] = selected
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            value = json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
            return response.status, value, response.headers

    def user(self, role):
        return self.core.create_user({'username': role, 'password': 'test-password12', 'role': role}, self.admin)['user']

    def cookie_for(self, username):
        status, value, headers = self.http('/api/platform/login', {'username': username, 'password': 'test-password12'}, cookie=None)
        self.assertEqual(status, 200, value)
        return headers['Set-Cookie'].split(';', 1)[0]

    def document(self, name='Paper', count=5, table=False):
        files = []
        for number in range(1, count + 1):
            text = f'# Chapter {number}\n\nArabidopsis test source page {number}.\n'
            if table:
                text += '\n|Trait|Value|\n|---|---|\n|Height|12 mm|\n'
            files.append({'path': f'{name}/page_{number}.md', 'content_base64': base64.b64encode(text.encode()).decode()})
        document = self.cp.import_package({'name': name, 'files': files, 'license': 'self_owned'})['document']
        with self.server.workshop.connect() as db:
            for chapter in db.execute('SELECT * FROM cp_chapters WHERE document_id=?', (document['id'],)).fetchall():
                pages = self.cp.chapter_pages(db, chapter)
                value = self.cp.validate_result({'facts': [{'text': 'Source-derived fact.', 'source_pages': [pages[0]['number']]}]}, pages, False, 'test preparation')
                db.execute("UPDATE cp_chapters SET result=?,corpus=?,revision=1,status='review' WHERE id=?", (json.dumps(value), value['corpus'], chapter['id']))
        return document

    def batch(self, documents=None, rate=.2, types=None):
        docs = documents or [self.document()]
        return self.core.create_batch({'name': 'Research batch', 'document_ids': [d['id'] for d in docs], 'sampling_rate': rate, 'document_types': types or {}}, self.admin)['batch']['id']

    def pass_samples(self, batch_id):
        detail = self.core.batch_detail(batch_id)
        for sample in detail['samples']:
            self.core.review_sample(batch_id, {'chapter_id': sample['chapter_id'], 'expected_revision': sample['expected_revision'], 'outcome': 'pass'}, self.admin)

    def test_real_sessions_acl_and_legacy_exit_gate(self):
        self.user('internal')
        self.user('external')
        internal, external = self.cookie_for('internal'), self.cookie_for('external')
        self.assertEqual(self.http('/api/state', cookie=None)[0], 401)
        self.assertEqual(self.http('/api/state', cookie=external)[0], 403)
        self.assertEqual(self.http('/api/state', cookie=internal)[0], 200)
        self.assertEqual(self.http('/api/platform/prompts', cookie=internal)[0], 403)
        self.assertEqual(self.http('/api/chapters/model', {'endpoint': '', 'model': ''}, internal)[0], 403)
        doc = self.document(count=1)
        self.assertEqual(self.http('/api/chapters/documents/' + doc['id'], {'extraction_schema': []}, internal)[0], 403)
        for path in ('/api/training/export', '/api/kb/publish', '/api/chapters/materialize'):
            self.assertEqual(self.http(path, {})[0], 409)
        self.assertEqual(self.http('/api/packages', cookie=external)[0], 200)
        self.assertEqual(self.http('/api/platform/setup', {'username': 'again', 'password': 'administrator12'}, None)[0], 409)
        self.assertEqual(self.http('/api/platform/logout', {})[0], 200)
        self.assertEqual(self.http('/api/state')[0], 401)

    def test_password_hash_disable_and_last_admin(self):
        worker = self.user('internal')
        worker_cookie = self.cookie_for('internal')
        with self.server.workshop.connect() as db:
            encoded = db.execute('SELECT password_hash FROM pc_users WHERE id=?', (worker['id'],)).fetchone()[0]
        self.assertNotIn('test-password12', encoded)
        self.assertTrue(encoded.startswith('pbkdf2_sha256$'))
        self.assertNotIn('password_hash', json.dumps(self.core.users()))
        self.core.update_user(worker['id'], {'active': False}, self.admin)
        self.assertIsNone(self.core.current_user(worker_cookie))
        with self.assertRaises(APIError):
            self.core.update_user(self.admin['id'], {'active': False}, self.admin)
        status, _, headers = self.http('/api/platform/password', {'current_password': 'administrator12', 'new_password': 'changed-password12'})
        self.assertEqual(status, 200)
        self.assertIsNone(self.core.current_user(self.cookie))
        self.assertIsNotNone(self.core.current_user(headers['Set-Cookie']))

    def test_document_type_strata_fixed_revisions_and_no_fake_review(self):
        first, second = self.document('Paper', 5), self.document('Report', 5, table=True)
        batch_id = self.batch([first, second], types={first['id']: 'paper', second['id']: 'report'})
        detail = self.core.sample_batch(batch_id, self.admin)
        self.assertEqual({s['literature_type'] for s in detail['samples']}, {'paper', 'report'})
        self.assertEqual(len(detail['samples']), 2)
        self.assertTrue(all(s['expected_revision'] == 1 for s in detail['samples']))
        with self.assertRaises(APIError):
            self.core.accept_batch(batch_id, self.admin)
        self.pass_samples(batch_id)
        self.core.accept_batch(batch_id, self.admin)
        snapshot = self.core.accepted_batch_snapshot(batch_id)
        self.assertEqual(len(snapshot['chapters']), 10)
        self.assertFalse(snapshot['all_human_reviewed'])
        with self.server.workshop.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cp_chapters WHERE status='approved'").fetchone()[0], 0)
        sample_ids = {s['chapter_id'] for s in detail['samples']}
        unsampled = next(c for c in detail['chapters'] if c['id'] not in sample_ids)
        with self.server.workshop.connect() as db:
            db.execute('UPDATE cp_chapters SET revision=2 WHERE id=?', (unsampled['id'],))
        self.assertEqual(self.core.batch_detail(batch_id)['batch']['status'], 'stale')
        with self.assertRaises(APIError):
            self.core.accepted_batch_snapshot(batch_id)

    def test_issue_requires_resolution_and_resampling_keeps_feedback(self):
        batch_id = self.batch(rate=1)
        detail = self.core.sample_batch(batch_id, self.admin)
        sample = detail['samples'][0]
        issue = self.core.review_sample(batch_id, {'chapter_id': sample['chapter_id'], 'expected_revision': 1, 'outcome': 'issue', 'message': '单位需要核对。'}, self.admin)['issues'][0]
        self.pass_samples(batch_id)
        self.assertFalse(self.core.batch_detail(batch_id)['can_accept'])
        self.core.sample_batch(batch_id, self.admin)
        self.assertEqual(self.core.batch_detail(batch_id)['issues'][0]['status'], 'open')
        self.pass_samples(batch_id)
        self.core.resolve_issue(issue['id'], {'action': 'resolve', 'resolution': '已核对原文单位，并记录处理。'}, self.admin)
        self.assertEqual(self.core.accept_batch(batch_id, self.admin)['batch']['status'], 'accepted')

    def test_stale_sample_and_cross_batch_action_are_rejected(self):
        doc = self.document()
        batch_id = self.batch([doc])
        sample = self.core.sample_batch(batch_id, self.admin)['samples'][0]
        with self.server.workshop.connect() as db:
            db.execute('UPDATE cp_chapters SET revision=2 WHERE id=?', (sample['chapter_id'],))
        for expected in (1, 2):
            with self.assertRaises(APIError):
                self.core.review_sample(batch_id, {'chapter_id': sample['chapter_id'], 'expected_revision': expected, 'outcome': 'pass'}, self.admin)
        other = self.document('Other', 1)
        with self.assertRaises(APIError):
            self.core.mutation_batch_context('/api/chapters/run', {'batch_id': batch_id, 'document_id': other['id']})

    def test_prompt_versions_and_pending_provider_are_honest(self):
        worker = self.user('internal')
        prompt = self.core.save_prompt({'name': 'Special', 'type': 'special', 'content': 'PRIVATE_DICTIONARY_A'}, self.admin)['prompt']
        snapshot = self.core.prompt_snapshot({'prompt_id': prompt['id']}, self.admin)
        self.core.save_prompt({'content': 'PRIVATE_DICTIONARY_B'}, self.admin, prompt['id'])
        self.assertEqual(snapshot['version'], 1)
        self.assertEqual(snapshot['content'], 'PRIVATE_DICTIONARY_A')
        with self.assertRaises(APIError):
            self.core.prompt_snapshot({'prompt_id': prompt['id']}, worker)
        batch_id = self.batch()
        with self.assertRaises(APIError):
            self.core.run_batch(batch_id, {}, worker)
        self.assertEqual(self.core.batch_detail(batch_id)['batch']['status'], 'pending')
        self.assertNotIn('PRIVATE_DICTIONARY_A', json.dumps(self.core.redact({'result': {'prompt_snapshot': snapshot}}, worker)))

    def test_feedback_manual_draft_admin_publish_and_audit(self):
        batch_id = self.batch(rate=1)
        sample = self.core.sample_batch(batch_id, self.admin)['samples'][0]
        self.core.review_sample(batch_id, {'chapter_id': sample['chapter_id'], 'expected_revision': 1, 'outcome': 'issue', 'message': '不应补填原文缺失的测量单位。'}, self.admin)
        worker = self.user('internal')
        outcome = self.core.draft_standard({'batch_ids': [batch_id]}, worker)
        self.assertEqual(outcome['standard']['origin'], 'manual_draft')
        self.assertIsNone(outcome['job'])
        self.assertIn('未调用模型', outcome['standard']['content'])
        with self.assertRaises(APIError):
            self.core.save_standard(outcome['standard']['id'], {}, worker, publish=True)
        self.core.save_standard(outcome['standard']['id'], {'content': '遇到未注明单位时保留缺失，要求人工核对。'}, self.admin, publish=True)
        snapshot = self.core.prompt_snapshot({}, worker)
        self.assertEqual(len(snapshot['standards']), 1)
        with self.server.workshop.connect() as db:
            audit = [dict(row) for row in db.execute('SELECT * FROM pc_audit WHERE batch_id=?', (batch_id,))]
        self.assertTrue(any(row['event'] == 'batch_review' and row['actor_id'] == self.admin['id'] for row in audit))
        self.assertTrue(any(row['event'] == 'standard_draft' and row['actor_id'] == worker['id'] for row in audit))

    def test_queued_model_jobs_use_immutable_prompt_and_actor(self):
        worker = self.user('internal')
        doc = self.document(count=1)
        batch_id = self.batch([doc])
        prompt = self.core.save_prompt({'name': 'Selected', 'type': 'special', 'content': 'IMMUTABLE_SELECTED_V1'}, self.admin)['prompt']
        self.cp.configure({'endpoint': 'http://127.0.0.1:9000/v1', 'model': 'mock', 'vision': False})
        captured, entered, release = [], threading.Event(), threading.Event()
        def open_request(request, timeout):
            captured.append(json.loads(request.data))
            entered.set()
            release.wait(3)
            reply = {'choices': [{'message': {'content': json.dumps({'facts': [{'text': 'Grounded fact.', 'source_pages': [1]}]})}}]}
            return io.BytesIO(json.dumps(reply).encode())
        class Opener:
            open = staticmethod(open_request)
        with patch('backend.chapter_pipeline.build_opener', return_value=Opener()):
            self.core.run_batch(batch_id, {'prompt_id': prompt['id']}, self.admin)
            self.assertTrue(entered.wait(2))
            self.core.save_prompt({'content': 'UPDATED_SELECTED_V2'}, self.admin, prompt['id'])
            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(j['status'] in {'queued', 'processing'} for j in self.core.batch_detail(batch_id)['jobs']):
                time.sleep(.02)
            detail = self.core.batch_detail(batch_id)
            self.assertEqual(detail['jobs'][0]['status'], 'completed')
            self.assertEqual(detail['jobs'][0]['actor_id'], self.admin['id'])
            self.assertEqual(detail['jobs'][0]['batch_id'], batch_id)
            self.assertIn('IMMUTABLE_SELECTED_V1', captured[0]['messages'][0]['content'])
            self.assertNotIn('UPDATED_SELECTED_V2', captured[0]['messages'][0]['content'])
            self.core.run_chapter({'document_id': doc['id'], 'batch_id': batch_id, 'mode': 'model'}, worker)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(j['status'] in {'queued', 'processing'} for j in self.core.batch_detail(batch_id)['jobs']):
                time.sleep(.02)
            self.assertIn('IMMUTABLE_SELECTED_V1', captured[1]['messages'][0]['content'])
        archive, _ = self.cp.download(doc['id'], 'chapters')
        import zipfile
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            self.assertNotIn('IMMUTABLE_SELECTED_V1', zipped.read('chapters.jsonl').decode())

    def test_direct_chapter_edits_audit_all_document_batches(self):
        doc = self.document(count=1)
        first, second = self.batch([doc]), self.batch([doc])
        chapter = self.cp.detail(doc['id'])['chapters'][0]
        status, outcome, _ = self.http('/api/chapters/revise', {'chapter_id': chapter['id'], 'expected_revision': chapter['revision'], 'corpus': 'Human revision based on source.'})
        self.assertEqual(status, 200, outcome)
        with self.server.workshop.connect() as db:
            records = [dict(row) for row in db.execute("SELECT * FROM pc_audit WHERE event='http_action' AND detail LIKE '%/api/chapters/revise%'")]
        self.assertEqual({row['batch_id'] for row in records}, {first, second})
        self.assertTrue(all(row['actor_id'] == self.admin['id'] for row in records))


if __name__ == '__main__':
    unittest.main()

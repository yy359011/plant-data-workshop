"""HTTP regression tests for paper packages, chapter processing and model jobs.

All workshop data lives in temporary directories. Model tests use a real local
HTTP endpoint controlled by the test; they never contact a model provider.
"""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import warnings
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
SPEC = importlib.util.spec_from_file_location("chapter_test_workshop", PROJECT_ROOT / "backend" / "server.py")
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGMw7kgDAAITASKwLtbTAAAAAElFTkSuQmCC"
)


def zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for name, content in files:
            archive.writestr(name, content)
    return stream.getvalue()


def legacy_materialize_response(test, body):
    """Exercise the historical serializer; public HTTP now requires batches."""
    try:
        return 200, test.server.chapters.materialize(body)
    except SERVER.APIError as error:
        return error.status, {"error": str(error)}


class RecordingModelHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible transport mock, with fully inspectable requests."""

    def log_message(self, *_args):
        pass

    def do_POST(self):
        content = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(content)
        self.server.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        reply = self.server.reply(body) if callable(self.server.reply) else self.server.reply
        payload = {
            "id": "local-test-completion",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class ChapterPipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="plant-chapter-test-")
        self.start_workshop()
        self.model_server = None

    def start_workshop(self):
        self.server = SERVER.create_server(host="127.0.0.1", port=0, data_dir=self.directory.name, seed=False)
        credentials = {"username": "regression_admin", "display_name": "隔离回归管理员", "password": "local-regression-only-8294"}
        session = self.server.platform.setup(credentials) if self.server.platform.bootstrap()["needs_setup"] else self.server.platform.login(credentials)
        self.test_cookie = session["_cookie"].split(";")[0]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop_workshop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def tearDown(self):
        self.stop_workshop()
        if self.model_server:
            self.model_server.shutdown()
            self.model_server.server_close()
            self.model_thread.join(timeout=5)
        self.directory.cleanup()

    def start_model(self, reply):
        self.model_server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingModelHandler)
        self.model_server.requests = []
        self.model_server.reply = reply
        self.model_thread = threading.Thread(target=self.model_server.serve_forever, daemon=True)
        self.model_thread.start()
        return f"http://127.0.0.1:{self.model_server.server_address[1]}/v1"

    def request(self, method, path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + path, data=body, method=method, headers={"Content-Type": "application/json", "Cookie": self.test_cookie, **(headers or {})})
        try:
            response = urlopen(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            if "application/json" in response.headers.get("Content-Type", ""):
                content = json.loads(content)
            return response.status, content

    def ok(self, method, path, payload=None):
        status, content = self.request(method, path, payload)
        self.assertGreaterEqual(status, 200, content)
        self.assertLess(status, 300, content)
        return content

    def import_package(self, entries=None, **changes):
        if entries is None:
            entries = [
                ("paper/pages/page_10.md", "# 结果\n\n模拟结果页，记录物种为拟南芥。"),
                ("paper/pages/page_2.md", "同一样本叶片宽度为 1.1 cm。\n\n![叶片示意](../images/leaf.png)"),
                ("paper/pages/page_1.md", "# 材料与方法\n\n拟南芥样本 A 叶片长度为 2.4 cm。"),
                ("paper/images/leaf.png", PNG_BYTES),
            ]
        payload = {
            "name": "拟南芥形态观察.zip",
            "content_base64": base64.b64encode(zip_bytes(entries)).decode("ascii"),
            "license": "self_owned",
            "purpose": "both",
            **changes,
        }
        result = self.ok("POST", "/api/chapters/import", payload)
        self.assertTrue(result.get("document"), result)
        document = result["document"]
        return self.ok("GET", "/api/chapters/documents/" + document["id"])

    def chapter_state(self):
        return self.ok("GET", "/api/chapters/state")

    def wait_job(self, job):
        deadline = time.monotonic() + 12
        latest = job
        while time.monotonic() < deadline:
            jobs = self.chapter_state()["jobs"]
            latest = next(item for item in jobs if item["id"] == job["id"])
            if latest["status"] in {"completed", "error", "failed"}:
                return latest
            time.sleep(0.03)
        self.fail(f"Chapter job did not finish: {latest}")

    def run_chapter(self, detail, mode="model", chapter=None):
        chapter = chapter or detail["chapters"][0]
        return self.ok(
            "POST", "/api/chapters/run",
            {"document_id": detail["document"]["id"], "chapter_ids": [chapter["id"]], "mode": mode},
        )["job"]

    def configure_model(self, reply, vision=True):
        endpoint = self.start_model(reply)
        return self.ok(
            "POST", "/api/chapters/model",
            {"endpoint": endpoint, "model": "local-contract-test", "key": "TEST_ONLY_SECRET_94271", "vision": vision},
        )

    def model_reply(self):
        return {
            "corpus": "拟南芥样本 A 叶片长度为 2.4 cm。\n\n同一样本叶片宽度为 1.1 cm。",
            "facts": [
                {"text": "拟南芥样本 A 叶片长度为 2.4 cm。", "source_pages": [1]},
                {"text": "同一样本叶片宽度为 1.1 cm。", "source_pages": [2]},
            ],
            "figures": [], "tables": [],
            "sft_samples": [{"question": "样本 A 的叶片长度是多少？", "answer": "叶片长度为 2.4 cm。", "source_pages": [1]}],
            "warnings": ["全部数据为合成测试记录。"],
        }

    def test_natural_page_order_parent_image_reference_and_cross_page_chapter(self):
        detail = self.import_package()
        self.assertEqual([page["number"] for page in detail["pages"]], [1, 2, 10])
        self.assertEqual(len(detail["chapters"]), 2, detail)
        first = detail["chapters"][0]
        self.assertEqual(first["source_pages"], [1, 2])
        self.assertEqual(len(first["page_ids"]), 2)
        page_two = detail["pages"][1]
        self.assertEqual(page_two["unresolved_images"], [])
        self.assertEqual(len(page_two["images"]), 1)
        image_url = page_two["images"][0]["url"]
        status, data = self.request("GET", image_url)
        self.assertEqual(status, 200)
        self.assertEqual(data, PNG_BYTES)
        duplicate = self.import_package(name="重复上传的同篇论文.zip")
        self.assertEqual(duplicate["document"]["id"], detail["document"]["id"])
        self.assertEqual(len(self.chapter_state()["documents"]), 1)

    def test_unconfigured_model_run_cannot_create_completed_ai_output(self):
        detail = self.import_package()
        status, response = self.request(
            "POST", "/api/chapters/run",
            {"document_id": detail["document"]["id"], "mode": "model"},
        )
        if 200 <= status < 300:
            job = self.wait_job(response["job"])
            self.assertIn(job["status"], {"error", "failed"}, job)
        else:
            self.assertGreaterEqual(status, 400, response)
            self.assertLess(status, 500, response)
        refreshed = self.ok("GET", "/api/chapters/documents/" + detail["document"]["id"])
        self.assertTrue(all(not chapter.get("result") for chapter in refreshed["chapters"]))

    def test_unresolved_image_is_visible_and_blocks_vision_submission(self):
        detail = self.import_package([
            ("paper/page_1.md", "# 材料\n\n拟南芥叶片。\n\n![缺失叶片图](images/missing.png)")
        ])
        self.assertTrue(detail["pages"][0]["unresolved_images"])
        self.configure_model(self.model_reply())
        status, response = self.request(
            "POST", "/api/chapters/run",
            {"document_id": detail["document"]["id"], "mode": "model"},
        )
        if 200 <= status < 300:
            job = self.wait_job(response["job"])
            self.assertIn(job["status"], {"error", "failed"}, job)
        else:
            self.assertGreaterEqual(status, 400, response)
            self.assertLess(status, 500, response)
        self.assertEqual(self.model_server.requests, [])

    def test_unsafe_zip_entries_are_rejected_without_partial_import(self):
        cases = {
            "traversal": [("../escaped.md", "# 外部路径\n不应导入。")],
            "duplicate": [("paper/page_1.md", "# 甲"), ("paper/page_1.md", "# 乙")],
            "compression_bomb": [("paper/page_1.md", "A" * (17 * 1024 * 1024))],
        }
        for case, entries in cases.items():
            with self.subTest(case=case):
                status, content = self.request("POST", "/api/chapters/import", {
                    "name": "unsafe.zip", "license": "self_owned", "purpose": "both",
                    "content_base64": base64.b64encode(zip_bytes(entries)).decode("ascii"),
                })
                self.assertGreaterEqual(status, 400, content)
                self.assertLess(status, 500, content)
                self.assertEqual(self.chapter_state()["documents"], [])
        self.assertFalse((Path(self.directory.name).parent / "escaped.md").exists())

    def test_model_configuration_is_same_origin_and_never_exposes_key(self):
        status, content = self.request(
            "POST", "/api/chapters/model", {"endpoint": "http://127.0.0.1:9999/v1", "model": "test"},
            headers={"Origin": "https://unrelated.example"},
        )
        self.assertEqual(status, 403, content)
        configured = self.configure_model(self.model_reply())
        state = self.chapter_state()
        standard_state = self.ok("GET", "/api/state")
        for response in (configured, state, standard_state):
            self.assertNotIn("TEST_ONLY_SECRET_94271", json.dumps(response))
        self.assertTrue(state["model"]["has_key"])

    def test_folder_import_uses_markdown_and_json_pages_without_duplicate_page(self):
        entries = [
            ("paper/page_1.md", "# 引言\n\nMD_PRIMARY_MARKER 拟南芥观察记录。"),
            ("paper/page_1.json", json.dumps({"page": 1, "text": "JSON_DUPLICATE_MARKER"})),
            ("paper/page_2.json", json.dumps({"page": 2, "markdown": "继续引言：JSON_PAGE_TWO_MARKER 叶片测量。"})),
            ("paper/page_3.json", json.dumps({"page": 3, "content": "# 结果\n\nJSON_PAGE_THREE_MARKER 观察结果。"})),
        ]
        imported = self.ok("POST", "/api/chapters/import", {
            "name": "文件夹导入", "license": "self_owned", "purpose": "both",
            "files": [{"path": path, "content_base64": base64.b64encode(text.encode()).decode()} for path, text in entries],
        })
        detail = self.ok("GET", "/api/chapters/documents/" + imported["document"]["id"])
        self.assertEqual([page["number"] for page in detail["pages"]], [1, 2, 3])
        self.assertIn("MD_PRIMARY_MARKER", detail["pages"][0]["text"])
        self.assertNotIn("JSON_DUPLICATE_MARKER", json.dumps(detail["pages"]))
        self.assertIn("JSON_PAGE_TWO_MARKER", detail["pages"][1]["text"])
        self.assertEqual(detail["chapters"][0]["source_pages"], [1, 2])

    def test_real_model_http_job_persists_and_requires_approval_before_materializing(self):
        detail = self.import_package()
        document_id = detail["document"]["id"]
        original_pages = [(page["id"], page["text"]) for page in detail["pages"]]
        self.configure_model(self.model_reply())
        job = self.wait_job(self.run_chapter(detail))
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(len(self.model_server.requests), 1)
        captured = self.model_server.requests[0]
        self.assertTrue(captured["path"].endswith("/chat/completions"), captured)
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer TEST_ONLY_SECRET_94271")
        self.assertEqual(captured["body"]["model"], "local-contract-test")
        model_input = json.dumps(captured["body"], ensure_ascii=False)
        self.assertIn("2.4 cm", model_input)
        self.assertIn("1.1 cm", model_input)
        self.assertIn("data:image/png;base64,", model_input)

        status, premature = legacy_materialize_response(self, {"document_id": document_id})
        if 200 <= status < 300:
            self.assertFalse(premature["created"], premature)
        else:
            self.assertGreaterEqual(status, 400, premature)
            self.assertLess(status, 500, premature)
        self.assertEqual(self.ok("GET", "/api/state")["assets"], [])
        processed = self.ok("GET", "/api/chapters/documents/" + document_id)
        chapter = next(item for item in processed["chapters"] if item["id"] == detail["chapters"][0]["id"])
        self.assertEqual(chapter["result"]["facts"], self.model_reply()["facts"])
        self.assertNotEqual(chapter["status"], "approved")
        status, pending_archive = self.request("GET", f"/api/chapters/download/{document_id}?stage=corpus")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(pending_archive)) as archive:
            self.assertEqual(archive.read("corpus.jsonl"), b"")

        self.stop_workshop()
        self.start_workshop()
        persisted_job = next(item for item in self.chapter_state()["jobs"] if item["id"] == job["id"])
        self.assertEqual(persisted_job["status"], "completed")
        self.assertNotIn("TEST_ONLY_SECRET_94271", json.dumps(self.chapter_state()))
        self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve"})
        materialized = self.server.chapters.materialize({"document_id": document_id})
        self.assertTrue(materialized["created"], materialized)
        state_before_repeat = self.ok("GET", "/api/state")
        initial_sft = self.ok("POST", "/api/training/preview", {"type": "sft", "source": "chapters"})
        self.assertGreater(initial_sft["total"], 0)
        self.assertIn("2.4 cm", json.dumps(initial_sft, ensure_ascii=False))
        repeated = self.server.chapters.materialize({"document_id": document_id})
        self.assertFalse(repeated["created"], repeated)
        self.assertEqual(
            [asset["id"] for asset in self.ok("GET", "/api/state")["assets"]],
            [asset["id"] for asset in state_before_repeat["assets"]],
        )
        final_detail = self.ok("GET", "/api/chapters/documents/" + document_id)
        self.assertEqual([(page["id"], page["text"]) for page in final_detail["pages"]], original_pages)
        status, pages_archive = self.request("GET", f"/api/chapters/download/{document_id}?stage=pages")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(pages_archive)) as archive:
            page_records = [json.loads(line) for line in archive.read("pages.jsonl").decode().splitlines()]
            self.assertEqual([(page["id"], page["text"]) for page in page_records], original_pages)
            images = [name for name in archive.namelist() if name.endswith("leaf.png")]
            self.assertEqual(len(images), 1)
            self.assertEqual(archive.read(images[0]), PNG_BYTES)
        status, corpus_archive = self.request("GET", f"/api/chapters/download/{document_id}?stage=corpus")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(corpus_archive)) as archive:
            corpus_records = [json.loads(line) for line in archive.read("corpus.jsonl").decode().splitlines()]
        self.assertEqual(len(corpus_records), 1)
        self.assertEqual(corpus_records[0]["status"], "approved")
        self.assertEqual(corpus_records[0]["source_pages"], [1, 2])
        preview = self.ok("POST", "/api/training/preview", {"type": "pretrain", "name": "章节正文"})
        self.assertTrue(preview["samples"], preview)
        self.assertIn("2.4 cm", json.dumps(preview, ensure_ascii=False))
        for sample in preview["samples"]:
            self.assertEqual(sample["metadata"]["source_document_id"], document_id)
            self.assertEqual(sample["metadata"]["content_origin"], "chapter_model")
            self.assertEqual(sample["metadata"]["provenance"]["source_pages"], [1, 2])
            self.assertEqual(sample["metadata"]["provenance"]["model"], "local-contract-test")
            self.assertEqual(sample["metadata"]["provenance"]["source_paths"], ["paper/pages/page_1.md", "paper/pages/page_2.md"])
            self.assertTrue(sample["metadata"]["provenance"]["endpoint"].startswith("http://127.0.0.1:"))
        self.server.workshop.publish_kb({"name": "审核后的章节知识库", "chunk_size": 500})
        search = self.ok("POST", "/api/kb/search", {"query": "拟南芥"})
        self.assertTrue(search["results"], search)

        self.ok("POST", "/api/import", {
            "files": [{"name": "独立原始资料.txt", "content_base64": base64.b64encode(b"ORIGINAL_SOURCE_ONLY_781").decode()}],
            "license": "self_owned", "purpose": "both", "process": True,
        })
        originals = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "original"})
        self.assertEqual(originals["total"], 1)
        self.assertEqual(originals["samples"][0]["text"], "ORIGINAL_SOURCE_ONLY_781")
        chapters_only = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})
        self.assertEqual(chapters_only["total"], preview["total"])
        self.assertNotIn("ORIGINAL_SOURCE_ONLY_781", json.dumps(chapters_only))
        all_sources = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "all"})
        self.assertEqual(all_sources["total"], originals["total"] + chapters_only["total"])
        exported = self.server.workshop.training_export({"type": "pretrain", "source": "chapters", "name": "仅章节导出"})
        status, archive_bytes = self.request("GET", exported["download_url"])
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            exported_text = "\n".join(archive.read(name).decode() for name in archive.namelist() if name.endswith(".jsonl"))
        self.assertNotIn("ORIGINAL_SOURCE_ONLY_781", exported_text)
        self.assertNotIn("TEST_ONLY_SECRET_94271", exported_text)
        self.assertIn(document_id, exported_text)
        originals_kb = self.server.workshop.publish_kb({"name": "仅原始", "chunk_size": 500, "source": "original"})
        original_search = self.ok("POST", "/api/kb/search", {"query": "ORIGINAL_SOURCE_ONLY_781", "version_id": originals_kb["version"]["id"]})
        self.assertTrue(original_search["results"])
        chapters_kb = self.server.workshop.publish_kb({"name": "仅章节", "chunk_size": 500, "source": "chapters"})
        forbidden_search = self.ok("POST", "/api/kb/search", {"query": "ORIGINAL_SOURCE_ONLY_781", "version_id": chapters_kb["version"]["id"]})
        self.assertEqual(forbidden_search["results"], [])
        status, archive_bytes = self.request("GET", chapters_kb["download_url"])
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            chunks = [json.loads(line) for line in archive.read("chunks.jsonl").decode().splitlines()]
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertEqual(chunk["source_document_id"], document_id)
            self.assertEqual(chunk["provenance"]["source_pages"], [1, 2])
            self.assertEqual(chunk["provenance"]["mode"], "model")

        self.ok("POST", "/api/chapters/documents/" + document_id, {"license": "unknown"})
        self.assertEqual(self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})["total"], 0)
        revoked_assets = [asset for asset in self.ok("GET", "/api/state")["assets"] if asset.get("source_document_id") == document_id]
        self.assertTrue(revoked_assets)
        self.assertTrue(all(asset["license"] == "unknown" for asset in revoked_assets))
        self.assertEqual(self.ok("POST", "/api/kb/search", {"query": "拟南芥", "version_id": chapters_kb["version"]["id"]})["results"], [])
        self.ok("POST", "/api/chapters/documents/" + document_id, {"license": "self_owned"})
        self.assertEqual(self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})["total"], chapters_only["total"])

        rerun = self.wait_job(self.run_chapter(detail))
        self.assertEqual(rerun["status"], "completed", rerun)
        self.assertEqual(self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})["total"], 0)
        status, unavailable = self.request("POST", "/api/kb/publish", {"name": "待重新审批", "source": "chapters", "chunk_size": 500})
        self.assertGreaterEqual(status, 400, unavailable)
        self.assertLess(status, 500, unavailable)
        rerun_detail = self.ok("GET", "/api/chapters/documents/" + document_id)
        revised_chapter = next(item for item in rerun_detail["chapters"] if item["id"] == chapter["id"])
        self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "corpus": revised_chapter["corpus"] + "\n人工补记：仍需对照合成原始记录。"})
        self.server.chapters.materialize({"document_id": document_id})
        self.assertEqual(self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})["total"], chapters_only["total"])
        self.assertEqual(self.ok("POST", "/api/training/preview", {"type": "sft", "source": "chapters"})["total"], 0)
        self.ok("POST", "/api/chapters/rebuild", {"document_id": document_id, "chapters": [{"title": "重分章后的资料", "page_ids": [page_id for page_id, _text in original_pages]}]})
        self.assertEqual(self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})["total"], 0)

    def test_model_response_with_out_of_chapter_citation_is_not_accepted(self):
        detail = self.import_package()
        reply = self.model_reply()
        reply["facts"][0]["source_pages"] = [999]
        self.configure_model(reply)
        job = self.wait_job(self.run_chapter(detail))
        self.assertIn(job["status"], {"error", "failed"}, job)
        self.assertEqual(len(self.model_server.requests), 1)
        refreshed = self.ok("GET", "/api/chapters/documents/" + detail["document"]["id"])
        target = next(item for item in refreshed["chapters"] if item["id"] == detail["chapters"][0]["id"])
        self.assertNotEqual(target["status"], "approved")
        self.assertFalse(target.get("result"), target)
        self.assertEqual(self.ok("GET", "/api/state")["assets"], [])

    def test_chapter_rebuild_can_merge_pages_without_rewriting_originals(self):
        detail = self.import_package()
        document_id = detail["document"]["id"]
        originals = [(page["id"], page["text"]) for page in detail["pages"]]
        self.ok("POST", "/api/chapters/rebuild", {
            "document_id": document_id,
            "chapters": [{"title": "完整形态观察章节", "page_ids": [page_id for page_id, _text in originals]}],
        })
        rebuilt = self.ok("GET", "/api/chapters/documents/" + document_id)
        self.assertEqual(len(rebuilt["chapters"]), 1)
        self.assertEqual(rebuilt["chapters"][0]["title"], "完整形态观察章节")
        self.assertEqual(rebuilt["chapters"][0]["source_pages"], [1, 2, 10])
        self.assertEqual([(page["id"], page["text"]) for page in rebuilt["pages"]], originals)

    def test_state_remains_pollable_while_model_request_is_in_flight(self):
        detail = self.import_package()
        model_entered = threading.Event()
        release_model = threading.Event()
        states_received = threading.Event()
        responses = []

        def delayed_reply(_body):
            model_entered.set()
            release_model.wait(6)
            return self.model_reply()

        self.configure_model(delayed_reply)
        job = self.run_chapter(detail)
        self.assertTrue(model_entered.wait(3), "The worker did not reach the model endpoint")

        def read_states():
            try:
                responses.append(self.request("GET", "/api/state"))
                responses.append(self.request("GET", "/api/chapters/state"))
                responses.append(self.request("POST", "/api/chapters/model", {"model": "should-not-change-mid-request"}))
            finally:
                states_received.set()

        polling = threading.Thread(target=read_states, daemon=True)
        polling.start()
        try:
            responsive = states_received.wait(2)
        finally:
            release_model.set()
        polling.join(timeout=5)
        self.assertTrue(responsive, "State polling blocked while the model endpoint was waiting")
        self.assertEqual([status for status, _payload in responses], [200, 200, 409], responses)
        finished = self.wait_job(job)
        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(self.chapter_state()["model"]["model"], "local-contract-test")

    def test_model_chapters_share_one_source_document_split(self):
        detail = self.import_package([
            (f"paper/page_{index}.md", f"# 观察章节 {index}\n\nDEMO_CHAPTER_{index:02d}：本章为合成植物观察演示内容。")
            for index in range(1, 13)
        ])
        document_id = detail["document"]["id"]
        self.assertEqual(len(detail["chapters"]), 12)

        def reply_for_page(body):
            match = re.search(r"DEMO_CHAPTER_(\d{2})", json.dumps(body, ensure_ascii=False))
            self.assertIsNotNone(match)
            number = int(match.group(1))
            return {
                "facts": [{"text": f"DEMO_CHAPTER_{number:02d}：本章为合成植物观察演示内容。", "source_pages": [number]}],
                "figures": [], "tables": [], "sft_samples": [], "warnings": [],
            }

        self.configure_model(reply_for_page, vision=False)
        submitted = self.ok("POST", "/api/chapters/run", {"document_id": document_id, "mode": "model"})
        finished = self.wait_job(submitted["job"])
        self.assertEqual(finished["status"], "completed", finished)
        processed = self.ok("GET", "/api/chapters/documents/" + document_id)
        for chapter in processed["chapters"]:
            self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve"})
        materialized = self.server.chapters.materialize({"document_id": document_id})
        self.assertTrue(materialized["created"])
        preview = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})
        self.assertGreaterEqual(preview["total"], 12)
        self.assertEqual(preview["split_counts"]["validation"], 0)
        self.assertEqual(preview["split_counts"]["test"], 0)
        self.assertEqual(preview["split_counts"]["train"], preview["total"])
        for sample in preview["samples"]:
            self.assertEqual(sample["metadata"]["source_document_id"], document_id)
            self.assertEqual(sample["metadata"]["source_group"], document_id)
        self.assertEqual(len(self.model_server.requests), 12)

    def test_demo_mode_is_explicitly_limited_to_builtin_demo_documents(self):
        imported = self.import_package()
        status, error = self.request("POST", "/api/chapters/run", {"document_id": imported["document"]["id"], "mode": "demo"})
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)
        created = self.ok("POST", "/api/chapters/demo", {})
        document = created["document"]
        repeated = self.ok("POST", "/api/chapters/demo", {})
        self.assertEqual(repeated["document"]["id"], document["id"])
        detail = self.ok("GET", "/api/chapters/documents/" + document["id"])
        self.assertTrue(detail["document"]["is_demo"])
        submitted = self.ok("POST", "/api/chapters/run", {"document_id": document["id"], "mode": "demo"})
        finished = self.wait_job(submitted["job"])
        self.assertEqual(finished["status"], "completed", finished)
        processed = self.ok("GET", "/api/chapters/documents/" + document["id"])
        for chapter in processed["chapters"]:
            self.assertTrue(chapter["result"]["is_demo"])
            self.assertIn("演示", chapter["corpus"])
            self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve"})
        self.server.chapters.materialize({"document_id": document["id"]})
        preview = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})
        self.assertTrue(preview["samples"])
        self.assertTrue(all(sample["metadata"]["is_demo"] for sample in preview["samples"]))
        self.assertTrue(all(sample["metadata"]["content_origin"] == "chapter_demo" for sample in preview["samples"]))
        sft_preview = self.ok("POST", "/api/training/preview", {"type": "sft", "source": "chapters"})
        self.assertGreater(sft_preview["total"], 0)
        self.assertTrue(all(sample["metadata"]["is_demo"] for sample in sft_preview["samples"]))
        original_text = "\n".join(page["text"] for page in detail["pages"])
        for sample in sft_preview["samples"]:
            answer = next(message["content"] for message in sample["messages"] if message["role"] == "assistant")
            self.assertIn(answer.split(" [来源页：")[0], original_text)
        self.assertFalse(self.chapter_state()["model"]["configured"])

    def test_figure_only_chapter_result_can_be_reviewed_without_fabricated_facts(self):
        detail = self.import_package([
            ("paper/page_1.md", "# 图像观察\n\n![合成色块示意](images/leaf.png)"),
            ("paper/images/leaf.png", PNG_BYTES),
        ])
        image_id = detail["pages"][0]["images"][0]["id"]
        self.configure_model({
            "facts": [], "tables": [], "sft_samples": [], "warnings": [],
            "figures": [{"image_id": image_id, "caption": "合成色块示意", "interpretation": "图像是测试色块，不能据此推断植物形态。", "source_pages": [1]}],
        })
        job = self.wait_job(self.run_chapter(detail))
        self.assertEqual(job["status"], "completed", job)
        refreshed = self.ok("GET", "/api/chapters/documents/" + detail["document"]["id"])
        chapter = refreshed["chapters"][0]
        self.assertEqual(chapter["result"]["facts"], [])
        self.assertEqual(len(chapter["result"]["figures"]), 1)
        self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve"})
        created = self.server.chapters.materialize({"document_id": detail["document"]["id"]})
        self.assertTrue(created["created"])

    def test_interrupted_queue_is_marked_error_without_replaying_model_requests(self):
        detail = self.import_package()
        self.configure_model(self.model_reply())
        job = self.wait_job(self.run_chapter(detail))
        self.assertEqual(job["status"], "completed")
        for interrupted_status in ("queued", "processing"):
            with self.subTest(state=interrupted_status):
                self.stop_workshop()
                connection = sqlite3.connect(Path(self.directory.name) / "workshop.sqlite3")
                try:
                    # Simulate a persisted job left behind by a terminated process.
                    connection.execute("UPDATE cp_jobs SET status=? WHERE id=?", (interrupted_status, job["id"]))
                    connection.execute("UPDATE cp_chapters SET status=? WHERE id=?", (interrupted_status, detail["chapters"][0]["id"]))
                    connection.commit()
                finally:
                    connection.close()
                self.start_workshop()
                restored = next(item for item in self.chapter_state()["jobs"] if item["id"] == job["id"])
                self.assertEqual(restored["status"], "error", restored)
                self.assertTrue(restored["error"])
                refreshed = self.ok("GET", "/api/chapters/documents/" + detail["document"]["id"])
                chapter = next(item for item in refreshed["chapters"] if item["id"] == detail["chapters"][0]["id"])
                self.assertEqual(chapter["status"], "error")
                self.assertEqual(len(self.model_server.requests), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

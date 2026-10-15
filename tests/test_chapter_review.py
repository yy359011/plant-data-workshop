"""PDF source preservation and revision-aware chapter review API regressions.

The workshop and the model HTTP mock use temporary local resources only.
"""

import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_chapter_pipeline as pipeline


def make_pdf(label="Synthetic botanical source", page_count=10):
    """A complete small PDF fixture, including valid object offsets and xref."""
    content_id, font_id = page_count + 3, page_count + 4
    stream = f"BT /F1 10 Tf 10 150 Td ({label}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (f"<< /Type /Pages /Count {page_count} /Kids [" + " ".join(f"{i} 0 R" for i in range(3, page_count + 3)) + "] >>").encode(),
    ]
    for _ in range(page_count):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode())
    objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n% local test fixture\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(offsets)}\n".encode())
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return output.getvalue()


class ChapterReviewTests(unittest.TestCase):
    # Reuse the proven isolated HTTP fixture without inheriting its test cases.
    setUp = pipeline.ChapterPipelineTests.setUp
    tearDown = pipeline.ChapterPipelineTests.tearDown
    start_workshop = pipeline.ChapterPipelineTests.start_workshop
    stop_workshop = pipeline.ChapterPipelineTests.stop_workshop
    start_model = pipeline.ChapterPipelineTests.start_model
    request = pipeline.ChapterPipelineTests.request
    ok = pipeline.ChapterPipelineTests.ok
    import_package = pipeline.ChapterPipelineTests.import_package
    chapter_state = pipeline.ChapterPipelineTests.chapter_state
    wait_job = pipeline.ChapterPipelineTests.wait_job
    run_chapter = pipeline.ChapterPipelineTests.run_chapter
    configure_model = pipeline.ChapterPipelineTests.configure_model
    model_reply = pipeline.ChapterPipelineTests.model_reply

    def import_paper(self, with_pdf=True):
        entries = [
            ("paper/pages/page_1.md", "# 材料与方法\n\n拟南芥样本 A 叶片长度为 2.4 cm。"),
            ("paper/pages/page_2.md", "同一样本叶片宽度为 1.1 cm。"),
        ]
        if with_pdf:
            entries.append(("paper/植物原论文.pdf", make_pdf()))
        return self.import_package(entries)

    def raw_request(self, method, path, headers=None):
        request = Request(self.base_url + path, method=method, headers={"Cookie": self.test_cookie, **(headers or {})})
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def attach_pdf(self, document_id, data=None, name="植物原论文.pdf", **options):
        return self.ok("POST", f"/api/chapters/documents/{document_id}/pdfs", {
            "name": name,
            "content_base64": base64.b64encode(data if data is not None else make_pdf()).decode(),
            **options,
        })

    def refresh(self, document_id):
        return self.ok("GET", "/api/chapters/documents/" + document_id)

    def generate(self):
        detail = self.import_paper()
        self.configure_model(self.model_reply(), vision=False)
        job = self.wait_job(self.run_chapter(detail))
        self.assertEqual(job["status"], "completed", job)
        return self.refresh(detail["document"]["id"])

    def preview(self, kind="pretrain"):
        return self.ok("POST", "/api/training/preview", {"type": kind, "source": "chapters"})

    def test_pdf_discovery_inline_ranges_and_append_only_upload(self):
        detail = self.import_paper()
        document_id = detail["document"]["id"]
        self.assertEqual(detail["document"]["pdf_count"], 1)
        original = detail["pdfs"][0]
        self.assertEqual(original["source"], "package")
        self.assertEqual(original["sha256"], hashlib.sha256(make_pdf()).hexdigest())
        self.assertIn(original["page_count"], {None, 10})
        self.assertEqual(detail["selected_pdf"]["id"], original["id"])
        status, body, headers = self.raw_request("GET", original["url"])
        self.assertEqual(status, 200)
        self.assertEqual(body, make_pdf())
        self.assertEqual(headers.get("Content-Type"), "application/pdf")
        self.assertTrue(headers.get("Content-Disposition", "").startswith("inline"))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        status, body, headers = self.raw_request("HEAD", original["url"])
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(int(headers["Content-Length"]), len(make_pdf()))
        for range_value, expected, start, end in (
            ("bytes=0-15", make_pdf()[:16], 0, 15),
            ("bytes=16-", make_pdf()[16:], 16, len(make_pdf()) - 1),
            ("bytes=-8", make_pdf()[-8:], len(make_pdf()) - 8, len(make_pdf()) - 1),
        ):
            with self.subTest(range=range_value):
                status, body, headers = self.raw_request("GET", original["url"], {"Range": range_value})
                self.assertEqual(status, 206)
                self.assertEqual(body, expected)
                self.assertEqual(headers["Content-Range"], f"bytes {start}-{end}/{len(make_pdf())}")
        status, _body, headers = self.raw_request("GET", original["url"], {"Range": f"bytes={len(make_pdf()) + 5}-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers.get("Content-Range"), f"bytes */{len(make_pdf())}")

        duplicate = self.attach_pdf(document_id, name="同内容改名.pdf")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["pdf"]["id"], original["id"])
        added_bytes = make_pdf("Second archived source")
        added = self.attach_pdf(document_id, added_bytes)
        self.assertFalse(added["duplicate"])
        self.assertNotEqual(added["pdf"]["id"], original["id"])
        self.assertEqual(added["document"]["pdf_count"], 2)
        self.assertEqual(self.raw_request("GET", original["url"])[1], make_pdf())
        self.assertEqual(self.raw_request("GET", added["pdf"]["url"])[1], added_bytes)
        refreshed = self.refresh(document_id)
        self.assertEqual([p["text"] for p in refreshed["pages"]], [p["text"] for p in detail["pages"]])

    def test_pdf_bad_magic_paths_and_cross_document_selection_are_rejected(self):
        detail = self.import_paper(with_pdf=False)
        document_id = detail["document"]["id"]
        cases = [
            ("fake.pdf", b"<html>not a PDF</html>"),
            ("truncated.pdf", b"%PDF-1.4\nmissing EOF"),
            ("../escape.pdf", make_pdf()),
            ("/tmp/escape.pdf", make_pdf()),
            ("nested\\escape.pdf", make_pdf()),
        ]
        for name, data in cases:
            with self.subTest(name=name):
                status, error = self.request("POST", f"/api/chapters/documents/{document_id}/pdfs", {"name": name, "content_base64": base64.b64encode(data).decode()})
                self.assertGreaterEqual(status, 400, error)
                self.assertLess(status, 500, error)
                self.assertEqual(self.refresh(document_id)["document"]["pdf_count"], 0)
        status, error = self.request("POST", "/api/chapters/import", {
            "name": "坏PDF资料包.zip", "purpose": "both", "license": "self_owned",
            "content_base64": base64.b64encode(pipeline.zip_bytes([
                ("invalid/page_1.md", "# 坏PDF资料包"), ("invalid/source.pdf", b"not a pdf")
            ])).decode(),
        })
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)

        own_pdf = self.attach_pdf(document_id)["pdf"]
        foreign = self.import_package([("other/page_1.md", "# 另一篇独立文献\n\n其他来源。")])
        foreign_id = foreign["document"]["id"]
        foreign_pdf = self.attach_pdf(foreign_id)["pdf"]
        status, error = self.request("POST", "/api/chapters/documents/" + document_id, {"selected_pdf_id": foreign_pdf["id"]})
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)
        self.assertEqual(self.refresh(document_id)["selected_pdf"]["id"], own_pdf["id"])
        for url in (f"/api/chapters/pdfs/{document_id}/{foreign_pdf['id']}", f"/api/chapters/pdfs/{document_id}/%2e%2e%2fworkshop.sqlite3"):
            status, _body, _headers = self.raw_request("GET", url)
            self.assertGreaterEqual(status, 400)
            self.assertLess(status, 500)
        self.ok("POST", "/api/chapters/documents/" + document_id, {"selected_pdf_id": own_pdf["id"], "pdf_page_offset": 2})
        self.assertEqual(self.refresh(document_id)["document"]["pdf_page_offset"], 2)
        status, error = self.request("POST", "/api/chapters/documents/" + document_id, {"pdf_page_offset": "not-a-page-number"})
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)

    def test_legacy_archived_package_pdf_is_discovered_after_restart(self):
        detail = self.import_paper()
        document_id = detail["document"]["id"]
        self.stop_workshop()
        connection = sqlite3.connect(Path(self.directory.name) / "workshop.sqlite3")
        try:
            # The older application archived package PDFs without a PDF registry.
            connection.execute("DELETE FROM cp_pdfs WHERE document_id=?", (document_id,))
            connection.execute("UPDATE cp_documents SET selected_pdf_id=NULL WHERE id=?", (document_id,))
            connection.commit()
        finally:
            connection.close()
        self.start_workshop()
        restored = self.refresh(document_id)
        self.assertEqual(restored["document"]["pdf_count"], 1)
        self.assertEqual(restored["pdfs"][0]["source"], "package")
        self.assertEqual(self.raw_request("GET", restored["pdfs"][0]["url"])[1], make_pdf())
        self.assertEqual([p["text"] for p in restored["pages"]], [p["text"] for p in detail["pages"]])

    def test_model_reorganization_sends_exact_instructions_and_keeps_history(self):
        detail = self.generate()
        document_id, chapter = detail["document"]["id"], detail["chapters"][0]
        instructions = "按“观测记录→限制”重组。\n保留 2.4 cm 与 1.1 cm，不补充数值。"
        reply = self.model_reply()
        reply["facts"] = list(reversed(reply["facts"]))
        self.model_server.reply = reply
        submitted = self.ok("POST", "/api/chapters/run", {
            "document_id": document_id, "chapter_ids": [chapter["id"]], "mode": "model",
            "instructions": instructions, "expected_revision": chapter["revision"],
        })
        job = self.wait_job(submitted["job"])
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["instructions"], instructions)
        self.assertEqual(len(self.model_server.requests), 2)
        messages = self.model_server.requests[-1]["body"]["messages"]
        self.assertGreaterEqual(len(messages), 4)
        self.assertIn("原始 OCR 页面", messages[1]["content"])
        self.assertIn(instructions, messages[-1]["content"])
        previous_message = messages[-2]["content"]
        self.assertIn("上次派生结果", previous_message)
        previous_payload = json.loads(previous_message.split("\n", 1)[1])
        self.assertEqual(previous_payload["corpus"], chapter["corpus"])
        self.assertEqual(previous_payload["revision"], chapter["revision"])
        revised = self.refresh(document_id)["chapters"][0]
        self.assertEqual(revised["revision"], chapter["revision"] + 1)
        self.assertNotEqual(revised["corpus"], chapter["corpus"])
        history = self.ok("GET", "/api/chapters/history/" + chapter["id"])
        self.assertTrue(any(row["event"] == "run_requested" and row["payload"].get("instructions") == instructions for row in history["history"]))
        self.assertEqual(len([row for row in history["history"] if row["event"] == "generated"]), 2)
        self.assertNotIn("TEST_ONLY_SECRET_94271", json.dumps(history))
        self.assertEqual(self.raw_request("GET", detail["pdfs"][0]["url"])[1], make_pdf())
        self.ok("POST", "/api/chapters/rebuild", {"document_id": document_id, "chapters": [{"title": "重新划分", "page_ids": [page["id"] for page in detail["pages"]]}]})
        historical = self.ok("GET", "/api/chapters/history/" + chapter["id"])
        self.assertFalse(historical["active"])
        self.assertEqual(historical["history"], history["history"])

    def test_manual_edit_invalidates_old_outputs_and_requires_new_approval(self):
        detail = self.generate()
        document_id, chapter = detail["document"]["id"], detail["chapters"][0]
        self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "expected_revision": chapter["revision"]})
        self.server.chapters.materialize({"document_id": document_id})
        self.assertGreater(self.preview()["total"], 0)
        self.assertGreater(self.preview("sft")["total"], 0)
        old_assets = {asset["id"] for asset in self.ok("GET", "/api/state")["assets"]}
        corpus = chapter["corpus"] + "\n\nMANUAL_REVISION_MARKER：人工说明，保留原观测数值。"
        saved = self.ok("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "corpus": corpus, "expected_revision": chapter["revision"]})
        self.assertEqual(saved["revision"], chapter["revision"] + 1)
        self.assertEqual(saved["status"], "review")
        self.assertEqual(saved["result"]["sft_samples"], [])
        self.assertTrue(saved["result"]["human_edited"])
        self.assertEqual(saved["result"]["edit_origin"], "manual")
        self.assertEqual(self.preview()["total"], 0)
        self.assertEqual(self.preview("sft")["total"], 0)
        premature = self.server.chapters.materialize({"document_id": document_id})
        self.assertEqual(premature["created"], 0)
        status, error = self.request("POST", "/api/kb/publish", {"name": "未重新审批", "source": "chapters", "chunk_size": 500})
        self.assertGreaterEqual(status, 400, error)
        approved = self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "expected_revision": saved["revision"]})
        self.assertEqual(approved["revision"], saved["revision"])
        self.assertEqual(self.preview()["total"], 0)
        created = self.server.chapters.materialize({"document_id": document_id})
        self.assertGreater(created["created"], 0)
        preview = self.preview()
        self.assertIn("MANUAL_REVISION_MARKER", json.dumps(preview, ensure_ascii=False))
        self.assertTrue(all(item["metadata"]["source_id"] not in old_assets for item in preview["samples"]))
        self.assertEqual(self.preview("sft")["total"], 0)
        final = self.refresh(document_id)
        self.assertEqual([(p["id"], p["text"]) for p in final["pages"]], [(p["id"], p["text"]) for p in detail["pages"]])
        self.assertEqual(self.raw_request("GET", final["pdfs"][0]["url"])[1], make_pdf())
        history = self.ok("GET", "/api/chapters/history/" + chapter["id"])
        edits = [row for row in history["history"] if row["event"] == "manual_revised"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["payload"]["corpus"], corpus)
        self.assertEqual(edits[0]["payload"]["previous_result"]["corpus"], chapter["corpus"])
        self.assertTrue(any(row["event"] == "approved" and row["revision"] == saved["revision"] for row in history["history"]))

    def test_stale_revision_guards_do_not_overwrite_or_trigger_model_calls(self):
        detail = self.generate()
        document_id, chapter = detail["document"]["id"], detail["chapters"][0]
        saved = self.ok("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "corpus": chapter["corpus"] + "\n新修订。", "expected_revision": chapter["revision"]})
        attempts = [
            ("/api/chapters/revise", {"chapter_id": chapter["id"], "corpus": "陈旧编辑", "expected_revision": chapter["revision"]}),
            ("/api/chapters/revise", {"chapter_id": chapter["id"], "corpus": "缺少版本校验"}),
            ("/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "expected_revision": chapter["revision"]}),
            ("/api/chapters/run", {"document_id": document_id, "chapter_ids": [chapter["id"]], "mode": "model", "expected_revision": chapter["revision"]}),
            ("/api/chapters/run", {"document_id": document_id, "chapter_ids": [chapter["id"]], "mode": "model", "expected_revisions": {chapter["id"]: chapter["revision"]}}),
        ]
        for path, payload in attempts:
            with self.subTest(path=path, payload=payload):
                status, error = self.request("POST", path, payload)
                self.assertEqual(status, 409, error)
        current = self.refresh(document_id)["chapters"][0]
        self.assertEqual(current["revision"], saved["revision"])
        self.assertEqual(current["corpus"], saved["corpus"])
        self.assertEqual(current["status"], "review")
        self.assertEqual(len(self.model_server.requests), 1)
        self.assertEqual(len(self.chapter_state()["jobs"]), 1)

    def test_processing_chapter_refuses_manual_edit_until_model_finishes(self):
        detail = self.generate()
        chapter = detail["chapters"][0]
        entered, release = threading.Event(), threading.Event()

        def delayed_reply(_request):
            entered.set()
            release.wait(6)
            return self.model_reply()

        self.model_server.reply = delayed_reply
        submitted = self.ok("POST", "/api/chapters/run", {"document_id": detail["document"]["id"], "chapter_ids": [chapter["id"]], "mode": "model", "expected_revision": chapter["revision"], "instructions": "仅重整段落顺序。"})
        try:
            self.assertTrue(entered.wait(3))
            status, error = self.request("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "corpus": "不应覆盖正在生成的结果", "expected_revision": chapter["revision"]})
            self.assertEqual(status, 409, error)
        finally:
            release.set()
        completed = self.wait_job(submitted["job"])
        self.assertEqual(completed["status"], "completed", completed)
        refreshed = self.refresh(detail["document"]["id"])["chapters"][0]
        self.assertNotIn("不应覆盖", refreshed["corpus"])
        history = self.ok("GET", "/api/chapters/history/" + chapter["id"])
        self.assertFalse(any(row["event"] == "manual_revised" for row in history["history"]))

    def test_targeted_materialize_checks_current_chapter_and_never_includes_others(self):
        document = self.ok("POST", "/api/chapters/demo", {})["document"]
        document_id = document["id"]
        submitted = self.ok("POST", "/api/chapters/run", {"document_id": document_id, "mode": "demo"})
        self.assertEqual(self.wait_job(submitted["job"])["status"], "completed")
        detail = self.refresh(document_id)
        self.assertGreaterEqual(len(detail["chapters"]), 2)
        first, second = detail["chapters"][:2]
        for chapter in (first, second):
            self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "expected_revision": chapter["revision"]})

        first_output = self.server.chapters.materialize({
            "document_id": document_id, "chapter_id": first["id"], "expected_revision": first["revision"],
        })
        self.assertGreater(first_output["created"], 0)
        self.assertEqual(first_output["chapter_id"], first["id"])
        self.assertEqual(first_output["revision"], first["revision"])
        first_assets = self.ok("GET", "/api/state")["assets"]
        self.assertTrue(first_assets)
        self.assertEqual({asset["provenance"]["source_chapter_id"] for asset in first_assets}, {first["id"]})
        first_ids = {asset["id"] for asset in first_assets}

        revised = self.ok("POST", "/api/chapters/revise", {
            "chapter_id": second["id"], "expected_revision": second["revision"],
            "corpus": second["corpus"] + "\nMANUAL_TARGET_REVISION：需要重新审批。",
        })
        for extra in ({}, {"expected_revision": second["revision"]}, {"expected_revision": revised["revision"]}):
            with self.subTest(revision=extra):
                status, error = pipeline.legacy_materialize_response(self, {"document_id": document_id, "chapter_id": second["id"], **extra})
                self.assertEqual(status, 409, error)
                self.assertEqual({asset["id"] for asset in self.ok("GET", "/api/state")["assets"]}, first_ids)

        self.ok("POST", "/api/chapters/review", {"chapter_id": second["id"], "action": "approve", "expected_revision": revised["revision"]})
        payload = {"document_id": document_id, "chapter_id": second["id"], "expected_revision": revised["revision"]}
        second_output = self.server.chapters.materialize(payload)
        self.assertGreater(second_output["created"], 0)
        self.assertEqual(second_output["chapter_id"], second["id"])
        self.assertEqual(second_output["revision"], revised["revision"])
        self.assertEqual({asset["provenance"]["source_chapter_id"] for asset in second_output["assets"]}, {second["id"]})
        second_ids = {asset["id"] for asset in second_output["assets"]}
        self.assertTrue(second_ids.isdisjoint(first_ids))
        self.assertEqual({asset["id"] for asset in self.ok("GET", "/api/state")["assets"]}, first_ids | second_ids)
        repeated = self.server.chapters.materialize(payload)
        self.assertEqual(repeated["created"], 0)
        self.assertEqual({asset["id"] for asset in repeated["assets"]}, second_ids)

        foreign = self.import_package([("foreign/page_1.md", "# 独立文献\n\n这是另一篇资料，不能引用其他文献的章节。")])
        status, error = pipeline.legacy_materialize_response(self, {**payload, "document_id": foreign["document"]["id"]})
        self.assertEqual(status, 409, error)
        self.ok("POST", "/api/chapters/rebuild", {"document_id": document_id, "chapters": [{"title": "新章节", "page_ids": [page["id"] for page in detail["pages"]]}]})
        status, error = pipeline.legacy_materialize_response(self, payload)
        self.assertEqual(status, 409, error)
        self.assertEqual({asset["id"] for asset in self.ok("GET", "/api/state")["assets"]}, first_ids | second_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)

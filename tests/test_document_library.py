"""Three-level paper directory behavior, using isolated temporary workspaces."""

import base64
from pathlib import Path
import sqlite3
import unittest

import test_chapter_pipeline as pipeline


class DocumentLibraryTests(unittest.TestCase):
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

    def library(self):
        return self.ok("GET", "/api/library/state")

    def source(self, name="植物标本库", **changes):
        return self.ok("POST", "/api/library/sources", {"name": name, "kind": "local", **changes})["source"]

    def record(self, source, name="拟南芥", **changes):
        return self.ok("POST", "/api/library/records", {"source_id": source["id"], "name": name, "scientific_name": "Arabidopsis thaliana", **changes})["record"]

    def import_payload(self, record=None, **changes):
        return {
            "name": "文献目录导入.zip", "purpose": "both", "license": "self_owned",
            "content_base64": base64.b64encode(pipeline.zip_bytes([
                ("paper/page_1.md", "# 叶片观察\n\n拟南芥样本 A 叶片长度为 2.4 cm。"),
            ])).decode("ascii"),
            **({"record_id": record["id"]} if record else {}), **changes,
        }

    def assert_client_error(self, method, path, payload):
        status, result = self.request(method, path, payload)
        self.assertGreaterEqual(status, 400, result)
        self.assertLess(status, 500, result)
        return result

    def test_local_and_pending_api_sources_and_record_metadata_persist(self):
        source = self.source(description="标本及文献目录")
        self.assertEqual(source["status"], "ready")
        record = self.record(source, summary="拟南芥形态资料", metadata={"accession": "IB-A-001"})
        self.assertEqual(record["document_count"], 0)
        pending = self.source("未来科研接口", kind="api")
        self.assertEqual(pending["endpoint"], "")
        self.assertEqual(pending["status"], "pending")
        endpoint = self.start_model({})
        self.ok("POST", "/api/library/sources/" + pending["id"], {"endpoint": endpoint, "description": "仅登记，尚未对接"})
        self.assert_client_error("POST", "/api/library/sources/" + pending["id"], {"status": "ready"})
        self.ok("POST", "/api/library/records/" + record["id"], {"name": "拟南芥文献条目", "metadata": {"accession": "IB-A-002"}})
        self.stop_workshop()
        self.start_workshop()
        state = self.library()
        api = next(item for item in state["sources"] if item["id"] == pending["id"])
        self.assertEqual(api["endpoint"], endpoint)
        self.assertEqual(api["status"], "pending")
        self.assertEqual(self.model_server.requests, [])
        saved = self.ok("GET", "/api/library/records/" + record["id"])
        self.assertEqual(saved["record"]["name"], "拟南芥文献条目")
        self.assertEqual(saved["record"]["scientific_name"], "Arabidopsis thaliana")
        self.assertEqual(saved["record"]["metadata"], {"accession": "IB-A-002"})
        self.assertEqual(saved["source"]["id"], source["id"])
        self.assertEqual(saved["documents"], [])

    def test_one_paper_can_belong_to_multiple_records_without_duplicate_import(self):
        source = self.source()
        first = self.record(source)
        second = self.record(source, "十字花科叶形研究")
        imported = self.ok("POST", "/api/chapters/import", self.import_payload(first, source_id=source["id"]))
        document_id = imported["document"]["id"]
        repeated = self.ok("POST", "/api/chapters/import", self.import_payload(second, name="另一个名称的相同资料.zip"))
        self.assertEqual(repeated["document"]["id"], document_id)
        for _ in range(2):
            self.ok("POST", "/api/library/link", {"record_id": first["id"], "document_id": document_id, "source_id": source["id"]})
        state = self.library()
        self.assertEqual(len(state["documents"]), 1)
        links = [link for link in state["links"] if link["document_id"] == document_id]
        self.assertEqual({link["record_id"] for link in links}, {first["id"], second["id"]})
        self.assertEqual(len(links), 2)
        parent = next(item for item in state["sources"] if item["id"] == source["id"])
        self.assertEqual(parent["record_count"], 2)
        self.assertEqual(parent["document_count"], 1)
        for record in (first, second):
            detail = self.ok("GET", "/api/library/records/" + record["id"])
            self.assertEqual(detail["record"]["document_count"], 1)
            self.assertEqual([item["id"] for item in detail["documents"]], [document_id])
        document = self.ok("GET", "/api/chapters/documents/" + document_id)["document"]
        for container in (document, state["documents"][0], self.chapter_state()["documents"][0]):
            contexts = container["library_contexts"]
            self.assertEqual({context["record_id"] for context in contexts}, {first["id"], second["id"]})
            self.assertIn(container["library_context"], contexts)
            self.assertTrue(all(context["source"]["id"] == source["id"] and context["record"]["id"] == context["record_id"] for context in contexts))

    def test_invalid_associations_and_record_parent_changes_leave_directory_unchanged(self):
        source = self.source()
        other = self.source("另一来源")
        record = self.record(source)
        imported = self.ok("POST", "/api/chapters/import", self.import_payload(record))
        document_id = imported["document"]["id"]
        before = self.library()
        cases = [
            ("/api/library/records", {"source_id": "source_deadbeef", "name": "不应创建"}),
            ("/api/library/link", {"record_id": "record_deadbeef", "document_id": document_id}),
            ("/api/library/link", {"record_id": record["id"], "document_id": "document_deadbeef"}),
            ("/api/library/link", {"record_id": record["id"], "document_id": document_id, "source_id": other["id"]}),
            ("/api/library/records/" + record["id"], {"source_id": other["id"]}),
        ]
        for path, payload in cases:
            with self.subTest(path=path, payload=payload):
                self.assert_client_error("POST", path, payload)
                self.assertEqual(self.library(), before)

    def test_failed_import_cannot_create_or_link_a_document(self):
        source = self.source()
        other = self.source("不属于当前条目的来源")
        record = self.record(source)
        before = self.library()
        bad_payloads = [
            self.import_payload(record, content_base64="invalid base64!!"),
            self.import_payload(record, content_base64=base64.b64encode(pipeline.zip_bytes([("../escaped.md", "# 不应导入")])).decode("ascii")),
            self.import_payload(record, record_id="record_deadbeef"),
            self.import_payload(record, source_id="source_deadbeef"),
            self.import_payload(record, source_id=other["id"]),
        ]
        for payload in bad_payloads:
            with self.subTest(payload_keys=list(payload)):
                self.assert_client_error("POST", "/api/chapters/import", payload)
                self.assertEqual(self.library(), before)
                self.assertEqual(list((Path(self.directory.name) / "chapter_packages").rglob("*.md")), [])
        imported = self.ok("POST", "/api/chapters/import", self.import_payload(record))
        valid_state = self.library()
        self.assertEqual(len(valid_state["documents"]), 1)
        self.assertEqual(len(valid_state["links"]), 1)
        self.assertEqual(valid_state["links"][0]["document_id"], imported["document"]["id"])
        self.assert_client_error("POST", "/api/chapters/import", self.import_payload(record, record_id="record_deadbeef"))
        self.assertEqual(self.library(), valid_state)

    def test_unlink_changes_only_directory_membership_and_counts(self):
        source = self.source()
        first = self.record(source)
        second = self.record(source, "保留的第二条目")
        imported = self.ok("POST", "/api/chapters/import", self.import_payload(first))
        document_id = imported["document"]["id"]
        self.ok("POST", "/api/library/link", {"record_id": second["id"], "document_id": document_id})
        detail = self.ok("GET", "/api/chapters/documents/" + document_id)
        self.ok("POST", "/api/library/unlink", {"record_id": first["id"], "document_id": document_id})
        after = self.ok("GET", "/api/chapters/documents/" + document_id)
        self.assertEqual(after["pages"], detail["pages"])
        self.assertEqual(after["chapters"], detail["chapters"])
        self.assertEqual([context["record_id"] for context in after["document"]["library_contexts"]], [second["id"]])
        self.assertEqual(self.ok("GET", "/api/library/records/" + first["id"])["record"]["document_count"], 0)
        self.assertEqual(self.ok("GET", "/api/library/records/" + second["id"])["record"]["document_count"], 1)
        self.assertEqual(len(self.library()["documents"]), 1)

    def test_legacy_migration_is_idempotent_and_preserves_pages_revisions_and_history(self):
        demo = self.ok("POST", "/api/chapters/demo", {})
        document_id = demo["document"]["id"]
        detail = self.ok("GET", "/api/chapters/documents/" + document_id)
        self.assertEqual(self.wait_job(self.run_chapter(detail, mode="demo"))["status"], "completed")
        detail = self.ok("GET", "/api/chapters/documents/" + document_id)
        chapter = detail["chapters"][0]
        self.ok("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "expected_revision": chapter["revision"], "corpus": chapter["corpus"] + "\n\n人工审核备注：供迁移回归验证。"})
        before = self.ok("GET", "/api/chapters/documents/" + document_id)
        history = self.ok("GET", "/api/chapters/history/" + chapter["id"])
        ordinary = self.import_package()
        real = self.ok("POST", "/api/chapters/real-demo", {})
        real_detail = self.ok("GET", "/api/chapters/documents/" + real["document"]["id"])
        legacy_documents = {item["document"]["id"]: item for item in (before, ordinary, real_detail)}
        package_root = Path(self.directory.name) / "chapter_packages"
        originals = {path.relative_to(package_root).as_posix(): path.read_bytes() for path in package_root.rglob("*") if path.is_file()}
        database = self.server.workshop.db_path
        self.stop_workshop()
        with sqlite3.connect(database) as database_connection:
            for table in ("dl_links", "dl_records", "dl_sources"):
                database_connection.execute("DROP TABLE " + table)
        self.start_workshop()
        migrated = self.library()
        contexts = self.ok("GET", "/api/chapters/documents/" + document_id)["document"]["library_contexts"]
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["source_id"], "source_plant_research")
        self.assertIn("拟南芥", contexts[0]["record"]["name"])
        ordinary_contexts = self.ok("GET", "/api/chapters/documents/" + ordinary["document"]["id"])["document"]["library_contexts"]
        self.assertEqual(len(ordinary_contexts), 1)
        self.assertIn("本地", ordinary_contexts[0]["source"]["name"])
        self.assertIn("待归类", ordinary_contexts[0]["record"]["name"])
        real_contexts = self.ok("GET", "/api/chapters/documents/" + real["document"]["id"])["document"]["library_contexts"]
        self.assertEqual(len(real_contexts), 1)
        self.assertEqual(real_contexts[0]["source_id"], "source_open_plants")
        self.assertIn("Pothos", real_contexts[0]["record"]["name"])
        self.stop_workshop()
        self.start_workshop()
        self.assertEqual(self.library(), migrated)
        for paper_id, original in legacy_documents.items():
            after = self.ok("GET", "/api/chapters/documents/" + paper_id)
            for key in ("pages", "chapters", "pdfs", "selected_pdf"):
                self.assertEqual(after[key], original[key])
        self.assertEqual(self.ok("GET", "/api/chapters/history/" + chapter["id"]), history)
        self.assertEqual({path.relative_to(package_root).as_posix(): path.read_bytes() for path in package_root.rglob("*") if path.is_file()}, originals)


if __name__ == "__main__":
    unittest.main(verbosity=2)

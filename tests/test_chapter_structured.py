"""Custom extraction schemas, independent interpretation and offline artifacts.

Models are local HTTP mocks. All workspaces are disposable temporary folders.
"""

import copy
import io
import json
import math
from pathlib import PurePosixPath
import posixpath
import re
import threading
import unittest
import zipfile

import test_chapter_pipeline as pipeline


SCHEMA = [
    {"key": "leaf_length", "label": "叶片长度", "type": "number", "description": "提取记录中的长度数值，单位为 cm。"},
    {"key": "species", "label": "物种", "type": "text", "description": "只采用原文中的物种名称。"},
    {"key": "has_control", "label": "是否设对照组", "type": "boolean", "description": "必须有明确原文证据。"},
    {"key": "sample_size", "label": "样本量", "type": "number", "description": "未提及则保留 null。"},
]


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


class ChapterStructuredTests(unittest.TestCase):
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

    def import_structured_paper(self):
        return self.import_package([
            ("paper/pages/page_1.md", "# 材料与方法\n\n拟南芥样本 A 叶片长度为 2.4 cm。\n\n本记录未设置对照组。"),
            ("paper/pages/page_2.md", "同一样本叶片宽度为 1.1 cm。\n\n![测试色块示意](../images/leaf.png)"),
            ("paper/images/leaf.png", pipeline.PNG_BYTES),
        ])

    def refresh(self, document_id):
        return self.ok("GET", "/api/chapters/documents/" + document_id)

    def set_schema(self, detail, schema=None, expected=None):
        payload = {"extraction_schema": copy.deepcopy(SCHEMA if schema is None else schema)}
        if expected is not None:
            payload["expected_schema_revision"] = expected
        self.ok("POST", "/api/chapters/documents/" + detail["document"]["id"], payload)
        return self.refresh(detail["document"]["id"])

    def structured_reply(self, detail):
        reply = self.model_reply()
        image_id = detail["pages"][1]["images"][0]["id"]
        reply["figures"] = [{"image_id": image_id, "caption": "测试色块示意", "interpretation": "该色块仅用于检查图像链路，不能推断真实植物形态。", "source_pages": [2]}]
        reply["interpretation"] = {"text": "ANALYSIS_ONLY_MARKER：现有记录不能支持处理组间差异结论。", "source_pages": [1, 2]}
        reply["structured_fields"] = [
            {"key": "leaf_length", "value": 2.4, "status": "found", "evidence": "拟南芥样本 A 叶片长度为 2.4 cm。", "source_pages": [1], "image_ids": []},
            {"key": "species", "value": "拟南芥", "status": "found", "evidence": "拟南芥样本 A 叶片长度为 2.4 cm。", "source_pages": [1], "image_ids": []},
            {"key": "has_control", "value": False, "status": "found", "evidence": "本记录未设置对照组。", "source_pages": [1], "image_ids": []},
        ]
        return reply

    def generate_structured(self):
        detail = self.set_schema(self.import_structured_paper(), expected=0)
        self.configure_model(self.structured_reply(detail))
        job = self.wait_job(self.run_chapter(detail))
        self.assertEqual(job["status"], "completed", job)
        return self.refresh(detail["document"]["id"])

    def test_schema_persists_and_rejects_invalid_definitions_and_stale_changes(self):
        detail = self.import_structured_paper()
        self.assertEqual(detail["document"]["schema_revision"], 0)
        configured = self.set_schema(detail, expected=0)
        self.assertEqual(configured["document"]["schema_revision"], 1)
        self.assertEqual(configured["document"]["extraction_schema"], SCHEMA)
        unchanged = self.set_schema(configured, expected=1)
        self.assertEqual(unchanged["document"]["schema_revision"], 1)
        self.stop_workshop()
        self.start_workshop()
        persisted = self.refresh(detail["document"]["id"])
        self.assertEqual(persisted["document"]["extraction_schema"], SCHEMA)
        self.assertEqual(persisted["document"]["schema_revision"], 1)
        invalid_schemas = [
            [{"key": "nested.field", "label": "非法键", "type": "text", "description": ""}],
            [{"key": "length", "label": "非法类型", "type": "object", "description": ""}],
            [SCHEMA[0], SCHEMA[0]],
        ]
        for schema in invalid_schemas:
            with self.subTest(schema=schema):
                status, error = self.request("POST", "/api/chapters/documents/" + detail["document"]["id"], {"extraction_schema": schema, "expected_schema_revision": 1})
                self.assertGreaterEqual(status, 400, error)
                self.assertLess(status, 500, error)
        status, error = self.request("POST", "/api/chapters/documents/" + detail["document"]["id"], {"extraction_schema": [], "expected_schema_revision": 0})
        self.assertEqual(status, 409, error)
        self.assertEqual(self.refresh(detail["document"]["id"])["document"]["extraction_schema"], SCHEMA)

    def test_model_receives_schema_and_unmentioned_field_is_null(self):
        detail = self.generate_structured()
        result = detail["chapters"][0]["result"]
        self.assertEqual(result["schema_revision"], 1)
        self.assertEqual(result["extraction_schema"], SCHEMA)
        fields = {field["key"]: field for field in result["structured_fields"]}
        self.assertEqual(set(fields), {field["key"] for field in SCHEMA})
        self.assertEqual(fields["leaf_length"]["value"], 2.4)
        self.assertIs(fields["has_control"]["value"], False)
        self.assertEqual(fields["sample_size"]["status"], "not_found")
        self.assertIsNone(fields["sample_size"]["value"])
        self.assertFalse(fields["sample_size"]["evidence"])
        self.assertEqual(fields["sample_size"]["source_pages"], [])
        self.assertEqual(fields["sample_size"]["image_ids"], [])
        prompt = "\n".join(strings_in(self.model_server.requests[0]["body"]["messages"]))
        for field in SCHEMA:
            self.assertIn(field["key"], prompt)
            self.assertIn(field["label"], prompt)
            self.assertIn(field["description"], prompt)
        self.assertIn("ANALYSIS_ONLY_MARKER", result["interpretation"]["text"])
        self.assertNotIn("ANALYSIS_ONLY_MARKER", detail["chapters"][0]["corpus"])
        chapter = detail["chapters"][0]
        self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "expected_revision": chapter["revision"]})
        self.server.chapters.materialize({"document_id": detail["document"]["id"], "chapter_id": chapter["id"], "expected_revision": chapter["revision"]})
        preview = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})
        self.assertTrue(preview["samples"])
        self.assertTrue(all("ANALYSIS_ONLY_MARKER" not in sample["text"] for sample in preview["samples"]))

    def test_invalid_field_values_evidence_and_source_references_are_rejected(self):
        detail = self.set_schema(self.import_structured_paper(), expected=0)
        base = self.structured_reply(detail)
        self.configure_model(base)
        image_id = detail["pages"][1]["images"][0]["id"]
        cases = {
            "unknown_key": {"key": "undeclared_fact"},
            "wrong_type": {"value": "2.4"},
            "boolean_is_not_number": {"value": True},
            "non_finite_number": {"value": math.nan},
            "missing_evidence": {"evidence": ""},
            "fabricated_quote": {"evidence": "原页完全没有这段句子。"},
            "foreign_page": {"source_pages": [999]},
            "fake_image": {"image_ids": ["image_not_in_this_paper"]},
            "image_page_mismatch": {"image_ids": [image_id], "source_pages": [1]},
            "not_found_with_value": {"status": "not_found", "value": 2.4, "evidence": "", "source_pages": [], "image_ids": []},
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                reply = copy.deepcopy(base)
                reply["structured_fields"][0].update(change)
                self.model_server.reply = reply
                job = self.wait_job(self.run_chapter(detail))
                self.assertEqual(job["status"], "error", job)
                chapter = self.refresh(detail["document"]["id"])["chapters"][0]
                self.assertFalse(chapter["result"], chapter)
        self.assertEqual(self.ok("GET", "/api/state")["assets"], [])

    def test_inflight_job_uses_schema_snapshot_after_template_changes(self):
        detail = self.set_schema(self.import_structured_paper(), expected=0)
        entered, release = threading.Event(), threading.Event()

        def delayed_reply(_request):
            entered.set()
            release.wait(6)
            return self.structured_reply(detail)

        self.configure_model(delayed_reply)
        submitted = self.ok("POST", "/api/chapters/run", {"document_id": detail["document"]["id"], "chapter_ids": [detail["chapters"][0]["id"]], "mode": "model", "expected_schema_revision": 1})
        try:
            self.assertTrue(entered.wait(3))
            updated_schema = copy.deepcopy(SCHEMA)
            updated_schema[0]["type"] = "text"
            updated = self.set_schema(detail, updated_schema, expected=1)
            self.assertEqual(updated["document"]["schema_revision"], 2)
        finally:
            release.set()
        job = self.wait_job(submitted["job"])
        self.assertEqual(job["status"], "completed", job)
        result = self.refresh(detail["document"]["id"])["chapters"][0]["result"]
        self.assertEqual(result["schema_revision"], 1)
        self.assertEqual(result["extraction_schema"], SCHEMA)
        self.assertEqual(next(field for field in result["structured_fields"] if field["key"] == "leaf_length")["value"], 2.4)
        status, error = self.request("POST", "/api/chapters/run", {"document_id": detail["document"]["id"], "chapter_ids": [detail["chapters"][0]["id"]], "mode": "model", "expected_schema_revision": 1})
        self.assertEqual(status, 409, error)
        self.assertEqual(len(self.model_server.requests), 1)

    def test_independent_products_can_be_revised_with_original_schema_snapshot(self):
        detail = self.generate_structured()
        chapter = detail["chapters"][0]
        self.assertTrue(chapter["result"]["sft_samples"])
        self.set_schema(detail, [{"key": "new_height", "label": "新的高度字段", "type": "number", "description": "下一次模型加工使用。"}], expected=1)
        interpretation = {"text": "人工解读：单个记录不足以推断总体差异。", "source_pages": [1, 2]}
        fields = copy.deepcopy(chapter["result"]["structured_fields"])
        saved = self.ok("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "expected_revision": chapter["revision"], "interpretation": interpretation, "structured_fields": fields})
        self.assertEqual(saved["revision"], chapter["revision"] + 1)
        self.assertEqual(saved["status"], "review")
        self.assertEqual(saved["corpus"], chapter["corpus"])
        self.assertEqual(saved["result"]["interpretation"]["text"], interpretation["text"])
        self.assertEqual(saved["result"]["interpretation"]["source_pages"], interpretation["source_pages"])
        self.assertEqual(saved["result"]["structured_fields"], fields)
        self.assertEqual(saved["result"]["schema_revision"], 1)
        self.assertEqual(saved["result"]["extraction_schema"], SCHEMA)
        self.assertEqual(saved["result"]["sft_samples"], [])
        status, error = self.request("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "expected_revision": chapter["revision"], "interpretation": interpretation})
        self.assertEqual(status, 409, error)
        status, error = self.request("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "expected_revision": saved["revision"], "structured_fields": [{"key": "new_height", "value": None, "status": "not_found", "evidence": "", "source_pages": [], "image_ids": []}]})
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)
        self.assertEqual(self.refresh(detail["document"]["id"])["chapters"][0]["revision"], saved["revision"])

    def test_corpus_zip_contains_markdown_json_and_resolvable_offline_images(self):
        detail = self.generate_structured()
        chapter = detail["chapters"][0]
        self.assertNotIn("ANALYSIS_ONLY_MARKER", chapter["corpus"])
        self.assertNotIn("ANALYSIS_ONLY_MARKER", chapter["result"]["corpus"])
        self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "expected_revision": chapter["revision"]})
        status, data = self.request("GET", f"/api/chapters/download/{detail['document']['id']}?stage=corpus")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            md_name = f"chapters/{chapter['id']}.md"
            json_name = f"chapters/{chapter['id']}.json"
            self.assertTrue({md_name, json_name, "corpus.jsonl"}.issubset(names), names)
            markdown = archive.read(md_name).decode("utf-8")
            factual_markdown, separator, interpretation = markdown.partition("章节解读（派生解释，非原文事实）")
            self.assertTrue(separator, markdown)
            self.assertNotIn("ANALYSIS_ONLY_MARKER", factual_markdown)
            self.assertIn("ANALYSIS_ONLY_MARKER", interpretation)
            self.assertIn("来源页", interpretation)
            self.assertIn("local-contract-test", interpretation)
            links = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", markdown)
            self.assertTrue(links, markdown)
            for link in links:
                self.assertFalse(link.startswith(("http:", "https:", "/", "data:")), link)
                resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(md_name).parent), link))
                self.assertFalse(resolved.startswith("../"), resolved)
                self.assertIn(resolved, names)
                self.assertEqual(archive.read(resolved), pipeline.PNG_BYTES)
            record = json.loads(archive.read(json_name))
            self.assertIn("ANALYSIS_ONLY_MARKER", json.dumps(record))
            self.assertTrue(record["images"])
            for image in record["images"]:
                resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(json_name).parent), image["path"]))
                self.assertIn(resolved, names)
                self.assertEqual(archive.read(resolved), pipeline.PNG_BYTES)
            for file_name in (json_name, "corpus.jsonl"):
                records = [json.loads(line) for line in archive.read(file_name).decode().splitlines() if line.strip()] if file_name.endswith(".jsonl") else [record]
                for item in records:
                    self.assertNotIn("ANALYSIS_ONLY_MARKER", item["corpus"])
                    self.assertNotIn("ANALYSIS_ONLY_MARKER", item["result"]["corpus"])
                    for text in strings_in(item):
                        self.assertNotIn("/api/chapters/images/", text)
                        for link in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
                            resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(file_name).parent), link))
                            self.assertIn(resolved, names)
        self.server.chapters.materialize({"document_id": detail["document"]["id"], "chapter_id": chapter["id"], "expected_revision": chapter["revision"]})
        preview = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})
        self.assertTrue(preview["samples"])
        self.assertTrue(all("ANALYSIS_ONLY_MARKER" not in sample["text"] for sample in preview["samples"]))
        kb = self.server.workshop.publish_kb({"source": "chapters", "name": "解读隔离检查", "chunk_size": 500})
        status, kb_bytes = self.request("GET", kb["download_url"])
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(kb_bytes)) as archive:
            chunks = [json.loads(line) for line in archive.read("chunks.jsonl").decode().splitlines()]
        self.assertTrue(chunks)
        self.assertTrue(all("ANALYSIS_ONLY_MARKER" not in chunk["text"] for chunk in chunks))
        unsafe_markdown = (
            "![外部图片](https://example.invalid/image.png)",
            "<script>alert('unexpected')</script>",
            "![图][remote]\n[remote]: https://example.invalid/image.png",
            "![remote]\n[remote]: https://example.invalid/image.png",
        )
        for unsafe in unsafe_markdown:
            for product in ("corpus", "interpretation"):
                with self.subTest(product=product, markdown=unsafe):
                    update = chapter["corpus"] + "\n\n" + unsafe if product == "corpus" else {"text": unsafe, "source_pages": [1, 2]}
                    status, error = self.request("POST", "/api/chapters/revise", {"chapter_id": chapter["id"], "expected_revision": chapter["revision"], product: update})
                    self.assertGreaterEqual(status, 400, error)
                    self.assertLess(status, 500, error)
        unchanged = self.refresh(detail["document"]["id"])["chapters"][0]
        self.assertEqual(unchanged["revision"], chapter["revision"])
        self.assertEqual(unchanged["status"], "approved")

    def test_real_paper_example_is_non_synthetic_and_does_not_claim_model_execution(self):
        created = self.ok("POST", "/api/chapters/real-demo", {})
        document_id = created["document"]["id"]
        detail = self.refresh(document_id)
        self.assertFalse(detail["document"]["is_demo"])
        self.assertTrue(detail["pages"])
        self.assertTrue(detail["pdfs"])
        self.assertTrue(any(page["images"] for page in detail["pages"]))
        source = detail["document"]["source_metadata"]
        self.assertEqual(source["doi"], "10.3897/phytokeys.247.130721")
        self.assertEqual(source["pdf_sha256"], detail["pdfs"][0]["sha256"])
        self.assertTrue(source["license_url"].startswith("https://creativecommons.org/licenses/by/"))
        self.assertEqual(self.chapter_state()["jobs"], [])
        prepared = [chapter for chapter in detail["chapters"] if chapter["result"]]
        self.assertTrue(prepared)
        for chapter in prepared:
            result = chapter["result"]
            self.assertEqual(chapter["status"], "review")
            self.assertFalse(result["is_demo"])
            self.assertTrue(result["prepared_example"])
            self.assertEqual(result["processing_origin"], "codex_prepared_example")
            self.assertEqual(result["model"], "Codex preparation")
            self.assertFalse(result["platform_model_run"])
            self.assertTrue(result["facts"] or result["tables"] or result["figures"])
            fields = {field["key"]: field for field in result["structured_fields"]}
            self.assertEqual(fields["chromosome_count"]["status"], "not_found")
            self.assertIsNone(fields["chromosome_count"]["value"])
        self.assertEqual(self.ok("GET", "/api/state")["assets"], [])
        repeated = self.ok("POST", "/api/chapters/real-demo", {})
        self.assertEqual(repeated["document"]["id"], document_id)
        self.assertEqual(self.chapter_state()["jobs"], [])
        for chapter in prepared:
            self.ok("POST", "/api/chapters/review", {"chapter_id": chapter["id"], "action": "approve", "expected_revision": chapter["revision"]})
        status, data = self.request("GET", f"/api/chapters/download/{document_id}?stage=corpus")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for chapter in prepared:
                markdown = archive.read(f"chapters/{chapter['id']}.md").decode("utf-8")
                self.assertIn("## 文献来源与许可", markdown)
                self.assertIn(source["doi"], markdown)
                self.assertIn("CC BY 4.0", markdown)
                for key in ("citation", "url", "pdf_url", "license_url", "license_notice", "processing_note"):
                    self.assertIn(source[key], markdown)
                self.assertIn("Codex 预置解读（平台未执行模型任务）", markdown)
        materialized = self.server.chapters.materialize({"document_id": document_id})
        self.assertTrue(materialized["assets"])
        preview = self.ok("POST", "/api/training/preview", {"type": "pretrain", "source": "chapters"})
        self.assertTrue(preview["samples"])
        for sample in preview["samples"]:
            metadata = sample["metadata"]
            self.assertFalse(metadata["is_demo"])
            self.assertEqual(metadata["content_origin"], "codex_prepared_example")
            self.assertEqual(metadata["source_document_id"], document_id)
            self.assertEqual(metadata["provenance"]["mode"], "prepared_example")
            self.assertEqual(metadata["provenance"]["source_metadata"]["doi"], source["doi"])
        exports = [
            self.server.workshop.training_export({"type": "pretrain", "source": "chapters", "name": "真实论文预置语料"}),
            self.server.workshop.publish_kb({"source": "chapters", "name": "真实论文预置知识库", "chunk_size": 500}),
        ]
        for exported in exports:
            status, data = self.request("GET", exported["download_url"])
            self.assertEqual(status, 200)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertFalse(manifest["details"]["platform_model_run"])
                self.assertTrue(manifest["details"]["ai_generated"])
                if "chunks.jsonl" in archive.namelist():
                    chunks = [json.loads(line) for line in archive.read("chunks.jsonl").decode().splitlines()]
                    self.assertTrue(chunks)
                    self.assertTrue(all(chunk["provenance"]["mode"] == "prepared_example" for chunk in chunks))
        self.assertEqual(self.chapter_state()["jobs"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

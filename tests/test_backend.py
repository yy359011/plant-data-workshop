"""Behavioral API tests for the local botanical data workshop.

Each test runs an HTTP server against a fresh temporary directory. Tests never
open or modify the application's normal demonstration database.

Run from the project root: python3 -m unittest discover -s tests -p 'test_backend.py'
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
SPEC = importlib.util.spec_from_file_location(
    "plant_workshop_server", PROJECT_ROOT / "backend" / "server.py"
)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)


class BackendBehaviorTests(unittest.TestCase):
    """Exercise public HTTP behavior instead of duplicating implementation."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="plant-workshop-test-")
        self.start_server()

    def start_server(self, seed=False, data_dir=None):
        self.server = SERVER.create_server(
            host="127.0.0.1", port=0, data_dir=data_dir or self.directory.name, seed=seed
        )
        credentials = {"username": "regression_admin", "display_name": "隔离回归管理员", "password": "local-regression-only-8294"}
        session = self.server.platform.setup(credentials) if self.server.platform.bootstrap()["needs_setup"] else self.server.platform.login(credentials)
        self.test_cookie = session["_cookie"].split(";")[0]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def tearDown(self):
        self.stop_server()
        self.directory.cleanup()

    def request(self, method, path, payload=None, raw=None, headers=None):
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body = raw
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Cookie": self.test_cookie, **(headers or {})},
        )
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                content = json.loads(content)
            return response.status, content, response.headers

    def assert_ok(self, method, path, payload=None):
        status, content, _ = self.request(method, path, payload)
        self.assertGreaterEqual(status, 200, content)
        self.assertLess(status, 300, content)
        return content

    def import_document(
        self, name, text, *, license="self_owned", purpose="both", process=True
    ):
        content = text if isinstance(text, bytes) else text.encode("utf-8")
        self.assert_ok(
            "POST",
            "/api/import",
            {
                "files": [
                    {
                        "name": name,
                        "content_base64": base64.b64encode(content).decode("ascii"),
                    }
                ],
                "license": license,
                "purpose": purpose,
                "process": process,
            },
        )
        state = self.assert_ok("GET", "/api/state")
        matches = [asset for asset in state["assets"] if asset["name"] == name]
        self.assertEqual(len(matches), 1, state)
        return matches[0]

    def training_preview(self, kind="pretrain"):
        return self.assert_ok(
            "POST", "/api/training/preview", {"type": kind, "name": "测试训练集"}
        )

    def publish_kb(self, name="植物学测试知识库"):
        # Historical serializer regression: public exports now require an
        # accepted batch and are exercised in test_platform_data.py.
        return self.server.workshop.publish_kb({"name": name, "chunk_size": 500})

    def legacy_output(self, path, body):
        """Preserve serializer/permission unit coverage behind disabled routes."""
        try:
            method = self.server.workshop.publish_kb if path == "/api/kb/publish" else self.server.workshop.training_export
            return 200, method(body), {}
        except SERVER.APIError as error:
            return error.status, {"error": str(error)}, {}

    def search_kb(self, query, version_id=None):
        payload = {"query": query}
        if version_id:
            payload["version_id"] = version_id
        return self.assert_ok("POST", "/api/kb/search", payload)

    def export_training(self, kind="pretrain"):
        result = self.server.workshop.training_export({"type": kind, "name": "植物数据导出测试"})
        self.assertTrue(result.get("version"), result)
        self.assertTrue(result.get("download_url"), result)
        status, archive, headers = self.request("GET", result["download_url"])
        self.assertEqual(status, 200, archive)
        self.assertIn("zip", headers.get("Content-Type", ""))
        return zipfile.ZipFile(io.BytesIO(archive))

    def read_export_records(self, archive):
        names = archive.namelist()
        for required in ("manifest.json", "train.jsonl", "validation.jsonl", "test.jsonl"):
            self.assertIn(required, names)
        manifest = json.loads(archive.read("manifest.json"))
        self.assertIsInstance(manifest, dict)
        records = {}
        for split in ("train", "validation", "test"):
            text = archive.read(split + ".jsonl").decode("utf-8")
            records[split] = [json.loads(line) for line in text.splitlines() if line.strip()]
        return records

    def test_csv_and_utf8_text_retain_botanical_measurements(self):
        source_csv = (
            "物种,处理,净光合速率,单位\n"
            "拟南芥,正常供水,12.7,μmol CO₂ m⁻² s⁻¹\n"
            "水稻,干旱处理,8.3,μmol CO₂ m⁻² s⁻¹\n"
        )
        asset = self.import_document("光合作用测量.csv", source_csv)
        detail = self.assert_ok("GET", f"/api/assets/{asset['id']}")
        block_text = "\n".join(block["text"] for block in detail["blocks"])
        for expected in ("拟南芥", "水稻", "12.7", "8.3", "μmol"):
            self.assertIn(expected, block_text)
        status, original, _ = self.request("GET", f"/api/files/{asset['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(original, source_csv.encode("utf-8"))

        text = "拟南芥种子萌发观察。\n\n所有数值为演示数据，不代表真实实验结论。"
        text_asset = self.import_document("种子萌发.txt", text)
        detail = self.assert_ok("GET", f"/api/assets/{text_asset['id']}")
        self.assertIn("拟南芥种子萌发", detail["raw_text"])
        self.assertGreater(len(detail["blocks"]), 0)

    def test_unknown_license_cannot_enter_training_or_kb(self):
        self.import_document(
            "用途未知文献.txt", "石斛专属标记甲乙丙。该资料尚未核实授权。", license="unknown"
        )
        preview = self.training_preview()
        self.assertEqual(preview["total"], 0, preview)
        status, error, _ = self.legacy_output(
            "/api/training/export", {"type": "pretrain", "name": "不应导出"}
        )
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)
        status, error, _ = self.legacy_output(
            "/api/kb/publish", {"name": "不应发布", "chunk_size": 500}
        )
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)

    def test_research_only_license_is_excluded_from_training(self):
        self.import_document(
            "限制用途材料.txt",
            "蕨类植物孢子叶具有特定形态。测试标记云杉五号。",
            license="research_only",
        )
        preview = self.training_preview()
        self.assertEqual(preview["total"], 0, preview)
        self.publish_kb()
        search = self.search_kb("蕨类植物")
        self.assertGreater(len(search["results"]), 0, search)
        self.assertIn("蕨类植物", search["results"][0]["text"])

    def test_chinese_search_returns_citable_source(self):
        asset = self.import_document(
            "叶绿素提取流程.md",
            "# 叶绿素测定\n\n使用乙醇提取叶片中的叶绿素，避光保存提取液。\n\n该流程为虚构演示。",
        )
        self.publish_kb()
        search = self.search_kb("叶绿素")
        self.assertGreater(len(search["results"]), 0, search)
        result = search["results"][0]
        self.assertEqual(result["asset_id"], asset["id"])
        self.assertEqual(result["asset_name"], "叶绿素提取流程.md")
        self.assertIn("叶绿素", result["text"])
        self.assertTrue(result["location"])
        self.assertGreater(result["score"], 0)

    def test_invalid_json_is_a_clear_client_error(self):
        status, content, _ = self.request("POST", "/api/import", raw=b"{not valid json")
        self.assertGreaterEqual(status, 400, content)
        self.assertLess(status, 500, content)
        self.assertIsInstance(content, dict)
        self.assertTrue(content.get("error") or content.get("message"), content)

    def test_invalid_base64_does_not_create_assets(self):
        status, content, _ = self.request(
            "POST",
            "/api/import",
            {
                "files": [{"name": "坏文件.txt", "content_base64": "not@base64!!!"}],
                "purpose": "both",
                "license": "self_owned",
                "process": True,
            },
        )
        self.assertGreaterEqual(status, 400, content)
        self.assertLess(status, 500, content)
        self.assertEqual(self.assert_ok("GET", "/api/state")["assets"], [])

    def test_file_route_refuses_path_traversal(self):
        secret = Path(self.directory.name) / "outside-secret.txt"
        secret.write_text("SHOULD_NEVER_BE_SERVED_8029", encoding="utf-8")
        for suffix in (
            "../outside-secret.txt",
            "%2e%2e%2foutside-secret.txt",
            "%2Fetc%2Fpasswd",
        ):
            with self.subTest(path=suffix):
                status, content, _ = self.request("GET", "/api/files/" + suffix)
                self.assertGreaterEqual(status, 400, content)
                self.assertNotIn("SHOULD_NEVER_BE_SERVED_8029", str(content))

    def test_asset_purpose_excludes_training_only_source_from_kb(self):
        self.import_document(
            "训练专用.txt", "拟南芥训练专用记录。禁止进入知识库。", purpose="training"
        )
        self.assertGreater(self.training_preview()["total"], 0)
        status, error, _ = self.legacy_output(
            "/api/kb/publish", {"name": "空知识库", "chunk_size": 500}
        )
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)

    def test_sft_jsonl_preserves_supplied_conversation(self):
        messages = [
            {"role": "user", "content": "记录中拟南芥样本的盆土质量是多少？"},
            {"role": "assistant", "content": "记录中的盆土质量为 182.4 g；这是合成演示。"},
        ]
        source = json.dumps({"messages": messages}, ensure_ascii=False) + "\n"
        self.import_document("人工标注植物问答.jsonl", source, purpose="training")
        preview = self.training_preview("sft")
        self.assertEqual(preview["total"], 1, preview)
        with self.export_training("sft") as archive:
            exported = self.read_export_records(archive)
        records = [record for split in exported.values() for record in split]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["messages"], messages)

    def test_export_splits_keep_each_document_together(self):
        identifiers = [f"BOTANY_SOURCE_{index:02d}" for index in range(12)]
        for index, identifier in enumerate(identifiers):
            text = "\n\n".join(
                f"{identifier} 第 {part} 条植物形态观察：本段是独立的合成演示记录。"
                for part in range(4)
            )
            self.import_document(f"来源分组_{index:02d}.txt", text, purpose="training")
        with self.export_training() as archive:
            exported = self.read_export_records(archive)
        observed_splits = {identifier: set() for identifier in identifiers}
        count = 0
        for split, records in exported.items():
            for record in records:
                self.assertIsInstance(record.get("text"), str, record)
                count += 1
                for identifier in identifiers:
                    if identifier in record["text"]:
                        observed_splits[identifier].add(split)
        self.assertGreater(count, 0)
        for identifier, splits in observed_splits.items():
            self.assertEqual(len(splits), 1, (identifier, splits))

    def test_published_kb_version_is_an_immutable_corpus_snapshot(self):
        first_asset = self.import_document(
            "初版叶绿素资料.txt", "叶绿素初版资料：本段属于首次发布的资料。"
        )
        self.publish_kb("初版")
        first_search = self.search_kb("叶绿素")
        first_version = first_search["version_id"]
        first_results = first_search["results"]
        self.assertTrue(first_version)
        self.assertTrue(first_results)

        second_asset = self.import_document(
            "增量叶绿素资料.txt", "叶绿素增量资料：这份文档只在第二次发布时存在。"
        )
        self.publish_kb("增量版本")
        current = self.search_kb("叶绿素")
        self.assertNotEqual(current["version_id"], first_version)
        self.assertIn(second_asset["id"], {item["asset_id"] for item in current["results"]})

        historical = self.search_kb("叶绿素", version_id=first_version)
        self.assertEqual(historical["version_id"], first_version)
        self.assertEqual(historical["results"], first_results)
        self.assertEqual(
            {item["asset_id"] for item in historical["results"]}, {first_asset["id"]}
        )

    def test_unlabelled_text_does_not_become_fabricated_sft(self):
        self.import_document(
            "未标注正文.txt", "拟南芥是一种植物学演示材料。此原文没有任何问答标注。"
        )
        preview = self.training_preview("sft")
        self.assertEqual(preview["total"], 0, preview)

    def test_oversized_request_is_rejected_before_reading_entire_body(self):
        status, content, _ = self.request(
            "POST", "/api/import", raw=b"{}", headers={"Content-Length": "1000000000"}
        )
        self.assertEqual(status, 413, content)

    def test_assets_and_published_kb_survive_server_restart(self):
        asset = self.import_document(
            "持久化植物记录.txt", "苔藓植物持久化测试记录。服务重启后该资料应保留。"
        )
        self.publish_kb("重启前发布")
        original = self.search_kb("苔藓")
        self.stop_server()
        self.start_server()
        state = self.assert_ok("GET", "/api/state")
        self.assertIn(asset["id"], {item["id"] for item in state["assets"]})
        restored = self.search_kb("苔藓", original["version_id"])
        self.assertEqual(restored, original)

    def test_review_correction_preserves_original_and_historical_kb(self):
        original = "样本A净光合速率为 3.2，单位未记录。"
        corrected = "样本A净光合速率为 3.2 μmol CO₂ m⁻² s⁻¹（仅作合成测试）。"
        asset = self.import_document("需要复核的光合作用记录.txt", original)
        state = self.assert_ok("GET", "/api/state")
        reviews = [item for item in state["reviews"] if item["asset_id"] == asset["id"]]
        self.assertEqual(len(reviews), 1, state)
        review = reviews[0]
        self.assertEqual(review["status"], "pending")
        self.assertEqual(self.training_preview()["total"], 0)

        self.assert_ok(
            "POST", f"/api/reviews/{review['id']}", {"action": "approve", "text": corrected}
        )
        detail = self.assert_ok("GET", f"/api/assets/{asset['id']}")
        self.assertEqual(detail["raw_text"], original)
        block = next(item for item in detail["blocks"] if item["id"] == review["block_id"])
        self.assertEqual(block["original_text"], original)
        self.assertEqual(block["text"], corrected)
        self.assertEqual(block["status"], "ready")
        _, source_bytes, _ = self.request("GET", f"/api/files/{asset['id']}")
        self.assertEqual(source_bytes, original.encode("utf-8"))
        self.assertEqual(self.training_preview()["total"], 1)
        self.assertIn(corrected, json.dumps(self.training_preview(), ensure_ascii=False))
        audit = self.assert_ok("GET", "/api/state")
        self.assertIn(review["id"], json.dumps(audit["logs"], ensure_ascii=False))

        status, error, _ = self.request(
            "POST", f"/api/reviews/{review['id']}", {"action": "approve", "text": "重复修改"}
        )
        self.assertEqual(status, 409, error)

        self.publish_kb("复核后的知识库")
        before_reprocess = self.search_kb("净光合速率")
        self.assertIn(corrected, json.dumps(before_reprocess, ensure_ascii=False))
        self.assert_ok("POST", "/api/jobs", {"asset_ids": [asset["id"]]})
        historical = self.search_kb("净光合速率", before_reprocess["version_id"])
        self.assertEqual(historical, before_reprocess)

    def test_rejected_review_block_never_enters_outputs(self):
        asset = self.import_document(
            "拒绝的数值记录.txt", "样本B净光合速率为 9.8，单位未记录。"
        )
        state = self.assert_ok("GET", "/api/state")
        review = next(item for item in state["reviews"] if item["asset_id"] == asset["id"])
        self.assert_ok("POST", f"/api/reviews/{review['id']}", {"action": "reject"})
        detail = self.assert_ok("GET", f"/api/assets/{asset['id']}")
        self.assertTrue(all(item["status"] == "rejected" for item in detail["blocks"]))
        self.assertEqual(self.training_preview()["total"], 0)
        status, error, _ = self.legacy_output(
            "/api/kb/publish", {"name": "拒绝内容不能发布", "chunk_size": 500}
        )
        self.assertGreaterEqual(status, 400, error)
        self.assertLess(status, 500, error)

    def test_moved_data_and_legacy_absolute_paths_remain_downloadable(self):
        source = "石斛迁移验证记录。整个数据目录复制后，原件、训练包与知识库均应可读取。"
        asset = self.import_document("可迁移的植物资料.txt", source)
        training = self.server.workshop.training_export({"type": "pretrain", "name": "迁移训练集"})
        knowledge = self.publish_kb("迁移知识库")
        original_search = self.search_kb("石斛", knowledge["version"]["id"])
        saved_archives = {}
        for version in (training, knowledge):
            status, content, _ = self.request("GET", version["download_url"])
            self.assertEqual(status, 200)
            saved_archives[version["download_url"]] = content

        self.stop_server()
        previous_root = Path(self.directory.name)
        connection = sqlite3.connect(previous_root / "workshop.sqlite3")
        try:
            # Current databases must store portable paths; then deliberately
            # turn them into the absolute paths used by the earlier build.
            for table in ("assets", "versions"):
                rows = connection.execute(f"SELECT id,path FROM {table}").fetchall()
                self.assertTrue(rows)
                for record_id, path in rows:
                    self.assertFalse(Path(path).is_absolute(), path)
                    connection.execute(
                        f"UPDATE {table} SET path=? WHERE id=?",
                        (str(previous_root / path), record_id),
                    )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()

        moved_directory = tempfile.TemporaryDirectory(prefix="plant-workshop-moved-")
        self.addCleanup(moved_directory.cleanup)
        moved_root = Path(moved_directory.name) / "data"
        shutil.copytree(previous_root, moved_root)
        shutil.rmtree(previous_root)
        self.assertFalse(previous_root.exists())
        self.start_server(data_dir=str(moved_root))

        status, original, _ = self.request("GET", f"/api/files/{asset['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(original, source.encode("utf-8"))
        for download_url, expected in saved_archives.items():
            status, content, _ = self.request("GET", download_url)
            self.assertEqual(status, 200)
            self.assertEqual(content, expected)
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertIsNone(archive.testzip())
        self.assertEqual(
            self.search_kb("石斛", knowledge["version"]["id"]), original_search
        )
        self.assert_ok("POST", "/api/jobs", {"asset_ids": [asset["id"]]})
        detail = self.assert_ok("GET", f"/api/assets/{asset['id']}")
        self.assertEqual(detail["raw_text"], source)
        connection = sqlite3.connect(moved_root / "workshop.sqlite3")
        try:
            for table in ("assets", "versions"):
                self.assertTrue(
                    all(not Path(path).is_absolute() for (path,) in connection.execute(f"SELECT path FROM {table}"))
                )
        finally:
            connection.close()

    def assert_kb_contains_only_source(self, source):
        published = self.publish_kb("仅原始资料知识库")
        status, content, _ = self.request("GET", published["download_url"])
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            chunks = [
                json.loads(line)
                for line in archive.read("chunks.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        self.assertEqual([item["text"] for item in chunks], [source])
        self.assertEqual(self.search_kb("TASK_REJECTED_MARKER")["results"], [])

    def test_task_records_use_task_exports_and_never_prose_or_kb(self):
        source = "BOTANY_PROSE_ONLY：拟南芥叶形观察原始记录，仅用于演示。"
        self.import_document("原始正文.txt", source)
        messages = [
            {"role": "user", "content": "TASK_CHAT_MARKER：请提取样本名称。"},
            {"role": "assistant", "content": "演示样本名称是拟南芥。"},
        ]
        instruction = {
            "instruction": "TASK_INSTRUCTION_MARKER：请提取温度单位。",
            "input": "记录值为 22 °C。",
            "output": "单位为 °C。",
        }
        completion = {
            "prompt": "TASK_COMPLETION_MARKER：叶绿素记录中的单位为",
            "completion": "mg/g鲜重（合成演示）。",
        }
        preference = {
            "prompt": "TASK_PREFERENCE_MARKER：没有测定单位时如何处理？",
            "chosen": "保留缺失，并核对原始记录。",
            "rejected": "TASK_REJECTED_MARKER：无证据地填入一个单位并作结论。",
        }
        for index, record in enumerate(({"messages": messages}, instruction, completion, preference)):
            self.import_document(
                f"任务记录_{index}.jsonl", json.dumps(record, ensure_ascii=False) + "\n", purpose="both"
            )

        preview = self.training_preview("pretrain")
        self.assertEqual(preview["total"], 1)
        self.assertEqual(preview["samples"][0]["text"], source)
        with self.export_training("pretrain") as archive:
            records_by_split = self.read_export_records(archive)
        self.assertEqual(
            [item["text"] for records in records_by_split.values() for item in records], [source]
        )
        self.assert_kb_contains_only_source(source)

        with self.export_training("sft") as archive:
            exported = self.read_export_records(archive)
        sft_records = [item for records in exported.values() for item in records]
        self.assertEqual(len(sft_records), 3)
        self.assertIn(messages, [item["messages"] for item in sft_records if "messages" in item])
        self.assertIn(
            [
                {"role": "user", "content": instruction["instruction"] + "\n" + instruction["input"]},
                {"role": "assistant", "content": instruction["output"]},
            ],
            [item["messages"] for item in sft_records if "messages" in item],
        )
        self.assertIn(
            completion,
            [{key: item[key] for key in ("prompt", "completion")} for item in sft_records if "completion" in item],
        )
        with self.export_training("preference") as archive:
            exported = self.read_export_records(archive)
        preference_records = [item for records in exported.values() for item in records]
        self.assertEqual(len(preference_records), 1)
        self.assertEqual(
            {key: preference_records[0][key] for key in ("prompt", "chosen", "rejected")}, preference
        )

    def test_reviewed_task_text_cannot_be_reclassified_as_source_prose(self):
        source = "BOTANY_PROSE_ONLY：合成苔藓观察正文。"
        self.import_document("独立原始正文.txt", source)
        task_records = (
            {"messages": [{"role": "user", "content": "提取记录。"}, {"role": "assistant", "content": "待复核字符：\ufffd"}]},
            {"prompt": "如何处理缺失单位？", "chosen": "保留缺失。", "rejected": "TASK_REJECTED_MARKER：\ufffd补造单位。"},
        )
        for index, task in enumerate(task_records):
            asset = self.import_document(
                f"待复核任务_{index}.jsonl", json.dumps(task, ensure_ascii=False) + "\n", purpose="both"
            )
            state = self.assert_ok("GET", "/api/state")
            reviews = [item for item in state["reviews"] if item["asset_id"] == asset["id"]]
            self.assertEqual(len(reviews), 1)
            self.assert_ok(
                "POST", f"/api/reviews/{reviews[0]['id']}",
                {"action": "approve", "text": f"TASK_REJECTED_MARKER：复核后的非 JSON 任务文本 {index}。"},
            )
            detail = self.assert_ok("GET", f"/api/assets/{asset['id']}")
            self.assertTrue(all(item["status"] == "ready" for item in detail["blocks"]))
        self.assertEqual(self.training_preview("pretrain")["total"], 1)
        with self.export_training("pretrain") as archive:
            exported = self.read_export_records(archive)
        self.assertEqual([item["text"] for records in exported.values() for item in records], [source])
        self.assertEqual(self.training_preview("sft")["total"], 0)
        self.assertEqual(self.training_preview("preference")["total"], 0)
        self.assert_kb_contains_only_source(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

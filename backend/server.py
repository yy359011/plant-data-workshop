#!/usr/bin/env python3
"""Local scientific data preparation service with explicit chapter model jobs.

Run: python backend/server.py [--port 8766]
PLANT_DATA_DIR selects a persistent directory. Optional parsers: pypdf, openpyxl.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import io
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import socket
import struct
import sys
import threading
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
import uuid
import xml.etree.ElementTree as ET
import zipfile

APP_ROOT = Path(__file__).resolve().parent.parent
MAX_FILE = 16 * 1024 * 1024
MAX_BODY = 48 * 1024 * 1024
MAX_TEXT = 4 * 1024 * 1024
PURPOSES = {"both", "training", "kb"}
LICENSES = {"self_owned", "research_only", "unknown"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
SUPPORTED = {".txt", ".md", ".csv", ".json", ".jsonl", ".pdf", ".xlsx", ".docx"} | IMAGE_TYPES


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def local_ipv4_addresses():
    """Discover assigned addresses without contacting an external service.

    The UDP connect only asks the OS to select its default interface; no packet
    is sent. Prefer that address in printed URLs, then use hostname resolution
    as a fallback. Binding each candidate verifies that it belongs to this PC.
    """
    candidates = []
    # Prefer physical Ethernet/Wi-Fi interfaces over virtual VPN defaults.
    # Windows falls back to hostname addresses and the route probe below.
    try:
        import fcntl
        request = 0xC0206921 if sys.platform == "darwin" else 0x8915
        for _, interface in socket.if_nameindex():
            if interface.startswith(("en", "eth", "wl")):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    try:
                        result = fcntl.ioctl(probe.fileno(), request, struct.pack("256s", interface[:15].encode()))
                        candidates.append(socket.inet_ntoa(result[20:24]))
                    except OSError:
                        pass
    except (ImportError, AttributeError, OSError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    addresses = []
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
            if (address.is_loopback or address.is_unspecified or address.is_multicast
                    or address in ipaddress.ip_network("198.18.0.0/15") or candidate in addresses):
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind((candidate, 0))
            addresses.append(candidate)
        except (OSError, ValueError):
            continue
    return addresses


def parse_authority(value):
    """Parse a literal IP/localhost authority, never a user-controlled DNS name."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError("Invalid authority")
    parsed = urlsplit("http://" + value)
    if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Invalid authority")
    hostname = parsed.hostname
    if hostname != "localhost":
        hostname = str(ipaddress.ip_address(hostname or ""))
    return hostname, parsed.port or 80


def parse_http_origin(value):
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Invalid origin")
    return parse_authority(parsed.netloc)


def is_loopback_address(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_text(text):
    # Preserve indentation, math, units and numeric precision; never infer facts.
    return "\n".join(line.rstrip() for line in str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "").replace("\u200b", "").split("\n")).strip()


def decode_text(data):
    if b"\x00" in data[:4096]:
        raise APIError("文本文件包含二进制内容，请检查文件格式。")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise APIError("无法识别文本编码，请保存为 UTF-8 后重试。")


def safe_name(name):
    if not isinstance(name, str) or not name or len(name) > 180:
        raise APIError("文件名为空或过长。")
    if "/" in name or "\\" in name or name in {".", ".."} or any(ord(c) < 32 for c in name):
        raise APIError("文件名不能包含目录路径或控制字符。")
    if Path(name).suffix.lower() not in SUPPORTED:
        raise APIError("暂不支持此文件类型：" + name)
    return name


def check_zip(path):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 20000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
            raise APIError("Office 文件解压后过大。")
        for info in infos:
            if info.file_size > 32 * 1024 * 1024 or info.flag_bits & 1:
                raise APIError("Office 文件包含过大的或加密的内部文件。")


class WorkspaceManager:
    """管理多个独立工作空间（各自独立的 data_dir + Workshop + 模块树）。

    注册表存于 <base_dir>/.registry.json；每个工作空间是 <base_dir>/workspaces/<id>/。
    模块树（Workshop + ChapterPipeline + PlatformCore + PlatformData）按 workspace_id 懒加载。
    """

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir = self.base_dir / "workspaces"
        self.workspaces_dir.mkdir(exist_ok=True)
        self.registry_path = self.base_dir / ".registry.json"
        self.registry = self._load_registry()
        # 旧格式迁移：如果 base_dir 根目录下有 workshop.sqlite3，自动登记为 legacy workspace
        self._migrate_legacy()
        self.active_id = self._resolve_active_id()
        self._instances = {}  # {id: {'workshop': ..., 'chapters': ..., ...}}
        self._builders = None  # 由 create_server 注入的模块构造函数

    def _migrate_legacy(self):
        """把旧格式（data/ 根目录直接有 SQLite）自动登记到注册表。"""
        legacy_db = self.base_dir / "workshop.sqlite3"
        if not legacy_db.exists():
            return
        # 如果 workspaces 子目录已存在，说明已经做过迁移
        if any(self.workspaces_dir.iterdir()):
            return
        ws_id = "legacy"
        ws_dir = self.workspaces_dir / ws_id
        ws_dir.mkdir(exist_ok=True)
        # 移动旧文件到 workspaces/legacy/
        for item in self.base_dir.iterdir():
            if item.name.startswith(".") or item.name == "workspaces":
                continue
            target = ws_dir / item.name
            if not target.exists():
                try:
                    item.rename(target)
                except OSError:
                    # 可能是被其他进程占用的 SQLite 日志文件，跳过
                    pass
        # 登记到注册表
        self.registry[ws_id] = {
            "name": "默认工作空间（历史数据）",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "legacy": True,
        }
        self.registry["_active"] = ws_id
        self._save_registry()

    # ---- 注册表 ---------------------------------------------------------------

    def _load_registry(self):
        if self.registry_path.exists():
            try:
                return json.loads(self.registry_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_registry(self):
        tmp = self.registry_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.registry, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.registry_path)

    def _resolve_active_id(self):
        active = self.registry.get("_active")
        if active and active in self.registry:
            return active
        ids = [k for k in self.registry if not k.startswith("_")]
        if ids:
            self.registry["_active"] = ids[0]
            self._save_registry()
            return ids[0]
        # 没有任何工作空间——创建默认的
        ws_id = self._create_default("默认工作空间")
        return ws_id

    def _create_default(self, name):
        ws_id = uuid.uuid4().hex[:12]
        created_at = datetime.now(timezone.utc).isoformat()
        self.registry[ws_id] = {"name": name, "created_at": created_at}
        self.registry["_active"] = ws_id
        self._save_registry()
        return ws_id

    # ---- 公开 API -------------------------------------------------------------

    def list_workspaces(self):
        items = []
        for ws_id, meta in self.registry.items():
            if ws_id.startswith("_"):
                continue
            dir_path = self.workspaces_dir / ws_id
            items.append({
                "id": ws_id,
                "name": meta.get("name", ws_id),
                "created_at": meta.get("created_at", ""),
                "active": ws_id == self.active_id,
                "exists": dir_path.exists(),
            })
        items.sort(key=lambda x: x["created_at"])
        return items

    def create_workspace(self, name):
        name = (name or "").strip()
        if not name:
            raise APIError("工作空间名称不能为空。")
        # 名称不能重复
        for meta in self.registry.values():
            if isinstance(meta, dict) and meta.get("name") == name:
                raise APIError("同名工作空间已存在。")
        ws_id = uuid.uuid4().hex[:12]
        created_at = datetime.now(timezone.utc).isoformat()
        self.registry[ws_id] = {"name": name, "created_at": created_at}
        self._save_registry()
        # 创建空目录（Workshop 在首次 _get_instances 时会自动 init_db）
        (self.workspaces_dir / ws_id).mkdir(exist_ok=True)
        return {"id": ws_id, "name": name, "created_at": created_at}

    def delete_workspace(self, ws_id):
        if ws_id not in self.registry:
            raise APIError("工作空间不存在。", 404)
        if ws_id == self.active_id:
            raise APIError("当前正在使用的工作空间不能删除，请先切换到其他工作空间。")
        if len(self.list_workspaces()) <= 1:
            raise APIError("至少要保留一个工作空间。")
        # 删除磁盘目录
        dir_path = self.workspaces_dir / ws_id
        if dir_path.exists():
            import shutil
            shutil.rmtree(dir_path, ignore_errors=True)
        # 清理注册表和实例缓存
        del self.registry[ws_id]
        self._instances.pop(ws_id, None)
        self._save_registry()

    def activate_workspace(self, ws_id):
        if ws_id not in self.registry:
            raise APIError("工作空间不存在。", 404)
        self.active_id = ws_id
        self.registry["_active"] = ws_id
        self._save_registry()
        # 确保模块树已初始化
        self._get_instances(ws_id)

    def set_builders(self, builders):
        self._builders = builders

    # ---- 内部 ---------------------------------------------------------------

    def _get_instances(self, ws_id, seed=False):
        if ws_id in self._instances:
            return self._instances[ws_id]
        if not self._builders:
            raise RuntimeError("WorkspaceManager.builders 尚未设置")
        dir_path = self.workspaces_dir / ws_id
        instances = self._builders["build_all"](dir_path, seed=seed)
        self._instances[ws_id] = instances
        return instances

    def get_active(self):
        return self._get_instances(self.active_id)

    @property
    def active_workspace(self):
        return self.get_active()["workshop"]

    @property
    def active_chapters(self):
        return self.get_active()["chapters"]

    @property
    def active_library(self):
        return self.get_active()["library"]

    @property
    def active_platform(self):
        return self.get_active()["platform"]

    @property
    def active_platform_data(self):
        return self.get_active()["platform_data"]


class Workshop:
    def __init__(self, data_dir, seed=True):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_dir = self.root / "raw"
        self.export_dir = self.root / "exports"
        self.raw_dir.mkdir(exist_ok=True)
        self.export_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "workshop.sqlite3"
        self.lock = threading.RLock()
        self.init_db()
        if seed:
            self.seed()

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def init_db(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,size INTEGER NOT NULL,
              status TEXT NOT NULL,created_at TEXT NOT NULL,purpose TEXT NOT NULL,license TEXT NOT NULL,
              is_demo INTEGER NOT NULL DEFAULT 0,sha256 TEXT NOT NULL UNIQUE,path TEXT NOT NULL,
              raw_text TEXT NOT NULL DEFAULT '',review_flags TEXT NOT NULL DEFAULT '[]',error TEXT);
            CREATE TABLE IF NOT EXISTS blocks (
              id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),text TEXT NOT NULL,
              kind TEXT NOT NULL,location TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ready',
              original_text TEXT NOT NULL,sample_json TEXT,ordinal INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (
              id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),block_id TEXT NOT NULL REFERENCES blocks(id),
              kind TEXT NOT NULL,message TEXT NOT NULL,original_text TEXT NOT NULL,
              extracted_text TEXT NOT NULL,suggested_text TEXT,status TEXT NOT NULL DEFAULT 'pending',
              location TEXT NOT NULL,created_at TEXT NOT NULL,resolved_at TEXT);
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,asset_count INTEGER NOT NULL,purpose TEXT NOT NULL,
              status TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,format TEXT NOT NULL,
              count INTEGER NOT NULL,created_at TEXT NOT NULL,download_url TEXT NOT NULL,
              details TEXT NOT NULL,path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks (
              id TEXT PRIMARY KEY,version_id TEXT NOT NULL REFERENCES versions(id),asset_id TEXT NOT NULL,
              asset_name TEXT NOT NULL,text TEXT NOT NULL,location TEXT NOT NULL,terms TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS logs (
              id TEXT PRIMARY KEY,event TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT OR IGNORE INTO settings(key,value) VALUES('project_name','植物科研数据工坊');
            """)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(assets)')}
            for column, definition in (("source_group", "TEXT"), ("provenance", "TEXT NOT NULL DEFAULT '{}'")):
                if column not in columns:
                    db.execute(f"ALTER TABLE assets ADD COLUMN {column} {definition}")
            # Older builds wrote absolute paths. Rebase them to the current data
            # directory so a complete data/ backup remains movable.
            for table, folder in (("assets", "raw"), ("versions", "exports")):
                for row in db.execute(f"SELECT id,path FROM {table}").fetchall():
                    if Path(row["path"]).is_absolute():
                        db.execute(f"UPDATE {table} SET path=? WHERE id=?", (folder + "/" + Path(row["path"]).name, row["id"]))

    def local_path(self, stored_path):
        path = (self.root / stored_path).resolve()
        if not path.is_relative_to(self.root):
            raise APIError("数据文件路径无效。", 403)
        return path

    def log(self, db, event, detail):
        db.execute("INSERT INTO logs VALUES(?,?,?,?)", (new_id("log"), event, detail, stamp()))

    def seed(self):
        with self.connect() as db:
            if db.execute("SELECT value FROM settings WHERE key='demo_seeded'").fetchone():
                return
        try:
            try:
                from .demo_seed import seed_documents
            except ImportError:
                from demo_seed import seed_documents
            docs = seed_documents()
        except ImportError:
            return
        with self.lock:
            batches = {"植物学文献整理": [], "实验与标本记录": [], "训练样本校验": []}
            for doc in docs:
                created = self.import_one(doc["name"], doc["content"].encode("utf-8"), doc.get("purpose", "both"), doc.get("license", "self_owned"), True, doc.get("review_flags", []))
                batch_name = "训练样本校验" if doc.get("purpose") == "training" else "植物学文献整理" if Path(doc["name"]).suffix.lower() == ".md" else "实验与标本记录"
                batches[batch_name].append(created["id"])
            for batch_name, ids in batches.items():
                if ids:
                    self.process(ids, batch_name)
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('demo_seeded','1')")

    def asset(self, db, asset_id):
        row = db.execute("SELECT a.*, (SELECT COUNT(*) FROM blocks b WHERE b.asset_id=a.id) block_count FROM assets a WHERE a.id=?", (asset_id,)).fetchone()
        if not row:
            raise APIError("资料不存在。", 404)
        return self.asset_public(row)

    def asset_public(self, row):
        provenance = json.loads(row['provenance'] or '{}')
        return {k: (bool(row[k]) if k == "is_demo" else row[k]) for k in ("id", "name", "kind", "size", "status", "created_at", "purpose", "license", "is_demo", "block_count", "source_group")} | {"provenance": provenance, "source_document_id": provenance.get('source_document_id')} | ({"error": row["error"]} if row["error"] else {})

    def version_public(self, row):
        return {k: row[k] for k in ("id", "name", "type", "format", "count", "created_at", "download_url")} | {"details": json.loads(row["details"])}

    def state(self):
        with self.connect() as db:
            assets = [self.asset_public(row) for row in db.execute("SELECT a.*, (SELECT COUNT(*) FROM blocks b WHERE b.asset_id=a.id) block_count FROM assets a ORDER BY a.created_at DESC,a.rowid DESC")]
            reviews = [dict(row) for row in db.execute("SELECT r.*,a.name asset_name FROM reviews r JOIN assets a ON a.id=r.asset_id ORDER BY r.created_at DESC,r.rowid DESC")]
            return {
                "stats": {"assets": len(assets), "normalized": db.execute("SELECT COUNT(*) FROM blocks WHERE status='ready'").fetchone()[0], "pending": sum(r["status"] == "pending" for r in reviews), "versions": db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]},
                "assets": assets,
                "jobs": [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT 100")],
                "reviews": reviews,
                "versions": [self.version_public(row) for row in db.execute("SELECT * FROM versions ORDER BY created_at DESC,rowid DESC")],
                "logs": [dict(row) for row in db.execute("SELECT * FROM logs ORDER BY created_at DESC,rowid DESC LIMIT 100")],
                "settings": {"project_name": db.execute("SELECT value FROM settings WHERE key='project_name'").fetchone()[0]},
                "capabilities": {"model_training": False, "ai_generation": "explicit_configured_chapter_jobs", "retrieval": "keyword", "ocr": False, "license_note": "本地用途机制是操作示例，不构成法律判定。"},
            }

    def asset_detail(self, asset_id):
        with self.connect() as db:
            asset = self.asset(db, asset_id)
            raw = db.execute("SELECT raw_text FROM assets WHERE id=?", (asset_id,)).fetchone()[0]
            blocks = [dict(row) for row in db.execute("SELECT id,text,kind,location,status,original_text FROM blocks WHERE asset_id=? ORDER BY ordinal", (asset_id,))]
            return {"asset": asset, "blocks": blocks, "raw_text": raw}

    def import_one(self, name, data, purpose, license_name, is_demo=False, review_flags=None):
        safe_name(name)
        if not data:
            raise APIError("空文件不能导入：" + name)
        if len(data) > MAX_FILE:
            raise APIError("单个文件不能超过 16 MB：" + name, 413)
        digest = hashlib.sha256(data).hexdigest()
        with self.connect() as db:
            exists = db.execute("SELECT id FROM assets WHERE sha256=?", (digest,)).fetchone()
            if exists:
                self.log(db, "duplicate", "相同内容已存在，跳过重复导入：" + name)
                return self.asset(db, exists["id"]) | {"duplicate": True}
            asset_id = new_id("asset")
            path = self.raw_dir / (asset_id + Path(name).suffix.lower())
            path.write_bytes(data)
            db.execute("INSERT INTO assets(id,name,kind,size,status,created_at,purpose,license,is_demo,sha256,path,review_flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (asset_id, name, Path(name).suffix.lstrip(".").lower(), len(data), "pending", stamp(), purpose, license_name, int(is_demo), digest, path.relative_to(self.root).as_posix(), dump(review_flags or [])))
            self.log(db, "import", "已导入" + ("合成演示" if is_demo else "用户") + "资料：" + name)
            return self.asset(db, asset_id)

    def import_files(self, body):
        files = body.get("files")
        purpose = body.get("purpose", "both")
        license_name = body.get("license", "unknown")
        self.check_choices(purpose, license_name)
        if not isinstance(files, list) or not 1 <= len(files) <= 100:
            raise APIError("一次须导入 1–100 个文件。")
        prepared = []
        total = 0
        for item in files:
            if not isinstance(item, dict):
                raise APIError("文件数据格式错误。")
            name = safe_name(item.get("name"))
            try:
                encoded = item.get("content_base64", "")
                if not isinstance(encoded, str) or len(encoded) > math.ceil(MAX_FILE / 3) * 4 + 4:
                    raise APIError("文件数据过大。", 413)
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise APIError("文件 Base64 内容无效：" + name)
            if not data or len(data) > MAX_FILE:
                raise APIError("文件为空或超过 16 MB：" + name)
            total += len(data)
            if total > 32 * 1024 * 1024:
                raise APIError("单批文件总容量不能超过 32 MB。", 413)
            prepared.append((name, data))
        with self.lock:
            imported = [self.import_one(name, data, purpose, license_name) for name, data in prepared]
            job = self.process([a["id"] for a in imported if not a.get("duplicate")]) if body.get("process", True) else None
        return {"assets": imported, "imported": imported, "job": job, "state": self.state()}

    @staticmethod
    def check_choices(purpose, license_name):
        if not isinstance(purpose, str) or not isinstance(license_name, str) or purpose not in PURPOSES or license_name not in LICENSES:
            raise APIError("用途或授权选项无效。")

    def update_asset(self, asset_id, body):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            if not row:
                raise APIError("资料不存在。", 404)
            purpose, license_name = body.get("purpose", row["purpose"]), body.get("license", row["license"])
            self.check_choices(purpose, license_name)
            db.execute("UPDATE assets SET purpose=?,license=? WHERE id=?", (purpose, license_name, asset_id))
            self.log(db, "permission", f"{row['name']}：用途 {row['purpose']}→{purpose}；授权 {row['license']}→{license_name}。此为用途登记，非法律判定。")
            return {"asset": self.asset(db, asset_id)}

    @staticmethod
    def task_kind(record):
        if not isinstance(record, dict):
            return None
        if all(key in record for key in ("chosen", "rejected")):
            return "preference"
        if "messages" in record or all(key in record for key in ("instruction", "output")) or all(key in record for key in ("prompt", "completion")):
            return "sft"
        return None

    def parse(self, path, kind):
        if "." + kind in IMAGE_TYPES:
            return "", [], True
        data = path.read_bytes()
        entries, raw_parts, needs_ocr = [], [], False
        raw_document = None
        raw_length = 0
        def add(text, block_kind="text", location="正文", sample=None, flags=None):
            nonlocal raw_length
            text = clean_text(text)
            if text:
                entries.append({"text": text, "kind": block_kind, "location": location, "sample": sample, "flags": flags or []})
                raw_parts.append(text)
                raw_length += len(text)
                if raw_length > MAX_TEXT:
                    raise APIError("提取文本超过 4 MB，请拆分资料后重试。")
        if kind in {"txt", "md"}:
            raw = decode_text(data)
            raw_document = raw
            parts = re.split(r"\n\s*\n", raw)
            for i, part in enumerate(parts):
                add(part, "formula" if "$$" in part else "text", "段落 " + str(i + 1))
        elif kind == "csv":
            raw = decode_text(data)
            raw_document = raw
            reader = csv.reader(io.StringIO(raw))
            rows = list(reader)
            if rows:
                header = rows[0]
                add("表头：" + " | ".join(header), "table", "第 1 行")
                for i, row in enumerate(rows[1:], 2):
                    if any(v.strip() for v in row):
                        flags = []
                        if len(row) != len(header):
                            flags.append({"kind": "structure", "message": "此行字段数与表头不一致，请核对 CSV 分隔符与缺失单元格。"})
                        for j, field in enumerate(header):
                            if field.strip().lower() in {"unit", "units", "单位", "计量单位"} and (j >= len(row) or not row[j].strip()):
                                flags.append({"kind": "unit", "message": "此行单位字段为空，请依据原始资料确认；系统不会补造单位。"})
                        # Original row retained, while headers give every record context.
                        add("字段：" + " | ".join(header) + "\n" + ",".join(row) + "\n" + "; ".join(f"{header[j] if j < len(header) else '列'+str(j+1)}={value}" for j, value in enumerate(row)), "table", f"第 {i} 行", flags=flags)
        elif kind in {"json", "jsonl"}:
            raw = decode_text(data)
            raw_document = raw
            if kind == "jsonl":
                records = []
                for i, line in enumerate(raw.splitlines(), 1):
                    if line.strip():
                        try:
                            records.append((i, json.loads(line)))
                        except json.JSONDecodeError as exc:
                            raise APIError(f"JSONL 第 {i} 行格式错误：{exc.msg}")
            else:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise APIError("JSON 格式错误：" + exc.msg)
                if isinstance(parsed, list):
                    records = list(enumerate(parsed, 1))
                elif isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
                    records = list(enumerate(parsed["data"], 1))
                else:
                    records = [(1, parsed)]
            for i, record in records:
                if isinstance(record, dict) and isinstance(record.get("text"), str):
                    add(record["text"], self.task_kind(record) or "text", "记录 " + str(i), record)
                else:
                    add(json.dumps(record, ensure_ascii=False, indent=2), self.task_kind(record) or "structured", "记录 " + str(i), record if isinstance(record, dict) else None)
        elif kind == "pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                raise APIError("PDF 解析需要 pypdf。原文件已保留，请安装可选解析器后重试。")
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(""):
                raise APIError("PDF 已加密，需先提供可读取版本。")
            if len(reader.pages) > 500:
                raise APIError("PDF 超过 500 页，请拆分后导入。")
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if not clean_text(text):
                    needs_ocr = True
                else:
                    add(text, "text", f"第 {i} 页")
        elif kind == "docx":
            check_zip(path)
            with zipfile.ZipFile(path) as z:
                root = ET.fromstring(z.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            body = root.find("w:body", ns)
            for i, element in enumerate(list(body) if body is not None else [], 1):
                if element.tag.endswith("}tbl"):
                    lines = [" | ".join("".join(t.text or "" for t in cell.findall(".//w:t", ns)) for cell in row.findall("w:tc", ns)) for row in element.findall("w:tr", ns)]
                    add("\n".join(lines), "table", f"文档表格 {i}")
                else:
                    add("".join(t.text or "" for t in element.findall(".//w:t", ns)), "text", f"文档段落 {i}")
        elif kind == "xlsx":
            check_zip(path)
            try:
                from openpyxl import load_workbook
            except ImportError:
                raise APIError("XLSX 解析需要 openpyxl。原文件已保留，请安装可选解析器后重试。")
            workbook = load_workbook(path, read_only=True, data_only=False)
            try:
                for sheet in workbook:
                    if sheet.max_row > 50000 or sheet.max_column > 200:
                        raise APIError("工作表超过 50,000 行或 200 列，请拆分后导入。")
                    header = None
                    for i, row in enumerate(sheet.iter_rows(values_only=True), 1):
                        cells = ["" if value is None else str(value) for value in row]
                        if not any(cells):
                            continue
                        if header is None:
                            header = cells
                        add("字段：" + " | ".join(header) + "\n" + " | ".join(cells), "table", f"{sheet.title} · 第 {i} 行")
            finally:
                workbook.close()
        else:
            raise APIError("无法处理此文件类型。")
        # Deduplicate identical extracted blocks within a source; never invent rows.
        seen, unique = set(), []
        for entry in entries:
            digest = hashlib.sha256(entry["text"].encode()).hexdigest()
            if digest not in seen:
                seen.add(digest)
                unique.append(entry)
        return raw_document if raw_document is not None else "\n\n".join(raw_parts), unique, needs_ocr

    def process(self, asset_ids=None, name="资料标准化批次"):
        with self.lock:
            with self.connect() as db:
                if asset_ids is not None and not isinstance(asset_ids, list):
                    raise APIError("资料 ID 列表无效。")
                if asset_ids:
                    if not isinstance(asset_ids, list) or len(asset_ids) > 1000 or not all(isinstance(i, str) for i in asset_ids):
                        raise APIError("资料 ID 列表无效。")
                    rows = []
                    for asset_id in dict.fromkeys(asset_ids):
                        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
                        if not row:
                            raise APIError("指定资料不存在。", 404)
                        # Preserve completed work and reviewed revisions on retry.
                        if row["status"] in {"pending", "error"}:
                            rows.append(row)
                else:
                    rows = list(db.execute("SELECT * FROM assets WHERE status IN ('pending','error')"))
                if not rows:
                    return {"message": "没有待处理资料；已完成的资料与复核修订保持不变。", "status": "completed", "asset_count": 0}
                job_id = new_id("job")
                purposes = {r["purpose"] for r in rows}
                purpose = next(iter(purposes)) if len(purposes) == 1 else "both"
                db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (job_id, str(name)[:120], len(rows), purpose, "processing", stamp()))
            failures = review_count = ocr_count = 0
            for row in rows:
                try:
                    raw, entries, needs_ocr = self.parse(self.local_path(row["path"]), row["kind"])
                    if not entries and not needs_ocr:
                        raise APIError("资料没有可提取的有效内容。")
                    with self.connect() as db:
                        # Failed parse attempts do not retain partial blocks.
                        db.execute("DELETE FROM reviews WHERE asset_id=?", (row["id"],))
                        db.execute("DELETE FROM blocks WHERE asset_id=?", (row["id"],))
                        pending = 0
                        flags = json.loads(row["review_flags"])
                        for ordinal, entry in enumerate(entries):
                            block_id = new_id("block")
                            matched = [flag for flag in flags if flag.get("match") and flag["match"] in entry["text"]]
                            if not matched:
                                matched = list(entry.get("flags", []))
                            if "\ufffd" in entry["text"]:
                                matched.append({"kind": "encoding", "message": "提取文本含替换字符，请对照原文件复核。"})
                            sample = entry["sample"]
                            task_sample = isinstance(sample, dict) and any(key in sample for key in ("messages", "instruction", "chosen", "completion"))
                            if re.search(r"(?:单位未记录|单位缺失|单位待确认)", entry["text"]) and not matched and not task_sample:
                                matched.append({"kind": "unit", "message": "原始资料注明单位缺失，请复核；系统不会补造单位。"})
                            block_status = "review" if matched else "ready"
                            db.execute("INSERT INTO blocks VALUES(?,?,?,?,?,?,?,?,?)", (block_id, row["id"], entry["text"], entry["kind"], entry["location"], block_status, entry["text"], dump(entry["sample"]) if entry["sample"] is not None else None, ordinal))
                            for flag in matched:
                                db.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (new_id("review"), row["id"], block_id, flag.get("kind", "quality"), flag.get("message", "请对照原资料核对此内容。"), entry["text"], entry["text"], flag.get("suggested_text", ""), "pending", entry["location"], stamp(), None))
                                pending += 1
                        status = "needs_ocr" if needs_ocr else ("review" if pending else "ready")
                        db.execute("UPDATE assets SET raw_text=?,status=?,error=? WHERE id=?", (raw, status, "此资料含图片或无文本层页面，需要 OCR/视觉解析；系统未调用 AI，也未生成替代内容。" if needs_ocr else None, row["id"]))
                        self.log(db, "process", f"{row['name']}：提取 {len(entries)} 个内容块，待复核 {pending} 个。" + ("含待 OCR 内容。" if needs_ocr else ""))
                        review_count += pending
                        ocr_count += int(needs_ocr)
                except Exception as exc:
                    failures += 1
                    message = str(exc) if isinstance(exc, APIError) else "解析失败，请检查文件完整性与格式。"
                    with self.connect() as db:
                        db.execute("UPDATE assets SET status='error',error=? WHERE id=?", (message, row["id"]))
                        self.log(db, "process_error", row["name"] + "：" + message)
            with self.connect() as db:
                final_status = "error" if failures == len(rows) else ("review" if review_count or ocr_count or failures else "completed")
                db.execute("UPDATE jobs SET status=? WHERE id=?", (final_status, job_id))
                self.log(db, "batch", f"批次完成：{len(rows)} 份资料，失败 {failures}，复核 {review_count}，待 OCR {ocr_count}。")
                return dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def review(self, review_id, body):
        action = body.get("action")
        if action not in {"approve", "reject"}:
            raise APIError("复核操作无效。")
        with self.lock, self.connect() as db:
            review = db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
            if not review:
                raise APIError("复核项目不存在。", 404)
            if review["status"] != "pending":
                raise APIError("此复核项已处理。", 409)
            text = body.get("text", review["extracted_text"])
            if not isinstance(text, str) or len(text) > MAX_TEXT:
                raise APIError("复核文本无效。")
            text = clean_text(text)
            if action == "approve" and not text:
                raise APIError("通过复核的内容不能为空。")
            status = "approved" if action == "approve" else "rejected"
            db.execute("UPDATE reviews SET status=?,suggested_text=?,resolved_at=? WHERE id=?", (status, text, stamp(), review_id))
            if action == "reject":
                db.execute("UPDATE blocks SET status='rejected' WHERE id=?", (review["block_id"],))
            else:
                block = db.execute("SELECT sample_json,kind FROM blocks WHERE id=?", (review["block_id"],)).fetchone()
                sample_json = block["sample_json"]
                block_kind = self.task_kind(json.loads(sample_json)) if sample_json else None
                if sample_json:
                    try:
                        sample_json = dump(json.loads(text))
                    except json.JSONDecodeError:
                        # Modified text cannot silently keep an obsolete SFT target.
                        sample_json = None
                db.execute("UPDATE blocks SET text=?,sample_json=?,kind=? WHERE id=?", (text, sample_json, block_kind or block["kind"], review["block_id"]))
                pending = db.execute("SELECT COUNT(*) FROM reviews WHERE block_id=? AND status='pending'", (review["block_id"],)).fetchone()[0]
                rejected = db.execute("SELECT COUNT(*) FROM reviews WHERE block_id=? AND status='rejected'", (review["block_id"],)).fetchone()[0]
                db.execute("UPDATE blocks SET status=? WHERE id=?", ("rejected" if rejected else "review" if pending else "ready", review["block_id"]))
            pending_asset = db.execute("SELECT COUNT(*) FROM reviews WHERE asset_id=? AND status='pending'", (review["asset_id"],)).fetchone()[0]
            current = db.execute("SELECT status FROM assets WHERE id=?", (review["asset_id"],)).fetchone()[0]
            if current not in {"needs_ocr", "error"}:
                db.execute("UPDATE assets SET status=? WHERE id=?", ("review" if pending_asset else "ready", review["asset_id"]))
            self.log(db, "review", f"复核 {review_id}：{status}；原文已保留，修订内容：{text[:180]}")
            return {"review": dict(db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()), "asset": self.asset(db, review["asset_id"])}

    def eligible(self, db, purpose, source='all'):
        if not isinstance(source, str) or source not in {'all', 'original', 'chapters'}:
            raise APIError('产出来源须为 all、original 或 chapters。')
        license_names = ("self_owned",) if purpose == "training" else ("self_owned", "research_only")
        rows = db.execute("SELECT b.*,a.name asset_name,a.is_demo,a.license,a.purpose,a.sha256,a.source_group,a.provenance FROM blocks b JOIN assets a ON b.asset_id=a.id WHERE b.status='ready' AND a.status IN ('ready','review') ORDER BY a.sha256,b.ordinal").fetchall()
        included = []
        for row in rows:
            item = dict(row)
            provenance = json.loads(item['provenance'] or '{}')
            derived = provenance.get('origin') == 'chapter_pipeline'
            if source == 'original' and derived or source == 'chapters' and not derived:
                continue
            if derived:
                # Immutable exported snapshots survive, but future outputs only
                # use the active, approved revision of each source chapter.
                chapter = db.execute("SELECT revision,status,active FROM cp_chapters WHERE id=?", (provenance.get('source_chapter_id'),)).fetchone()
                if not chapter or not chapter['active'] or chapter['status'] != 'approved' or chapter['revision'] != provenance.get('revision'):
                    continue
                document = db.execute('SELECT purpose,license FROM cp_documents WHERE id=?', (item['source_group'],)).fetchone()
                if not document or document['license'] not in license_names or document['purpose'] not in {purpose, 'both'}:
                    continue
            if item['license'] in license_names and item['purpose'] in {purpose, 'both'}:
                included.append(item)
        excluded = db.execute("SELECT COUNT(*) FROM assets WHERE license='unknown'").fetchone()[0]
        warnings = ["用途判定为本地示例机制，不构成法律结论。"]
        if excluded:
            warnings.append(f"已排除 {excluded} 份授权待确认资料。")
        if purpose == "training":
            count = db.execute("SELECT COUNT(*) FROM assets WHERE license='research_only'").fetchone()[0]
            if count:
                warnings.append(f"已排除 {count} 份仅限知识库用途资料。")
        if db.execute("SELECT COUNT(*) FROM reviews WHERE status='pending'").fetchone()[0]:
            warnings.append("待复核与不采用的内容块不进入产物。")
        if any(r["is_demo"] for r in included):
            warnings.append("包含合成演示资料；不应作为真实科研证据。")
        if source == 'chapters':
            warnings.append('章节出口仅采用当前已审批的修订；旧修订、重建后章节与运行中的章节不进入新产物。')
        return included, warnings

    @staticmethod
    def valid_messages(messages):
        return isinstance(messages, list) and bool(messages) and all(isinstance(m, dict) and m.get("role") in {"system", "user", "assistant", "tool"} and isinstance(m.get("content"), str) and bool(m["content"].strip()) for m in messages) and any(m["role"] == "assistant" for m in messages)

    @staticmethod
    def split_map(asset_ids):
        ids = sorted(set(asset_ids))
        n = len(ids)
        # Document-level splits: a source never occurs in multiple splits.
        n_train = math.ceil(n * 0.9)
        n_val = math.floor(n * 0.05)
        return {asset_id: "train" if i < n_train else "validation" if i < n_train + n_val else "test" for i, asset_id in enumerate(ids)}

    def training_preview(self, body, full=False):
        kind = body.get("type", "pretrain")
        if not isinstance(kind, str) or kind not in {"pretrain", "sft", "preference", "image"}:
            raise APIError("训练数据类型无效。")
        if kind == "image":
            return {"samples": [], "total": 0, "split_counts": {"train": 0, "validation": 0, "test": 0}, "warnings": ["此出口不生成图文训练样本；章节视觉结果可经复核产出文字语料，图文训练仍需专门标注与配置。"], "type": kind}
        with self.connect() as db:
            blocks, warnings = self.eligible(db, "training", body.get('source', 'all'))
        samples, seen, excluded_tasks = [], set(), 0
        for block in blocks:
            record = json.loads(block["sample_json"]) if block["sample_json"] else None
            sample = None
            if kind == "pretrain":
                # Task examples carry supervision (including intentionally wrong
                # rejected answers); they are not source-document prose.
                task_record = self.task_kind(record) or block["kind"] in {"sft", "preference"}
                if task_record:
                    excluded_tasks += 1
                    continue
                sample = {"text": block["text"]}
            elif kind == "sft" and isinstance(record, dict):
                if self.valid_messages(record.get("messages")):
                    sample = {"messages": record["messages"]}
                elif isinstance(record.get("instruction"), str) and isinstance(record.get("output"), str) and record["instruction"].strip() and record["output"].strip():
                    sample = {"messages": [{"role": "user", "content": record["instruction"] + ("\n" + str(record["input"]) if record.get("input") else "")}, {"role": "assistant", "content": record["output"]}]}
                elif isinstance(record.get("prompt"), str) and isinstance(record.get("completion"), str) and record["completion"].strip():
                    sample = {"prompt": record["prompt"], "completion": record["completion"]}
            elif kind == "preference" and isinstance(record, dict) and all(k in record for k in ("prompt", "chosen", "rejected")):
                if all(isinstance(record[k], str) and record[k].strip() for k in ("prompt", "chosen", "rejected")) or (isinstance(record["prompt"], list) and self.valid_messages(record["chosen"]) and self.valid_messages(record["rejected"])):
                    if record["chosen"] != record["rejected"]:
                        sample = {k: record[k] for k in ("prompt", "chosen", "rejected")}
            if sample:
                digest = hashlib.sha256(dump(sample).encode()).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                provenance = json.loads(block['provenance'] or '{}')
                origin = 'codex_prepared_example' if provenance.get('mode') == 'prepared_example' else 'chapter_model' if provenance.get('mode') == 'model' else 'chapter_demo' if provenance else 'source_extraction'
                sample["metadata"] = {"source_id": block["asset_id"], "source_name": block["asset_name"], "source_group": block['source_group'] or block['asset_id'], "source_document_id": provenance.get('source_document_id'), "provenance": provenance, "location": block["location"], "is_demo": bool(block["is_demo"]), "content_origin": origin, "license": block["license"]}
                samples.append(sample)
        mapping = self.split_map([s["metadata"]["source_group"] for s in samples])
        counts = {"train": 0, "validation": 0, "test": 0}
        for sample in samples:
            split = mapping[sample["metadata"]["source_group"]]
            sample["split"] = split
            counts[split] += 1
        if not samples:
            warnings.append("没有符合用途、质量及样本结构的记录。SFT/偏好样本须来自已导入的真实结构化记录；系统不会自动编造问答。")
        if excluded_tasks:
            warnings.append(f"正文预训练已排除 {excluded_tasks} 条 SFT/偏好任务样本；它们仅在对应任务类型中导出，rejected 回答不会混入正文语料。")
        if samples and (not counts["validation"] or not counts["test"]):
            warnings.append("资料组数量较少，90/5/5 按原文档分组后验证集或测试集为空；请导入更多独立资料。")
        if kind != "pretrain":
            warnings.append("结构化样本来自上传记录或已审批的章节结果；模型派生样本带有来源页与模型标识。")
        return {"samples": samples if full else samples[:8], "total": len(samples), "source_count": len(mapping), "split_counts": counts, "warnings": warnings, "type": kind}

    def training_export(self, body):
        with self.lock:
            preview = self.training_preview(body, full=True)
            if not preview["total"]:
                raise APIError("；".join(preview["warnings"][-2:]))
            version_id = new_id("version")
            name = str(body.get("name") or {"pretrain": "预训练正文", "sft": "SFT 对话", "preference": "偏好样本"}[preview["type"]])[:120]
            origins = sorted({s['metadata']['content_origin'] for s in preview['samples']})
            details = {"split_counts": preview["split_counts"], "warnings": preview["warnings"], "split_policy": "source_document_90_5_5", "content_origin": origins, "source_filter": body.get('source', 'all'), "source_count": preview['source_count'], "purpose": "training", "ai_generated": any(origin in {'chapter_model', 'codex_prepared_example'} for origin in origins), "platform_model_run": 'chapter_model' in origins}
            archive = self.export_dir / (version_id + ".zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                for split in ("train", "validation", "test"):
                    lines = []
                    for sample in preview["samples"]:
                        if sample["split"] == split:
                            payload = {k: v for k, v in sample.items() if k != "split"}
                            lines.append(dump(payload))
                    z.writestr(split + ".jsonl", "\n".join(lines) + ("\n" if lines else ""))
                z.writestr("manifest.json", json.dumps({"name": name, "type": preview["type"], "count": preview["total"], "details": details}, ensure_ascii=False, indent=2))
            with self.connect() as db:
                db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?)", (version_id, name, preview["type"], "JSONL", preview["total"], stamp(), "/api/downloads/" + version_id, dump(details), archive.relative_to(self.root).as_posix()))
                self.log(db, "export", f"导出 {name}：{preview['total']} 条样本，按文档分组划分，ZIP 包含三个 JSONL 与清单。")
                version = self.version_public(db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone())
            return {"version": version, "download_url": version["download_url"]}

    @staticmethod
    def terms(text):
        text = unicodedata.normalize("NFKC", text).lower()
        terms = re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", text)
        for run in re.findall(r"[\u3400-\u9fff]+", text):
            terms.extend(run)
            terms.extend(run[i:i + 2] for i in range(len(run) - 1))
        return set(terms)

    def publish_kb(self, body):
        try:
            size = int(body.get("chunk_size", 500))
        except (TypeError, ValueError):
            raise APIError("内容块长度必须为数字。")
        if not 100 <= size <= 4000:
            raise APIError("内容块长度须为 100–4000 字符。")
        with self.lock:
            with self.connect() as db:
                blocks, warnings = self.eligible(db, "kb", body.get('source', 'all'))
            source_blocks = []
            excluded_tasks = 0
            for block in blocks:
                record = json.loads(block["sample_json"]) if block["sample_json"] else None
                if self.task_kind(record) or block["kind"] in {"sft", "preference"}:
                    excluded_tasks += 1
                else:
                    source_blocks.append(block)
            blocks = source_blocks
            if excluded_tasks:
                warnings.append(f"知识库已排除 {excluded_tasks} 条 SFT/偏好任务样本；rejected 回答不会作为知识证据进入索引。")
            if not blocks:
                raise APIError("没有可发布的原始资料内容：请检查用途、授权及待复核状态。SFT/偏好任务样本仅通过对应训练出口导出。")
            version_id, name = new_id("version"), str(body.get("name") or "植物知识库")[:120]
            chunks, seen = [], set()
            for block in blocks:
                text = block["text"]
                for offset in range(0, len(text), size):
                    part = text[offset:offset + size]
                    if not part.strip():
                        continue
                    key = hashlib.sha256(part.encode()).hexdigest()
                    if key in seen:
                        continue
                    seen.add(key)
                    chunks.append({"id": new_id("chunk"), "asset_id": block["asset_id"], "asset_name": block["asset_name"], "source_document_id": block['source_group'], "provenance": json.loads(block['provenance'] or '{}'), "text": part, "location": block["location"] + (f" · 字符 {offset + 1}–{offset + len(part)}" if len(text) > size else ""), "terms": sorted(self.terms(part))})
            details = {"chunk_size": size, "document_count": len({c["source_document_id"] or c['asset_id'] for c in chunks}), "source_filter": body.get('source', 'all'), "ai_generated": any(c['provenance'].get('mode') in {'model', 'prepared_example'} for c in chunks), "platform_model_run": any(c['provenance'].get('mode') == 'model' for c in chunks), "index_type": "keyword", "retrieval": "中文字符/二元词及英文关键词检索；检索过程不生成回答", "warnings": warnings, "purpose": "kb", "snapshot": True}
            archive = self.export_dir / (version_id + ".zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("chunks.jsonl", "\n".join(dump(c) for c in chunks) + "\n")
                z.writestr("manifest.json", json.dumps({"name": name, "count": len(chunks), "details": details}, ensure_ascii=False, indent=2))
            with self.connect() as db:
                db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?)", (version_id, name, "kb", "关键词索引", len(chunks), stamp(), "/api/downloads/" + version_id, dump(details), archive.relative_to(self.root).as_posix()))
                db.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)", [(c["id"], version_id, c["asset_id"], c["asset_name"], c["text"], c["location"], dump(c["terms"])) for c in chunks])
                self.log(db, "publish", f"发布知识库 {name}：{len(chunks)} 个块；内容与关键词持久化为独立版本快照。")
                version = self.version_public(db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone())
            return {"version": version, "download_url": version["download_url"]}

    def search(self, body):
        query = body.get("query", "")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise APIError("请输入 1–500 字符的检索词。")
        with self.connect() as db:
            version_id = body.get("version_id")
            if version_id:
                version = db.execute("SELECT * FROM versions WHERE id=? AND type='kb'", (version_id,)).fetchone()
            else:
                version = db.execute("SELECT * FROM versions WHERE type='kb' ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
            if not version:
                if version_id:
                    raise APIError("知识库版本不存在。", 404)
                return {"results": [], "version_id": None, "message": "请先发布知识库版本。", "retrieval": "keyword"}
            # Snapshot contents are fixed; revoked source purposes are enforced now.
            chunks = list(db.execute("SELECT c.*,a.provenance,a.source_group FROM chunks c JOIN assets a ON c.asset_id=a.id WHERE c.version_id=? AND a.license IN ('self_owned','research_only') AND a.purpose IN ('both','kb')", (version["id"],)))
        query_terms = self.terms(query)
        if not query_terms:
            return {"results": [], "version_id": version["id"], "retrieval": "keyword"}
        indexed_terms = [set(json.loads(chunk["terms"])) for chunk in chunks]
        frequencies = {term: sum(term in terms for terms in indexed_terms) for term in query_terms}
        results = []
        for chunk, terms in zip(chunks, indexed_terms):
            matched = query_terms & terms
            if not matched:
                continue
            score = sum((2 if len(term) > 1 else 0.3) * math.log(1 + (len(chunks) + 1) / (frequencies[term] + 1)) for term in matched)
            score /= (1 + math.log(1 + len(chunk["text"]) / 500))
            if query.strip().lower() in chunk["text"].lower():
                score += 5
            results.append({"text": chunk["text"], "asset_name": chunk["asset_name"], "asset_id": chunk["asset_id"], "source_document_id": chunk['source_group'], "provenance": json.loads(chunk['provenance'] or '{}'), "location": chunk["location"], "score": round(score, 4), "matched_terms": sorted(matched), "retrieval": "keyword"})
        results.sort(key=lambda result: result["score"], reverse=True)
        return {"results": results[:10], "version_id": version["id"], "retrieval": "keyword"}

    def update_settings(self, body):
        name = body.get("project_name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise APIError("项目名称须为 1–80 字符。")
        with self.lock, self.connect() as db:
            db.execute("UPDATE settings SET value=? WHERE key='project_name'", (name.strip(),))
            self.log(db, "settings", "项目名称改为：" + name.strip())
        return {"settings": {"project_name": name.strip()}}


class Handler(BaseHTTPRequestHandler):
    server_version = "PlantDataWorkshop/1.0"

    def log_message(self, format, *args):
        if os.getenv("PLANT_VERBOSE"):
            super().log_message(format, *args)

    @property
    def workshop(self):
        return self.server.workspace_manager.active_workspace

    @property
    def chapters(self):
        return self.server.workspace_manager.active_chapters

    @property
    def library(self):
        return self.server.workspace_manager.active_library

    @property
    def platform(self):
        return self.server.workspace_manager.active_platform

    @property
    def platform_data(self):
        return self.server.workspace_manager.active_platform_data

    def uses_platform_data(self, path):
        return path in {'/api/data-hub', '/api/data-links', '/api/search'} or path.startswith(('/api/data-hub/', '/api/ocr/', '/api/packages'))

    def validate_request(self, mutation=False):
        try:
            if len(self.headers.get_all("Host", [])) != 1:
                raise ValueError("Invalid Host")
            authority = parse_authority(self.headers["Host"])
            if authority[0] not in self.server.allowed_hostnames or authority[1] != self.server.server_port:
                raise ValueError("Unregistered Host")
            self.request_authority = authority
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1:
                raise ValueError("Invalid Origin")
            if origins:
                origin = parse_http_origin(origins[0])
                if origin != authority:
                    # A development proxy is a deliberate, registered exception
                    # to the port match. It must arrive over loopback and retain
                    # the browser authority and client address for setup checks.
                    forwarded = self.headers.get("X-Forwarded-Host", "")
                    clients = self.headers.get("X-Forwarded-For", "")
                    if (not is_loopback_address(self.client_address[0])
                            or origin not in self.server.proxy_origins
                            or authority[0] not in LOOPBACK_HOSTS
                            or parse_authority(forwarded) != origin
                            or not clients
                            or self.headers.get("X-Forwarded-Proto", "http") != "http"):
                        raise ValueError("Unregistered proxy origin")
                    for client in clients.split(","):
                        ipaddress.ip_address(client.strip())
        except (TypeError, ValueError):
            raise APIError("请求 Host 或来源不在此服务允许的本机/局域网地址内。", 403)

    def local_setup_allowed(self):
        if not is_loopback_address(self.client_address[0]) or self.request_authority[0] not in LOOPBACK_HOSTS:
            return False
        try:
            origin = self.headers.get("Origin")
            if origin and parse_http_origin(origin)[0] not in LOOPBACK_HOSTS:
                return False
            forwarded_host = self.headers.get("X-Forwarded-Host")
            if forwarded_host and parse_authority(forwarded_host)[0] not in LOOPBACK_HOSTS:
                return False
            clients = self.headers.get("X-Forwarded-For")
            if clients and not all(is_loopback_address(client.strip()) for client in clients.split(",")):
                return False
        except ValueError:
            return False
        return True

    def local_setup_url(self):
        # Same-origin GET fetches normally have no Origin, so consult the
        # forwarded host as well when the explicit development proxy is used.
        if is_loopback_address(self.client_address[0]):
            for value in (self.headers.get("Origin"), self.headers.get("X-Forwarded-Host")):
                if not value:
                    continue
                try:
                    authority = parse_http_origin(value) if value.startswith("http://") else parse_authority(value)
                    if authority in self.server.proxy_origins:
                        return "http://127.0.0.1:" + str(authority[1])
                except ValueError:
                    pass
        return "http://127.0.0.1:" + str(self.server.server_port)

    def json_response(self, payload, status=200):
        cookie = payload.pop('_cookie', None) if isinstance(payload, dict) else None
        if hasattr(self.server, 'platform'):
            payload = self.platform.redact(payload, getattr(self, 'actor', None))
        content = dump(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def platform_response(self, payload):
        if isinstance(payload, dict) and '_binary' in payload:
            from urllib.parse import quote
            content = payload['_binary']
            self.send_response(200)
            self.send_header('Content-Type', payload.get('content_type', 'application/octet-stream'))
            self.send_header('Content-Length', str(len(content)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            filename = payload.get('filename')
            if filename:
                self.send_header('Content-Disposition', ('inline' if payload.get('inline') else 'attachment') + "; filename*=UTF-8''" + quote(filename))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(content)
            return
        return self.json_response(payload)

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise APIError("不支持分块请求体。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError("请求长度无效。")
        if not 0 < length <= MAX_BODY:
            raise APIError("请求为空或超过大小限制。", 413)
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise APIError("请求须使用 application/json。", 415)
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise APIError("JSON 请求格式无效。")
        if not isinstance(parsed, dict):
            raise APIError("JSON 请求须为对象。")
        return parsed

    def file_response(self, path, content_type=None, download_name=None):
        if not path.is_file():
            raise APIError("文件不存在。", 404)
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name:
            from urllib.parse import quote
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download_name))
        self.end_headers()
        if self.command != "HEAD":
            with path.open("rb") as stream:
                while data := stream.read(64 * 1024):
                    self.wfile.write(data)

    def zip_response(self, content, name):
        from urllib.parse import quote
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(name))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def pdf_response(self, path, name):
        from urllib.parse import quote
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested.strip())
            valid = bool(match and (match[1] or match[2]))
            if valid:
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]) if match[2] else size - 1, size - 1)
                else:
                    length = int(match[2])
                    start = max(0, size - length)
                    valid = length > 0
                valid = valid and 0 <= start <= end < size
            if not valid:
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */' + str(size))
                self.send_header('Content-Length', '0')
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Disposition', "inline; filename*=UTF-8''" + quote(name))
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'private, no-cache')
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command != 'HEAD':
            with path.open('rb') as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    content = source.read(min(remaining, 65536))
                    if not content:
                        break
                    self.wfile.write(content)
                    remaining -= len(content)

    def do_HEAD(self):
        self.do_GET()

    def do_DELETE(self):
        try:
            self.validate_request(mutation=True)
            path = urlsplit(self.path).path
            self.actor = self.platform.current_user(self.headers.get('Cookie'))
            self.platform.authorize(path, self.command, self.actor)
            # 工作空间删除（admin only）
            if path.startswith("/api/workspaces/"):
                ws_id = path.rsplit("/", 1)[-1]
                self.server.workspace_manager.delete_workspace(ws_id)
                return self.json_response({"ok": True})
            platform_result = self.platform.dispatch(self.command, path, None, self.actor, self.headers.get('Cookie', ''))
            if platform_result is not None:
                return self.platform_response(platform_result)
            data_result = self.server.platform_data.dispatch(self.command, path, None, self.actor) if self.uses_platform_data(path) else None
            if data_result is not None:
                self.platform.audit_http_action(self.actor, path, {}, data_result)
                return self.platform_response(data_result)
            raise APIError("接口不存在。", 404)
        except APIError as exc:
            self.json_response({"error": str(exc), "code": exc.code}, exc.code)
        except Exception:
            import traceback
            traceback.print_exc()
            self.json_response({"error": "服务处理失败，请稍后重试。"}, 500)

    def do_GET(self):
        try:
            self.validate_request()
            path = unquote(urlsplit(self.path).path)
            self.actor = self.platform.current_user(self.headers.get('Cookie'))
            self.platform.authorize(path, self.command, self.actor)
            platform_result = self.platform.dispatch(self.command, path, None, self.actor, self.headers.get('Cookie', ''))
            if platform_result is not None:
                if path == '/api/platform/bootstrap':
                    platform_result.update(local_setup_allowed=self.local_setup_allowed(), local_setup_url=self.local_setup_url())
                return self.platform_response(platform_result)
            data_result = self.server.platform_data.dispatch('GET' if self.command == 'HEAD' else self.command, path, None, self.actor) if self.uses_platform_data(path) else None
            if data_result is not None:
                return self.platform_response(data_result)
            if path == "/api/health":
                return self.json_response({"ok": True, "service": "plant-data-workshop", "version": "2.1.0", "lan_enabled": self.server.lan_enabled, "access_urls": self.server.access_urls})
            if path == "/api/state":
                return self.json_response(
                    self.workshop.state()
                    | {'chapter_summary': self.chapters.summary()}
                    | {'workspaces': self.server.workspace_manager.list_workspaces(),
                       'active_workspace': self.server.workspace_manager.active_id}
                )
            if path == "/api/workspaces":
                return self.json_response({
                    "workspaces": self.server.workspace_manager.list_workspaces(),
                    "active": self.server.workspace_manager.active_id,
                })
            if path == '/api/chapters/state':
                return self.json_response(self.chapters.state())
            if path == '/api/library/state':
                return self.json_response(self.library.state())
            match = re.fullmatch(r'/api/library/records/(record_[a-z0-9_]+)', path)
            if match:
                return self.json_response(self.library.record_detail(match[1]))
            match = re.fullmatch(r'/api/chapters/history/(chapter_[a-f0-9]+)', path)
            if match:
                return self.json_response(self.chapters.history(match[1]))
            match = re.fullmatch(r'/api/chapters/pdfs/(document_[a-f0-9]+)/(pdf_[a-f0-9]+)', path)
            if match:
                pdf_path, name = self.chapters.pdf_file(match[1], match[2])
                return self.pdf_response(pdf_path, name)
            match = re.fullmatch(r'/api/chapters/documents/(document_[a-f0-9]+)', path)
            if match:
                return self.json_response(self.chapters.detail(match[1]))
            match = re.fullmatch(r'/api/chapters/images/(document_[a-f0-9]+)/(image_[a-f0-9]+)', path)
            if match:
                image_path, mime = self.chapters.image_file(match[1], match[2])
                return self.file_response(image_path, mime)
            match = re.fullmatch(r'/api/chapters/download/(document_[a-f0-9]+)', path)
            if match:
                stage = parse_qs(urlsplit(self.path).query).get('stage', ['corpus'])[0]
                content, name = self.chapters.download(match[1], stage)
                return self.zip_response(content, name)
            match = re.fullmatch(r"/api/assets/(asset_[a-f0-9]+)", path)
            if match:
                return self.json_response(self.workshop.asset_detail(match[1]))
            match = re.fullmatch(r"/api/files/(asset_[a-f0-9]+)", path)
            if match:
                with self.workshop.connect() as db:
                    row = db.execute("SELECT path,name FROM assets WHERE id=?", (match[1],)).fetchone()
                if not row:
                    raise APIError("原文件不存在。", 404)
                return self.file_response(self.workshop.local_path(row["path"]), download_name=row["name"])
            match = re.fullmatch(r"/api/(?:downloads|versions)/(version_[a-f0-9]+)(?:/download)?", path)
            if match:
                with self.workshop.connect() as db:
                    row = db.execute("SELECT path,name FROM versions WHERE id=?", (match[1],)).fetchone()
                if not row:
                    raise APIError("版本不存在。", 404)
                return self.file_response(self.workshop.local_path(row["path"]), "application/zip", row["name"] + ".zip")
            if path.startswith("/api/"):
                raise APIError("接口不存在。", 404)
            static_root = (APP_ROOT / "dist" / "client").resolve()
            target = (static_root / path.lstrip("/")).resolve()
            if not target.is_relative_to(static_root) or "\x00" in path or "\\" in path:
                raise APIError("路径无效。", 403)
            if not target.is_file():
                if Path(path).suffix:
                    raise APIError("静态文件不存在。", 404)
                target = static_root / "index.html"
            if not target.is_file():
                return self.json_response({"service": "plant-data-workshop", "message": "后端已运行；前端构建目录 dist/client 尚未准备。"})
            return self.file_response(target)
        except APIError as exc:
            self.json_response({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.json_response({"error": "服务处理失败，请检查文件与操作参数。"}, 500)

    def do_POST(self):
        try:
            self.validate_request(mutation=True)
            path = urlsplit(self.path).path
            if path == '/api/platform/setup' and not self.local_setup_allowed():
                raise APIError("首次管理员只能在运行服务的电脑上，通过本机地址创建。", 403)
            self.actor = self.platform.current_user(self.headers.get('Cookie'))
            self.platform.authorize(path, self.command, self.actor)
            body = self.body()
            if 'extraction_schema' in body and re.fullmatch(r'/api/chapters/documents/document_[a-f0-9]+', path):
                self.platform.require_role(self.actor, 'admin')
            batch_id = self.platform.mutation_batch_context(path, body)
            if path in {'/api/training/export', '/api/kb/publish', '/api/chapters/materialize'}:
                raise APIError('请先对批次分层抽样验收，再通过数据包接口生成产出；旧逐章或直接导出接口已停止使用。', 409)
            platform_result = self.platform.dispatch(self.command, path, body, self.actor, self.headers.get('Cookie', ''))
            if platform_result is not None:
                return self.platform_response(platform_result)
            data_result = self.server.platform_data.dispatch(self.command, path, body, self.actor) if self.uses_platform_data(path) else None
            if data_result is not None:
                self.platform.audit_http_action(self.actor, path, body, data_result)
                return self.platform_response(data_result)
            if path == '/api/library/sources':
                result = self.library.create_source(body)
            elif path == '/api/library/records':
                result = self.library.create_record(body)
            elif path == '/api/library/link':
                result = self.library.link(body)
            elif path == '/api/library/unlink':
                result = self.library.link(body, remove=True)
            elif re.fullmatch(r'/api/library/sources/source_[a-z0-9_]+', path):
                result = self.library.update_source(path.rsplit('/', 1)[1], body)
            elif re.fullmatch(r'/api/library/records/record_[a-z0-9_]+', path):
                result = self.library.update_record(path.rsplit('/', 1)[1], body)
            elif path == '/api/chapters/import':
                result = self.chapters.import_package(body)
            elif path == '/api/chapters/demo':
                result = self.chapters.demo(body)
            elif path == '/api/chapters/real-demo':
                result = self.chapters.real_demo(body)
            elif path == '/api/chapters/model':
                result = self.chapters.configure(body)
            elif path == '/api/chapters/rebuild':
                result = self.chapters.rebuild(body)
            elif path == '/api/chapters/run':
                result = self.platform.run_chapter(body, self.actor)
            elif path == '/api/chapters/review':
                result = self.chapters.review(body)
            elif path == '/api/chapters/revise':
                result = self.chapters.revise(body)
            elif path == '/api/chapters/materialize':
                result = self.chapters.materialize(body)
            elif re.fullmatch(r'/api/chapters/documents/document_[a-f0-9]+', path):
                result = self.chapters.update_document(path.rsplit('/', 1)[1], body)
            elif re.fullmatch(r'/api/chapters/documents/document_[a-f0-9]+/pdfs', path):
                result = self.chapters.attach_pdf(path.split('/')[-2], body)
            elif path == "/api/import":
                result = self.workshop.import_files(body)
            elif path == "/api/jobs":
                result = {"job": self.workshop.process(body.get("asset_ids")), "state": self.workshop.state()}
            elif re.fullmatch(r"/api/assets/asset_[a-f0-9]+", path):
                result = self.workshop.update_asset(path.rsplit("/", 1)[1], body)
            elif re.fullmatch(r"/api/reviews/review_[a-f0-9]+", path):
                result = self.workshop.review(path.rsplit("/", 1)[1], body)
            elif path == "/api/training/preview":
                result = self.workshop.training_preview(body)
            elif path == "/api/training/export":
                result = self.workshop.training_export(body)
            elif path == "/api/kb/publish":
                result = self.workshop.publish_kb(body)
            elif path == "/api/kb/search":
                result = self.workshop.search(body)
            elif path == "/api/settings":
                result = self.workshop.update_settings(body)
            elif path == "/api/workspaces":
                result = self.server.workspace_manager.create_workspace(body.get("name", ""))
            elif path.startswith("/api/workspaces/") and path.endswith("/activate"):
                ws_id = path.rsplit("/", 2)[-2]
                self.server.workspace_manager.activate_workspace(ws_id)
                result = {"ok": True, "active": ws_id}
            else:
                raise APIError("接口不存在。", 404)
            self.platform.audit_http_action(self.actor, path, body, result)
            self.json_response(result)
        except APIError as exc:
            self.json_response({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.json_response({"error": "服务处理失败，请检查文件与操作参数。"}, 500)


class WorkshopServer(ThreadingHTTPServer):
    def server_close(self):
        if hasattr(self, 'platform_data'):
            self.platform_data.close()
        if hasattr(self, 'platform'):
            self.platform.close()
        if hasattr(self, 'chapters'):
            self.chapters.close()
        super().server_close()


def create_server(host=None, port=8766, data_dir=None, seed=True, *, lan=False, allowed_hosts=None, proxy_origins=None):
    host = host or ("0.0.0.0" if lan else "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        try:
            address = ipaddress.ip_address(host)
            if not lan or address.version != 4 or address.is_multicast:
                raise ValueError()
        except ValueError:
            raise ValueError("非本机绑定须显式启用 --lan，并使用 IPv4 地址。") from None
    extra_hosts = [allowed_hosts] if isinstance(allowed_hosts, str) else list(allowed_hosts or [])
    local_addresses = local_ipv4_addresses() if lan or proxy_origins else []
    local_names = LOOPBACK_HOSTS | set(local_addresses)
    for value in extra_hosts:
        try:
            address = ipaddress.ip_address(value)
            if address.is_unspecified or address.is_multicast:
                raise ValueError()
            local_names.add(str(address))
        except ValueError:
            raise ValueError("--allow-host 只接受明确的 IP 地址，不接受域名或通配符。") from None
    if host not in {"0.0.0.0", "localhost"}:
        local_names.add(host)
    configured_origins = set()
    for value in ([proxy_origins] if isinstance(proxy_origins, str) else proxy_origins or []):
        try:
            origin = parse_http_origin(value)
            if origin[0] not in local_names:
                raise ValueError()
            configured_origins.add(origin)
        except ValueError:
            raise ValueError("--proxy-origin 须为本机/已允许 IP 的完整 http://地址:端口。") from None
    server = WorkshopServer((host, port), Handler)
    server.daemon_threads = True
    server.lan_enabled = bool(lan)
    server.allowed_hostnames = local_names if lan else LOOPBACK_HOSTS
    server.proxy_origins = configured_origins
    server.access_urls = ["http://127.0.0.1:" + str(server.server_port)]
    if lan and host not in {"127.0.0.1", "localhost"}:
        primary = host if host != "0.0.0.0" else (local_addresses[0] if local_addresses else (extra_hosts[0] if extra_hosts else None))
        if primary:
            server.access_urls.append("http://" + primary + ":" + str(server.server_port))
    base_data_dir = Path(data_dir or os.environ.get("PLANT_DATA_DIR") or APP_ROOT / "data")
    manager = WorkspaceManager(base_data_dir)

    def _build_all(ws_dir, seed=False):
        try:
            from .chapter_pipeline import ChapterPipeline as _CP
            from .platform_core import PlatformCore as _PC
            from .platform_data import PlatformData as _PD
        except ImportError:
            from chapter_pipeline import ChapterPipeline as _CP
            from platform_core import PlatformCore as _PC
            from platform_data import PlatformData as _PD
        workshop = Workshop(ws_dir, seed=seed)
        chapters = _CP(workshop, APIError)
        library = chapters.library
        platform = _PC(workshop, chapters, APIError)
        platform_data = _PD(workshop, chapters, platform, APIError)
        return {
            "workshop": workshop,
            "chapters": chapters,
            "library": library,
            "platform": platform,
            "platform_data": platform_data,
        }

    manager.set_builders({"build_all": _build_all})
    # 初始化 active workspace 的完整模块树（首次启动时 seed=True 创建 demo 数据）
    manager._get_instances(manager.active_id, seed=True)
    server.workspace_manager = manager
    # 兼容 handler 的快捷属性
    server.workshop = manager.active_workspace
    server.chapters = manager.active_chapters
    server.library = manager.active_library
    server.platform = manager.active_platform
    server.platform_data = manager.active_platform_data
    return server


def main():
    parser = argparse.ArgumentParser(description="本地植物科研数据工坊")
    parser.add_argument("--host", help="绑定地址；默认127.0.0.1，--lan 时默认0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--lan", action="store_true", help="显式允许通过本机局域网 IP 访问（仍需登录）")
    parser.add_argument("--allow-host", action="append", default=[], metavar="IP", help="补充允许的本机 IP（可重复）")
    parser.add_argument("--proxy-origin", action="append", default=[], metavar="URL", help="注册本机开发代理来源，如 http://192.168.1.10:5173")
    args = parser.parse_args()
    try:
        server = create_server(args.host, args.port, seed=not args.no_seed, lan=args.lan, allowed_hosts=args.allow_host, proxy_origins=args.proxy_origin)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print("植物科研数据工坊：" + "；".join(server.access_urls), flush=True)
    if args.lan:
        print("局域网访问已启用；首次管理员须在服务电脑通过本机地址创建，其他电脑随后使用账号登录。", flush=True)
        if len(server.access_urls) == 1:
            print("未识别到主局域网 IP；可使用 --allow-host 本机IP 指定访问地址。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

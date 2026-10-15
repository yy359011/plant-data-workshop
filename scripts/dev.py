#!/usr/bin/env python3
"""Run the source frontend and Python API together, without system installs."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 10)
MIN_NODE = (22, 13, 0)  # pdfjs-dist 6.4.299 is stricter than Vite itself.


class LaunchError(RuntimeError):
    pass


def version_tuple(text):
    match = re.search(r"(?:^|\s)v?(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        raise LaunchError(f"无法识别运行时版本：{text.strip()}")
    return tuple(int(part) for part in match.groups())


def require_runtimes():
    if sys.version_info < MIN_PYTHON:
        raise LaunchError("需要 Python 3.10 或更高版本。请安装 Python 后重新打开启动窗口。")
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise LaunchError("未找到 Node.js / npm。请安装 Node.js 22.13.0 或更高版本，并重新打开启动窗口。")
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise LaunchError(f"无法运行 Node.js：{error}") from error
    version = version_tuple(result.stdout)
    if version < MIN_NODE:
        raise LaunchError(f"当前 Node.js {result.stdout.strip()}；PDF 组件要求 22.13.0 或更高版本。")
    # Execute npm's JS entry point directly. This avoids cmd.exe argument parsing
    # for Unicode, spaces and shell metacharacters in the project directory.
    npm_candidates = [Path(node).parent / "node_modules/npm/bin/npm-cli.js",
                      Path(npm).resolve().parent / "npm-cli.js",
                      Path(npm).resolve().parent / "node_modules/npm/bin/npm-cli.js"]
    npm_cli = next((path for path in npm_candidates if path.is_file()), None)
    if not npm_cli:
        raise LaunchError("找到 npm 命令，但缺少 npm-cli.js。请修复包含 npm 的 Node.js 安装。")
    return {"node": node, "npm": [node, str(npm_cli)], "node_version": version}


def required_files(root):
    return [root / name for name in ("package.json", "package-lock.json", "vite.config.mjs",
            "index.html", "src/main.ts", "backend/server.py", "scripts/prepare-pdf-assets.mjs",
            "requirements-optional.txt")]


def check_sources(root):
    missing = [str(path.relative_to(root)) for path in required_files(root) if not path.is_file()]
    if missing:
        raise LaunchError("源码包不完整，缺少：" + "、".join(missing))


def venv_python(root):
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def dependency_hash(root, names, runtime):
    digest = hashlib.sha256(str(runtime).encode())
    for name in names:
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def read_stamp(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def dependency_status(root, runtime):
    python_hash = dependency_hash(root, ["requirements-optional.txt"], (sys.version_info[:2], platform.system(), platform.machine()))
    node_hash = dependency_hash(root, ["package.json", "package-lock.json"], (runtime["node_version"], platform.system(), platform.machine()))
    python_ready = venv_python(root).is_file() and read_stamp(root / ".venv/.workshop-deps") == python_hash
    node_ready = (root / "node_modules/vite/bin/vite.js").is_file() and (root / "node_modules/pdfjs-dist/package.json").is_file() and read_stamp(root / "node_modules/.workshop-deps") == node_hash
    return python_ready, node_ready, python_hash, node_hash


class WindowsJob:
    """Kill every child if the launcher exits, including console-window close."""
    def __init__(self):
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise LaunchError("无法创建 Windows 子进程管理器；请在普通终端中启动，以保证退出时服务能一并停止。")

    def add(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            process.wait()
            raise LaunchError("Windows 拒绝管理启动进程；已停止该进程，请在普通终端中重试。")

    def close(self):
        if getattr(self, "handle", None):
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Children:
    def __init__(self, root, env):
        self.root, self.env, self.processes = root, env, []
        self.job = WindowsJob() if os.name == "nt" else None

    def spawn(self, command):
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen([str(arg) for arg in command], cwd=self.root, env=self.env, **options)
        self.processes.append(process)
        if self.job:
            self.job.add(process)
        return process

    def run(self, command, label):
        print(label, flush=True)
        if self.spawn(command).wait() != 0:
            raise LaunchError(f"{label}失败。请查看上方错误；修复网络/依赖问题后重新双击启动。")

    def close(self):
        if self.job:
            self.job.close()
        else:
            # Signal groups even if their leader exited, so npm hook children
            # or an unexpectedly detached backend worker cannot be left running.
            for process in reversed(self.processes):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in reversed(self.processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait(timeout=5)


def install_dependencies(root, runtime, children, skip_install=False):
    python_ready, node_ready, python_hash, node_hash = dependency_status(root, runtime)
    python = venv_python(root)
    if skip_install:
        python = python if python.is_file() else Path(sys.executable)
        if not (root / "node_modules/vite/bin/vite.js").is_file() or not (root / "node_modules/pdfjs-dist/package.json").is_file():
            raise LaunchError("--skip-install 需要已存在的 node_modules；请先正常启动完成 npm ci。")
        children.run([python, "-c", "import sys; assert sys.version_info >= (3, 10); import pypdf, openpyxl"], "使用已有 Python 依赖（跳过安装）……")
        children.run([runtime["node"], root / "scripts/prepare-pdf-assets.mjs"], "准备本地 PDF 渲染资源（predev）……")
        return python
    if not python.is_file():
        children.run([sys.executable, "-m", "venv", root / ".venv"], "创建项目 Python 虚拟环境……")
    if not python_ready:
        children.run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", root / "requirements-optional.txt"], "安装 PDF / Excel 解析依赖……")
        (root / ".venv/.workshop-deps").write_text(python_hash, encoding="utf-8")
    if not node_ready:
        children.run(runtime["npm"] + ["ci", "--no-audit", "--no-fund"], "按锁定文件安装前端依赖（npm ci）……")
        (root / "node_modules/.workshop-deps").write_text(node_hash, encoding="utf-8")
    children.run([python, "-c", "import pypdf, openpyxl"], "检查 Python 解析组件……")
    children.run([runtime["node"], root / "scripts/prepare-pdf-assets.mjs"], "准备本地 PDF 字体和渲染资源（predev）……")
    return python


def require_free_port(host, port, label):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError as error:
        raise LaunchError(f"{label}端口 {host}:{port} 不可用：{error}。请关闭占用程序，或指定其他 --port / --api-port。") from error


def proxy_origins(port, lan):
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if lan:
        sys.path.insert(0, str(ROOT))
        try:
            from backend.server import local_ipv4_addresses
            addresses = local_ipv4_addresses()
        finally:
            sys.path.pop(0)
        origins.extend(f"http://{address}:{port}" for address in addresses if not address.startswith("127."))
    return list(dict.fromkeys(origins))


def backend_command(root, python, api_port, origins):
    command = [str(python), str(root / "backend/server.py"), "--host", "127.0.0.1", "--port", str(api_port)]
    for origin in origins:
        command.extend(["--proxy-origin", origin])
    return command


def wait_ready(process, url, label, timeout=40, health=False):
    opener = build_opener(ProxyHandler({}))  # Loopback readiness must not use a system HTTP proxy.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LaunchError(f"{label}已退出（退出码 {process.returncode}），请查看上方日志。")
        try:
            with opener.open(url, timeout=0.7) as response:
                if health:
                    result = json.load(response)
                    if result.get("ok") is True and result.get("service") == "plant-data-workshop":
                        return
                elif response.status == 200:
                    return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.15)
    raise LaunchError(f"{label}未在 {timeout} 秒内就绪，请查看上方日志。")


def positive_port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("端口须为 1–65535 的整数")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口须为 1–65535 的整数")
    return port


def parser():
    result = argparse.ArgumentParser(description="源码启动：Python API + Vite 热更新。Windows 双击 start-dev.cmd。")
    result.add_argument("--lan", dest="lan", action="store_true", help="允许同局域网通过本机 IPv4 访问前端")
    result.add_argument("--local", dest="lan", action="store_false", help="仅允许本机访问（可覆盖启动脚本的 --lan）")
    result.set_defaults(lan=False)
    result.add_argument("--port", "--frontend-port", type=positive_port, default=5173, help="Vite 端口，默认 5173")
    result.add_argument("--api-port", "--backend-port", type=positive_port, default=8766, help="本机 API 端口，默认 8766")
    result.add_argument("--data-dir", type=Path, help="持久数据目录，默认项目 data/；也可设 PLANT_DATA_DIR")
    result.add_argument("--check", action="store_true", help="离线只读检查文件和运行时；不安装依赖、不启动、不写数据")
    result.add_argument("--skip-install", action="store_true", help="高级验收：只用已有依赖；无 .venv 时使用当前 Python，不下载或安装")
    result.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    children = None
    previous_term = None
    try:
        if args.port == args.api_port:
            raise LaunchError("前端端口与 API 端口不能相同。")
        check_sources(ROOT)
        runtime = require_runtimes()
        print(f"Python {sys.version.split()[0]}；Node.js {'.'.join(map(str, runtime['node_version']))}", flush=True)
        if args.check:
            python_ready, node_ready, _, _ = dependency_status(ROOT, runtime)
            print(f"源码与运行时检查通过。Python 依赖：{'已准备' if python_ready else '首次启动时安装'}；前端依赖：{'已准备' if node_ready else '首次启动时 npm ci'}。")
            print("此检查未安装依赖、未启动服务，也未写入数据。Windows 控制台/防火墙仍需在目标电脑验证。")
            return 0
        host = "0.0.0.0" if args.lan else "127.0.0.1"
        require_free_port("127.0.0.1", args.api_port, "API ")
        require_free_port(host, args.port, "前端 ")
        env = os.environ.copy()
        env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PLANT_API_PORT": str(args.api_port), "PLANT_DEV_PORT": str(args.port), "PLANT_DEV_HOST": host})
        data_dir = (args.data_dir or Path(env.get("PLANT_DATA_DIR", str(ROOT / "data")))).expanduser().resolve()
        env["PLANT_DATA_DIR"] = str(data_dir)
        children = Children(ROOT, env)
        def interrupted(_signum, _frame):
            raise KeyboardInterrupt
        previous_term = signal.signal(signal.SIGTERM, interrupted)
        python = install_dependencies(ROOT, runtime, children, skip_install=args.skip_install)
        # Installation can take time. Recheck before either service is started.
        require_free_port("127.0.0.1", args.api_port, "API ")
        require_free_port(host, args.port, "前端 ")
        origins = proxy_origins(args.port, args.lan)
        api = children.spawn(backend_command(ROOT, python, args.api_port, origins))
        wait_ready(api, f"http://127.0.0.1:{args.api_port}/api/health", "Python API", health=True)
        frontend = children.spawn([runtime["node"], ROOT / "node_modules/vite/bin/vite.js", "--host", host, "--port", str(args.port), "--strictPort"])
        local_url = f"http://127.0.0.1:{args.port}"
        wait_ready(frontend, local_url, "Vite")
        print(f"\n本机入口：{local_url}\n数据目录：{data_dir}", flush=True)
        if args.lan:
            for origin in origins[2:]:
                print(f"局域网入口：{origin}", flush=True)
            print("首次管理员创建请使用本机入口；同事使用局域网入口登录。Windows 如提示防火墙，请只允许专用网络。", flush=True)
        print("修改 src/ 会热更新；修改 Python 文件后需停止并重新启动。按 Ctrl-C 同时停止两个服务。", flush=True)
        if not args.no_browser:
            webbrowser.open(local_url)
        while True:
            for label, process in (("Python API", api), ("Vite", frontend)):
                if process.poll() is not None:
                    raise LaunchError(f"{label}已退出（退出码 {process.returncode}）；正在停止另一服务。")
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n正在停止源码开发服务……", flush=True)
        return 0
    except (LaunchError, OSError) as error:
        print(f"启动失败：{error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if children:
            # A second Ctrl-C must not interrupt cleanup and orphan services.
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                children.close()
            finally:
                signal.signal(signal.SIGINT, previous)
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())

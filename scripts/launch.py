#!/usr/bin/env python3
"""Start the prebuilt local workshop using Python's standard library only."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8766
URL = f"http://{HOST}:{PORT}"


def workshop_health(port=PORT):
    try:
        with urlopen(f"http://{HOST}:{port}/api/health", timeout=1) as response:
            result = json.load(response)
        if result.get("ok") is True and result.get("service") == "plant-data-workshop":
            return result
    except (OSError, ValueError, HTTPError, URLError):
        pass
    return None


def existing_workshop(port=PORT):
    return workshop_health(port) is not None


def port_in_use(port=PORT):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((HOST, port)) == 0


def show_addresses(health):
    for url in health.get("access_urls", []):
        print(("本机地址：" if url.startswith("http://127.0.0.1:") else "局域网地址：") + url, flush=True)
    if health.get("lan_enabled"):
        print("首次管理员须在服务电脑的本机地址创建；局域网用户随后使用账号登录。", flush=True)


def main():
    parser = argparse.ArgumentParser(description="启动植物科研数据工坊的本地服务。")
    parser.add_argument("--no-browser", action="store_true", help="启动服务但不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="仅检查运行文件，不启动服务")
    parser.add_argument("--lan", action="store_true", help="通过本机局域网 IP 开放访问，仍需账号登录")
    parser.add_argument("--port", type=int, default=PORT, help="服务端口，默认8766")
    parser.add_argument("--allow-host", action="append", default=[], metavar="IP", help="补充允许的本机 IP（可重复）")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口须为 1–65535。")
    url = f"http://{HOST}:{args.port}"
    required = [ROOT / "backend" / "server.py", ROOT / "dist" / "client" / "index.html"]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        print("运行文件缺失：" + "、".join(missing), file=sys.stderr)
        print("请使用完整交付目录，或按 README 从源码构建。", file=sys.stderr)
        return 1
    if args.check:
        print(f"运行文件完整；Python {sys.version.split()[0]}；本地地址 {url}；局域网开关 {'开启' if args.lan else '关闭'}")
        return 0
    health = workshop_health(args.port)
    if health:
        if args.lan and not health.get("lan_enabled"):
            print("此端口的数据工坊以本机模式运行，请先关闭原启动窗口，再使用 --lan 启动。", file=sys.stderr)
            return 1
        print(f"数据工坊已在运行：{url}")
        show_addresses(health)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if port_in_use(args.port):
        print(f"端口 {args.port} 被其他程序占用；请先关闭占用服务后重试。", file=sys.stderr)
        return 1

    print(f"启动植物科研数据工坊：{url}", flush=True)
    data_dir = Path(os.environ.get("PLANT_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
    print(f"数据目录：{data_dir}；关闭此窗口或按 Control-C 停止服务。", flush=True)
    command = [sys.executable, str(ROOT / "backend" / "server.py"), "--port", str(args.port)]
    if args.lan:
        command.append("--lan")
    for address in args.allow_host:
        command.extend(["--allow-host", address])
    child = subprocess.Popen(
        command,
        cwd=ROOT,
        env=os.environ.copy(),
    )
    try:
        for _ in range(60):
            if child.poll() is not None:
                print("服务启动失败；请查看上方错误信息。", file=sys.stderr)
                return child.returncode or 1
            health = workshop_health(args.port)
            if health:
                if not args.no_browser:
                    webbrowser.open(url)
                show_addresses(health)
                print("服务已就绪。", flush=True)
                return child.wait()
            time.sleep(0.2)
        print("服务未在预期时间内就绪，请查看上方日志。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n正在停止本地服务……", flush=True)
        return 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())

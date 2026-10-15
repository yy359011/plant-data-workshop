"""Source launcher contracts; no dependency downloads or real application data."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import dev


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="植物 启动检查 ")
        self.root = Path(self.temp.name)
        for path in dev.required_files(self.root):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        self.runtime = {"node": "/runtime path/node", "npm": ["/runtime path/node", "/runtime path/npm-cli.js"], "node_version": (22, 13, 0)}

    def tearDown(self):
        self.temp.cleanup()

    def test_version_floor_matches_locked_pdf_engine_and_cli_modes(self):
        self.assertEqual(dev.version_tuple("v22.13.0\n"), (22, 13, 0))
        lock = json.loads((dev.ROOT / "package-lock.json").read_text())
        package = json.loads((dev.ROOT / "package.json").read_text())
        self.assertEqual(lock["version"], package["version"])
        self.assertEqual(lock["packages"][""]["engines"], package["engines"])
        self.assertIn("22.13.0", lock["packages"]["node_modules/pdfjs-dist"]["engines"]["node"])
        self.assertEqual(dev.MIN_NODE, (22, 13, 0))
        args = dev.parser().parse_args(["--lan", "--local", "--frontend-port", "5191", "--backend-port", "8791"])
        self.assertFalse(args.lan)
        self.assertEqual((args.port, args.api_port), (5191, 8791))
        with self.assertRaises(Exception):
            dev.positive_port("0")

    def test_missing_or_unsupported_node_is_actionable(self):
        with mock.patch.object(dev.shutil, "which", return_value=None):
            with self.assertRaisesRegex(dev.LaunchError, "Node.js / npm"):
                dev.require_runtimes()
        with mock.patch.object(dev.shutil, "which", return_value="node"), mock.patch.object(dev.subprocess, "run", return_value=mock.Mock(stdout="v22.12.0\n")):
            with self.assertRaisesRegex(dev.LaunchError, "22.13.0"):
                dev.require_runtimes()

    def test_check_is_read_only_offline_and_dist_is_not_required(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with mock.patch.object(dev, "ROOT", self.root), mock.patch.object(dev, "require_runtimes", return_value=self.runtime), mock.patch.object(dev, "Children") as children, mock.patch.object(dev, "require_free_port") as ports, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dev.main(["--check", "--lan"]), 0)
            children.assert_not_called()
            ports.assert_not_called()
        after = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / ".venv").exists())
        (self.root / "package-lock.json").unlink()
        with self.assertRaisesRegex(dev.LaunchError, "package-lock.json"):
            dev.check_sources(self.root)

    def test_first_install_cache_and_changed_lock_handle_unicode_paths(self):
        commands = []
        def run(command, _label):
            commands.append([str(value) for value in command])
            if "venv" in command:
                python = dev.venv_python(self.root)
                python.parent.mkdir(parents=True)
                python.touch()
            if "ci" in command:
                for name in ("node_modules/vite/bin/vite.js", "node_modules/pdfjs-dist/package.json"):
                    target = self.root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
        children = mock.Mock()
        children.run.side_effect = run
        python = dev.install_dependencies(self.root, self.runtime, children)
        self.assertEqual(python, dev.venv_python(self.root))
        self.assertEqual(sum("ci" in command for command in commands), 1)
        self.assertTrue(any(str(self.root / "requirements-optional.txt") in command for command in commands))
        self.assertTrue(any(str(self.root / "scripts/prepare-pdf-assets.mjs") in command for command in commands))
        self.assertTrue(all(isinstance(command, list) for command in commands))
        commands.clear()
        dev.install_dependencies(self.root, self.runtime, children)
        self.assertFalse(any("ci" in command or "install" in command or "venv" in command for command in commands))
        (self.root / "package-lock.json").write_text('{"changed":true}', encoding="utf-8")
        dev.install_dependencies(self.root, self.runtime, children)
        self.assertEqual(sum("ci" in command for command in commands), 1)

    def test_skip_install_checks_existing_dependencies_without_pip_or_npm(self):
        children = mock.Mock()
        with self.assertRaisesRegex(dev.LaunchError, "node_modules"):
            dev.install_dependencies(self.root, self.runtime, children, skip_install=True)
        for name in ("node_modules/vite/bin/vite.js", "node_modules/pdfjs-dist/package.json"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        self.assertEqual(dev.install_dependencies(self.root, self.runtime, children, skip_install=True), Path(sys.executable))
        for call in children.run.call_args_list:
            self.assertNotIn("install", call.args[0])
            self.assertNotIn("ci", call.args[0])
        self.assertFalse((self.root / ".venv").exists())

    def test_backend_loopback_command_registers_origins_without_shell(self):
        origins = ["http://127.0.0.1:5183", "http://localhost:5183", "http://192.168.2.12:5183"]
        command = dev.backend_command(self.root, self.root / ".venv/Scripts/python.exe", 8776, origins)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertNotIn("--lan", command)
        self.assertEqual([command[index + 1] for index, item in enumerate(command) if item == "--proxy-origin"], origins)
        self.assertEqual(command[1], str(self.root / "backend/server.py"))

    def test_occupied_port_is_rejected_without_stopping_existing_listener(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(dev.LaunchError, f"{port}.*--port / --api-port"):
                dev.require_free_port("127.0.0.1", port, "API ")
            self.assertEqual(listener.getsockname()[1], port)

    def test_startup_failure_and_ctrl_c_always_close_children(self):
        for problem, expected in [(dev.LaunchError("Vite failed"), 1), (KeyboardInterrupt(), 0)]:
            with self.subTest(error=type(problem).__name__), mock.patch.object(dev, "ROOT", self.root), mock.patch.object(dev, "require_runtimes", return_value=self.runtime), mock.patch.object(dev, "Children") as children, mock.patch.object(dev, "require_free_port"), mock.patch.object(dev, "install_dependencies", return_value=Path(sys.executable)), mock.patch.object(dev, "wait_ready", side_effect=[None, problem]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(dev.main(["--local", "--skip-install", "--no-browser", "--data-dir", str(self.root / "isolated data")]), expected)
                children.return_value.close.assert_called_once()
                self.assertEqual(children.return_value.spawn.call_count, 2)
                self.assertEqual(children.call_args.args[1]["PLANT_DATA_DIR"], str((self.root / "isolated data").resolve()))
                self.assertFalse((self.root / "isolated data").exists())

    @unittest.skipIf(os.name == "nt", "POSIX process groups run here; Windows Job Object is covered statically and needs Windows host validation")
    def test_actual_process_tree_stops_with_launcher_cleanup(self):
        pid_file = self.root / "grandchild.pid"
        code = "import pathlib,subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)"
        children = dev.Children(self.root, os.environ.copy())
        process = children.spawn([sys.executable, "-c", code, str(pid_file)])
        try:
            deadline = time.monotonic() + 5
            while not pid_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.is_file())
            grandchild = int(pid_file.read_text())
        finally:
            children.close()
        self.assertIsNotNone(process.poll())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.03)
        else:
            self.fail("Descendant survived launcher cleanup")

    def test_windows_entrypoint_is_quoted_and_does_not_install_runtimes(self):
        script = (dev.ROOT / "start-dev.cmd").read_text(encoding="utf-8")
        self.assertIn('pushd "%~dp0"', script)
        self.assertIn('"%~dp0scripts\\dev.py" --lan %*', script)
        self.assertIn("DisableDelayedExpansion", script)
        self.assertIn("sys.version_info >= (3,10)", script)
        self.assertNotIn("winget", script)
        self.assertNotIn("curl", script)


if __name__ == "__main__":
    unittest.main()

"""Explicit LAN binding, same-origin proxy checks and local-only first setup."""
import contextlib
from email.message import Message
import http.client
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.server import Handler, create_server

spec = importlib.util.spec_from_file_location("workshop_launch", ROOT / "scripts" / "launch.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class LANServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
        self.directory.cleanup()

    def start(self, **options):
        with patch("backend.server.local_ipv4_addresses", return_value=["192.168.50.21"]):
            self.server = create_server(port=0, data_dir=self.directory.name, seed=False, **options)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.local = "127.0.0.1:" + str(self.server.server_port)
        self.lan = "192.168.50.21:" + str(self.server.server_port)

    def request(self, path, body=None, host=None, origin=None, cookie=None, headers=None):
        fields = {"Host": host or self.local, "Content-Type": "application/json"}
        if origin is not None:
            fields["Origin"] = origin
        if cookie:
            fields["Cookie"] = cookie
        fields.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET" if body is None else "POST", path,
                               body=None if body is None else json.dumps(body), headers=fields)
            response = connection.getresponse()
            value = json.loads(response.read())
            return response.status, value, dict(response.getheaders())
        finally:
            connection.close()

    def setup_admin(self):
        status, value, headers = self.request("/api/platform/setup", {"username": "admin", "password": "administrator12"}, origin="http://" + self.local)
        self.assertEqual(status, 200, value)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_explicit_binding_and_exact_host_port(self):
        for options in ({"host": "0.0.0.0"}, {"host": "192.168.50.21"},
                        {"lan": True, "allowed_hosts": ["evil.example"]},
                        {"lan": True, "allowed_hosts": ["0.0.0.0"]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                create_server(port=0, data_dir=self.directory.name, seed=False, **options)
        self.start(lan=True)
        self.assertEqual(self.server.server_address[0], "0.0.0.0")
        status, health, _ = self.request("/api/health", host=self.lan)
        self.assertEqual(status, 200)
        self.assertTrue(health["lan_enabled"])
        self.assertIn("http://" + self.lan, health["access_urls"])
        for host in ("untrusted.example:" + str(self.server.server_port), "192.168.50.99:" + str(self.server.server_port),
                     "192.168.50.21:1", "192.168.50.21", "user@" + self.lan):
            with self.subTest(host=host):
                self.assertEqual(self.request("/api/health", host=host)[0], 403)

    def test_lan_first_setup_blocked_and_remote_peer_cannot_spoof_loopback(self):
        self.start(lan=True)
        status, bootstrap, _ = self.request("/api/platform/bootstrap", host=self.lan)
        self.assertEqual(status, 200)
        self.assertTrue(bootstrap["needs_setup"])
        self.assertFalse(bootstrap["local_setup_allowed"])
        self.assertEqual(bootstrap["local_setup_url"], "http://" + self.local)
        self.assertEqual(self.request("/api/platform/setup", {"username": "admin", "password": "administrator12"}, host=self.lan, origin="http://" + self.lan)[0], 403)
        self.assertTrue(self.server.platform.bootstrap()["needs_setup"])
        handler = Handler.__new__(Handler)
        handler.client_address = ("192.168.50.88", 50000)
        handler.request_authority = ("127.0.0.1", self.server.server_port)
        handler.headers = Message()
        handler.headers["X-Forwarded-For"] = "127.0.0.1"
        self.assertFalse(handler.local_setup_allowed())
        self.setup_admin()
        self.assertFalse(self.request("/api/platform/bootstrap", host=self.lan)[1]["needs_setup"])

    def test_lan_login_keeps_sessions_roles_and_same_origin(self):
        self.start(lan=True, allowed_hosts=["192.168.50.22"])
        admin_cookie = self.setup_admin()
        self.assertEqual(self.request("/api/state", host=self.lan, origin="http://" + self.lan)[0], 401)
        status, _, headers = self.request("/api/platform/login", {"username": "admin", "password": "administrator12"}, host=self.lan, origin="http://" + self.lan)
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("/api/state", host=self.lan, origin="http://" + self.lan, cookie=cookie)[0], 200)
        for origin in ("http://192.168.50.22:" + str(self.server.server_port), "http://192.168.50.21:5173", "https://" + self.lan, "null"):
            with self.subTest(origin=origin):
                self.assertEqual(self.request("/api/platform/login", {"username": "admin", "password": "administrator12"}, host=self.lan, origin=origin)[0], 403)
        status, _, _ = self.request("/api/platform/users", {"username": "outside", "password": "external-password12", "role": "external"}, cookie=admin_cookie)
        self.assertEqual(status, 200)
        status, _, headers = self.request("/api/platform/login", {"username": "outside", "password": "external-password12"}, host=self.lan, origin="http://" + self.lan)
        self.assertEqual(status, 200)
        external = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("/api/state", host=self.lan, cookie=external)[0], 403)
        self.assertEqual(self.request("/api/packages", host=self.lan, cookie=external)[0], 200)

    def test_loopback_mode_rejects_lan_host(self):
        self.start()
        self.assertEqual(self.request("/api/health", host=self.lan)[0], 403)
        self.assertFalse(self.request("/api/health")[1]["lan_enabled"])

    def test_registered_development_proxy_and_forwarded_setup_checks(self):
        self.start(proxy_origins=["http://192.168.50.21:5173", "http://127.0.0.1:5173"])
        remote = {"X-Forwarded-Host": "192.168.50.21:5173", "X-Forwarded-For": "192.168.50.88", "X-Forwarded-Proto": "http"}
        local = {"X-Forwarded-Host": "127.0.0.1:5173", "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "http"}
        status, bootstrap, _ = self.request("/api/platform/bootstrap", headers=remote)
        self.assertEqual(status, 200)
        self.assertFalse(bootstrap["local_setup_allowed"])
        self.assertEqual(bootstrap["local_setup_url"], "http://127.0.0.1:5173")
        credentials = {"username": "admin", "password": "administrator12"}
        self.assertEqual(self.request("/api/platform/setup", credentials, origin="http://192.168.50.21:5173", headers=remote)[0], 403)
        spoofed = dict(local, **{"X-Forwarded-For": "127.0.0.1, 192.168.50.88"})
        self.assertEqual(self.request("/api/platform/setup", credentials, origin="http://127.0.0.1:5173", headers=spoofed)[0], 403)
        status, _, _ = self.request("/api/platform/setup", credentials, origin="http://127.0.0.1:5173", headers=local)
        self.assertEqual(status, 200)
        status, _, headers = self.request("/api/platform/login", credentials, origin="http://192.168.50.21:5173", headers=remote)
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("/api/state", origin="http://192.168.50.21:5173", headers=remote, cookie=cookie)[0], 200)
        self.assertEqual(self.request("/api/platform/login", credentials, origin="http://192.168.50.21:5173")[0], 403)
        self.assertEqual(self.request("/api/platform/login", credentials, origin="http://192.168.50.99:5173", headers=remote)[0], 403)
        self.assertEqual(self.request("/api/health", host=self.lan)[0], 403)


class LANLaunchTests(unittest.TestCase):
    def test_existing_local_service_is_not_reported_as_lan(self):
        with patch.object(sys, "argv", ["launch.py", "--lan", "--no-browser"]), \
                patch.object(launch, "workshop_health", return_value={"ok": True, "lan_enabled": False}), \
                patch.object(launch.subprocess, "Popen") as spawn, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(launch.main(), 1)
            spawn.assert_not_called()

    def test_lan_launch_passes_explicit_switch_and_prints_actual_urls(self):
        class Child:
            def __init__(self):
                self.polls = 0
            def poll(self):
                self.polls += 1
                return None if self.polls == 1 else 0
            def wait(self):
                return 0
        health = {"ok": True, "lan_enabled": True, "access_urls": ["http://127.0.0.1:8877", "http://192.168.50.21:8877"]}
        output = io.StringIO()
        with patch.object(sys, "argv", ["launch.py", "--lan", "--port", "8877", "--allow-host", "192.168.50.21", "--no-browser"]), \
                patch.object(launch, "workshop_health", side_effect=[None, health]), \
                patch.object(launch, "port_in_use", return_value=False), \
                patch.object(launch.subprocess, "Popen", return_value=Child()) as spawn, contextlib.redirect_stdout(output):
            self.assertEqual(launch.main(), 0)
        command = spawn.call_args.args[0]
        self.assertIn("--lan", command)
        self.assertEqual(command[command.index("--port") + 1], "8877")
        self.assertIn("192.168.50.21", command)
        self.assertIn("局域网地址：http://192.168.50.21:8877", output.getvalue())


if __name__ == "__main__":
    unittest.main()

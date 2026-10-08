"""Exercise server lifecycle using real processes and a local HTTP/TCP stand-in."""
from __future__ import annotations

import http.client
import importlib.util
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cdp_server", ROOT / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)

FAKE = r'''
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import signal
import socket
import socketserver
import sys
import time

name = Path(sys.argv[0]).name
root = Path(os.environ["TEST_ROOT"])
(root / (name + ".pid")).write_text(str(os.getpid()))
(root / (name + ".args")).write_text(json.dumps(sys.argv[1:]))

def stop(signum, frame):
    (root / (name + ".stopped")).write_text(str(signum))
    sys.exit(0)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
if os.environ.get("FAIL_CHILD") == name:
    sys.exit(23)
if name == "browser":
    time.sleep(float(os.environ.get("BROWSER_DELAY", "0")))
    class HTTP(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path.startswith("/devtools/browser/"):
                key = self.headers["Sec-WebSocket-Key"]
                accept = base64.b64encode(hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()).decode()
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                self.end_headers()
                self.wfile.write(b"\x81\x09bridge-ok")
                return
            body = json.dumps({"Browser": "Chromium/154.0.8037.97",
                "webSocketDebuggerUrl": "ws://" + self.headers["Host"] + "/devtools/browser/mock"
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    ThreadingHTTPServer(("127.0.0.1", 9223), HTTP).serve_forever()
else:
    class Bridge(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with socket.create_connection(("127.0.0.1", 9223), timeout=2) as upstream:
                    streams = [self.request, upstream]
                    while True:
                        readable, _, _ = select.select(streams, [], [], 2)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            streams[1 if source is streams[0] else 0].sendall(data)
            except OSError:
                pass
    class TCP(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    TCP(("127.0.0.1", 9222), Bridge).serve_forever()
'''


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("browser", "socat"):
            path = self.root / name
            path.write_text(f"#!{sys.executable}\n" + FAKE)
            path.chmod(0o755)
        self.env = {
            **os.environ, "PATH": f"{self.root}:{os.environ['PATH']}",
            "TEST_ROOT": str(self.root), "CHROMIX_BROWSER_BINARY": str(self.root / "browser"),
            "CHROMIX_USER_DATA_DIR": str(self.root / "profile with spaces"),
            "CHROMIX_STARTUP_TIMEOUT": "3",
        }
        entry = self.root / "entrypoint.sh"
        entry.write_text((ROOT / "entrypoint.sh").read_text().replace(
            "/usr/local/lib/chromix/server.py", str(ROOT / "server.py"),
        ))
        self.entry = entry

    def start(self, mode="serve", arguments=()):
        process = subprocess.Popen(["sh", str(self.entry), mode, *arguments],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        return process

    def wait_ready(self, process):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"Server exited: {process.communicate()!r}")
            try:
                server.probe()
                return
            except (OSError, ValueError, RuntimeError, http.client.HTTPException):
                time.sleep(0.05)
        self.fail("Mock CDP did not become ready")

    def assert_children_stopped(self):
        for name in ("browser", "socat"):
            path = self.root / (name + ".pid")
            if path.exists():
                pid = int(path.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_both_aliases_health_discovery_upgrade_and_signals(self):
        for mode, sig in (("serve", signal.SIGTERM), ("cdp", signal.SIGINT)):
            with self.subTest(mode=mode):
                process = self.start(mode, ["--fingerprint=123", "data:text/html,a b;$HOME"])
                self.wait_ready(process)
                health = subprocess.run([sys.executable, str(ROOT / "server.py"), "healthcheck"],
                                        capture_output=True, timeout=5)
                self.assertEqual(health.returncode, 0, health.stderr)
                connection = http.client.HTTPConnection("127.0.0.1", 9222, timeout=2)
                connection.request("GET", "/json/version", headers={"Host": "127.0.0.1:19222"})
                version = json.loads(connection.getresponse().read())
                connection.close()
                self.assertEqual(version["webSocketDebuggerUrl"],
                                 "ws://127.0.0.1:19222/devtools/browser/mock")
                connection = http.client.HTTPConnection("127.0.0.1", 9222, timeout=2)
                connection.request("GET", "/devtools/browser/mock", headers={
                    "Upgrade": "websocket", "Connection": "Upgrade",
                    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13",
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 101)
                self.assertEqual(response.fp.read(11), b"\x81\x09bridge-ok")
                response.close()
                connection.close()
                arguments = json.loads((self.root / "browser.args").read_text())
                self.assertIn("--remote-debugging-port=9223", arguments)
                self.assertIn(f"--user-data-dir={self.env['CHROMIX_USER_DATA_DIR']}", arguments)
                self.assertEqual(arguments[-2:], ["--fingerprint=123", "data:text/html,a b;$HOME"])
                self.assertNotIn("--no-sandbox", arguments)
                self.assertFalse(any(arg.startswith("--remote-debugging-address") for arg in arguments))
                self.assertFalse(any(arg.startswith("--remote-allow-origins") for arg in arguments))
                self.assertEqual(json.loads((self.root / "socat.args").read_text()), [
                    "TCP4-LISTEN:9222,bind=0.0.0.0,reuseaddr,fork", "TCP4:127.0.0.1:9223",
                ])
                process.send_signal(sig)
                process.communicate(timeout=8)
                self.assertEqual(process.returncode, 128 + sig)
                self.assert_children_stopped()

    def test_startup_waits_for_delayed_browser(self):
        self.env["BROWSER_DELAY"] = "0.4"
        process = self.start()
        self.wait_ready(process)
        self.assertIsNone(process.poll())

    def test_startup_timeout_cleans_up(self):
        self.env.update(BROWSER_DELAY="5", CHROMIX_STARTUP_TIMEOUT="0.2")
        process = self.start()
        _, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 1)
        self.assertIn("startup timed out", stderr)
        self.assert_children_stopped()

    def test_child_startup_failure_is_not_success(self):
        for child in ("browser", "socat"):
            with self.subTest(child=child):
                self.env["FAIL_CHILD"] = child
                process = self.start()
                _, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 1)
                self.assertIn("status 23", stderr)
                self.assert_children_stopped()

    def test_child_runtime_failure_stops_peer(self):
        for child in ("browser", "socat"):
            with self.subTest(child=child):
                process = self.start()
                self.wait_ready(process)
                os.kill(int((self.root / (child + ".pid")).read_text()), signal.SIGKILL)
                _, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 1)
                self.assertIn("exited unexpectedly", stderr)
                self.assert_children_stopped()

    def test_healthcheck_fails_without_browser(self):
        result = subprocess.run([sys.executable, str(ROOT / "server.py"), "healthcheck"],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)

    def test_reserved_options_and_wildcard_origins_fail(self):
        with patch.dict(os.environ, self.env):
            for argument in ("--remote-debugging-port=9", "--remote-debugging-pipe",
                             "--remote-debugging-address=0.0.0.0", "--user-data-dir=/tmp/profile",
                             "--headless=old", "--remote-allow-origins=*", "--remote-allow-origins",
                             "--", "--version"):
                with self.subTest(argument=argument), self.assertRaises(ValueError):
                    server.browser_command([argument])
            command = server.browser_command(["--remote-allow-origins=http://127.0.0.1:3000"])
            self.assertIn("--remote-allow-origins=http://127.0.0.1:3000", command)
            self.assertIn("--no-sandbox", server.browser_command(["--no-sandbox"]))

    def test_invalid_timeout_and_profile_fail_before_launch(self):
        for timeout in ("0", "-1", "nan", "inf", "invalid"):
            self.env["CHROMIX_STARTUP_TIMEOUT"] = timeout
            process = self.start()
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 1)
            self.assertFalse((self.root / "browser.pid").exists())
        self.env["CHROMIX_STARTUP_TIMEOUT"] = "3"
        self.env["CHROMIX_USER_DATA_DIR"] = "relative"
        process = self.start()
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 1)
        self.assertFalse((self.root / "browser.pid").exists())

    def test_compose_security_defaults(self):
        compose = (ROOT / "compose.yml").read_text()
        for expected in ('127.0.0.1:${CHROMIX_HOST_PORT:-9222}:9222', 'shm_size: "2gb"',
                         'user: "10001:10001"', 'seccomp=./seccomp.json',
                         'no-new-privileges:true', 'init: true', '"healthcheck"',
                         'profile:/home/chromix/profile'):
            self.assertIn(expected, compose)
        for forbidden in ('ipc: host', 'network_mode: host', 'privileged:', '--no-sandbox'):
            self.assertNotIn(forbidden, compose)


if __name__ == "__main__":
    unittest.main()

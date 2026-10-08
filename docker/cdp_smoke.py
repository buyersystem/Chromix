#!/usr/bin/env python3
"""Exercise discovery and a real CDP WebSocket through a loopback-published port."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import struct
import subprocess
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parent


class WebSocket:
    def __init__(self, endpoint: str):
        url = urlsplit(endpoint)
        if url.scheme != "ws" or url.hostname != "127.0.0.1":
            raise RuntimeError(f"Expected a host-loopback WebSocket, got {endpoint!r}")
        self.connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.connection.request("GET", url.path, headers={
            "Connection": "Upgrade", "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": key,
        })
        response = self.connection.getresponse()
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(),
        ).digest()).decode()
        if response.status != 101 or response.getheader("Sec-WebSocket-Accept") != accept:
            response.close()
            self.connection.close()
            raise RuntimeError("CDP WebSocket upgrade failed")
        self.response = response
        self.stream = response.fp
        self.sequence = 0

    def close(self):
        self.response.close()
        self.connection.close()

    def read_exact(self, size: int) -> bytes:
        data = self.stream.read(size)
        if len(data) != size:
            raise RuntimeError("CDP WebSocket closed unexpectedly")
        return data

    def send(self, opcode: int, payload: bytes):
        length = len(payload)
        if length < 126:
            header = bytes([0x80 | opcode, 0x80 | length])
        elif length < 65536:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        self.connection.sock.sendall(header + mask + bytes(
            value ^ mask[index % 4] for index, value in enumerate(payload)
        ))

    def receive(self) -> dict:
        fragments = bytearray()
        while True:
            first, second = self.read_exact(2)
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self.read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.read_exact(8))[0]
            if second & 128 or length > 4 * 1024 * 1024:
                raise RuntimeError("Invalid or oversized CDP frame")
            payload = self.read_exact(length)
            opcode = first & 15
            if opcode == 9:
                self.send(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise RuntimeError(f"Unexpected CDP opcode {opcode}")
            fragments.extend(payload)
            if first & 128:
                return json.loads(fragments)

    def call(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self.sequence += 1
        request = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            request["sessionId"] = session
        self.send(1, json.dumps(request).encode())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = self.receive()
            if result.get("id") == self.sequence:
                if "error" in result:
                    raise RuntimeError(f"CDP {method}: {result['error']}")
                return result["result"]
        raise RuntimeError(f"CDP {method} timed out")


def discovery(port: int) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"CDP discovery returned HTTP {response.status}")
        version = json.loads(response.read())
        url = urlsplit(version["webSocketDebuggerUrl"])
        if url.netloc != f"127.0.0.1:{port}":
            raise RuntimeError(f"Discovery leaked an internal endpoint: {url.geturl()}")
        return version
    finally:
        connection.close()


def check_endpoint(port: int) -> str:
    version = discovery(port)
    websocket = WebSocket(version["webSocketDebuggerUrl"])
    try:
        product = websocket.call("Browser.getVersion")["product"]
        target = websocket.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = websocket.call("Target.attachToTarget", {
            "targetId": target, "flatten": True,
        })["sessionId"]
        result = websocket.call("Runtime.evaluate", {
            "expression": "document.body.textContent = 'chromix-cdp-' + (6 * 7)",
            "returnByValue": True,
        }, session)
        if result.get("result", {}).get("value") != "chromix-cdp-42":
            raise RuntimeError(f"CDP JavaScript evaluation failed: {result!r}")
        websocket.call("Target.closeTarget", {"targetId": target})
        return product
    finally:
        websocket.close()


def run_cdp_smoke(image: str, apparmor_profile: str | None = None) -> str:
    name = f"chromix-cdp-smoke-{uuid.uuid4().hex}"
    options = [
        "--init", "--shm-size=2g", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", f"--security-opt=seccomp={ROOT / 'seccomp.json'}",
        "--publish=127.0.0.1::9222",
        "--health-cmd=python3 /usr/local/lib/chromix/server.py healthcheck",
        "--health-interval=1s", "--health-timeout=3s", "--health-start-period=30s",
    ]
    if apparmor_profile:
        options.append(f"--security-opt=apparmor={apparmor_profile}")
    try:
        subprocess.run(["docker", "run", "--detach", "--name", name, *options, image, "serve"],
                       check=True, capture_output=True, text=True, timeout=30)
        deadline = time.monotonic() + 60
        while True:
            result = subprocess.run(["docker", "inspect", name], check=True,
                                    capture_output=True, text=True, timeout=10)
            container = json.loads(result.stdout)[0]
            state = container["State"]
            if not state["Running"]:
                raise RuntimeError(f"CDP container exited: {state!r}")
            if state.get("Health", {}).get("Status") == "healthy":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("CDP container never became healthy")
            time.sleep(0.5)
        bindings = container["NetworkSettings"]["Ports"]["9222/tcp"]
        if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            raise RuntimeError(f"Unsafe CDP host binding: {bindings!r}")
        product = check_endpoint(int(bindings[0]["HostPort"]))
        subprocess.run(["docker", "stop", "--time=10", name], check=True,
                       capture_output=True, timeout=20)
        stopped = subprocess.run(["docker", "inspect", name], check=True,
                                 capture_output=True, text=True, timeout=10)
        state = json.loads(stopped.stdout)[0]["State"]
        if state["Running"] or state["ExitCode"] != 143:
            raise RuntimeError(f"CDP did not shut down cleanly on SIGTERM: {state!r}")
        return product
    except Exception:
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True,
                              timeout=10, check=False)
        print(logs.stdout + logs.stderr, flush=True)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True,
                       timeout=20, check=False)

#!/usr/bin/env python3
"""Supervise a loopback Chromium CDP endpoint and a transparent socat bridge."""
from __future__ import annotations

import http.client
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

BROWSER_PORT = 9223
BRIDGE_PORT = 9222


def probe(port: int = BRIDGE_PORT) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"CDP returned HTTP {response.status}")
        version = json.loads(response.read())
        endpoint = urlsplit(version.get("webSocketDebuggerUrl", ""))
        if (endpoint.scheme != "ws" or endpoint.netloc != f"127.0.0.1:{port}"
                or not endpoint.path.startswith("/devtools/browser/")):
            raise RuntimeError(f"Invalid CDP browser endpoint: {version!r}")
        return version
    finally:
        connection.close()


def browser_command(arguments: list[str]) -> list[str]:
    for argument in arguments:
        flag = argument.split("=", 1)[0]
        if flag.startswith("--remote-debugging") or flag in {
            "--user-data-dir", "--headless", "--", "--help", "--version",
        }:
            raise ValueError(f"serve manages {flag}; use CLI mode for custom CDP wiring")
        if flag == "--remote-allow-origins" and ("=" not in argument or "*" in argument):
            raise ValueError("Use --remote-allow-origins=<exact-origin>, never a wildcard")
    profile = Path(os.environ.get("CHROMIX_USER_DATA_DIR", "/home/chromix/profile"))
    if not profile.is_absolute():
        raise ValueError("CHROMIX_USER_DATA_DIR must be an absolute path")
    profile.mkdir(parents=True, exist_ok=True)
    return [
        os.environ.get("CHROMIX_BROWSER_BINARY", "/opt/chromix/chromix"),
        "--headless", "--no-first-run", "--no-default-browser-check",
        f"--user-data-dir={profile}", f"--remote-debugging-port={BROWSER_PORT}",
        *arguments,
    ]


def stop_children(children: list[subprocess.Popen]) -> None:
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while any(child.poll() is None for child in children) and time.monotonic() < deadline:
        time.sleep(0.05)
    # The leader can exit before its browser workers or socat connection handlers.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def serve(arguments: list[str]) -> int:
    timeout = float(os.environ.get("CHROMIX_STARTUP_TIMEOUT", "30"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("CHROMIX_STARTUP_TIMEOUT must be a positive finite number")
    command = browser_command(arguments)
    children = []
    stopped = 0

    def on_signal(signum, _frame):
        nonlocal stopped
        stopped = signum

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        children.append(subprocess.Popen(command, start_new_session=True))
        children.append(subprocess.Popen([
            "socat", f"TCP4-LISTEN:{BRIDGE_PORT},bind=0.0.0.0,reuseaddr,fork",
            f"TCP4:127.0.0.1:{BROWSER_PORT}",
        ], start_new_session=True))
        deadline = time.monotonic() + timeout
        ready = False
        while not stopped:
            for name, child in zip(("browser", "bridge"), children):
                code = child.poll()
                if code is not None:
                    raise RuntimeError(f"{name} exited unexpectedly with status {code}")
            if not ready:
                try:
                    probe()
                except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"CDP startup timed out: {error}") from error
                else:
                    ready = True
                    print("CDP ready on container port 9222; publish only to host 127.0.0.1", flush=True)
            time.sleep(0.1)
        return 128 + stopped
    finally:
        stop_children(children)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    try:
        if sys.argv[1:] == ["healthcheck"]:
            probe()
            return 0
        if sys.argv[1:2] == ["serve"]:
            return serve(sys.argv[2:])
        raise ValueError("Expected serve [browser flags] or healthcheck")
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
        print(f"chromix: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

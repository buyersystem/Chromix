#!/usr/bin/env python3
"""Native browser RFC 1929 TCP routing against an owned, bounded SOCKS fixture."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socketserver
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk/python"))
from chromix._socks_auth import native_socks_config, SOCKS_AUTH_ENV
from fingerprint_smoke import sha256_file

HOST = "chromix-probe.invalid"
TOKEN = "chromix-owned-socks-fixture"


def exact(sock, size):
    output = b""
    while len(output) < size:
        part = sock.recv(size - len(output))
        if not part:
            raise EOFError
        output += part
    return output


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(5)
        row = {"methods": [], "authenticated": False, "requests": []}
        self.server.observations.append(row)
        try:
            version, count = exact(sock, 2)
            if version != 5 or count < 1:
                return
            row["methods"] = list(exact(sock, count))
            if self.server.mode == "downgrade":
                sock.sendall(bytes([5, 0]))
            elif 2 not in row["methods"]:
                sock.sendall(bytes([5, 255])); return
            else:
                sock.sendall(bytes([5, 2]))
                version, size = exact(sock, 2)
                username = exact(sock, size)
                password = exact(sock, exact(sock, 1)[0])
                row["auth_attempted"] = True
                row["authenticated"] = version == 1 and username == b"fixture" and password == b"correct"
                sock.sendall(bytes([1, 0 if row["authenticated"] else 1]))
                if not row["authenticated"]:
                    return
            version, command, reserved, kind = exact(sock, 4)
            if (version, command, reserved, kind) != (5, 1, 0, 3):
                row["invalid_connect"] = True; return
            host = exact(sock, exact(sock, 1)[0]).decode("ascii")
            port = int.from_bytes(exact(sock, 2), "big")
            row["destination"] = {"host": host, "port": port, "address_type": kind}
            # Never resolve or forward arbitrary requests; serve only this owned fixture.
            if (host, port) != (HOST, 80):
                sock.sendall(bytes([5, 2, 0, 1, 127, 0, 0, 1, 0, 0])); return
            sock.sendall(bytes([5, 0, 0, 1, 127, 0, 0, 1, 0, 80]))
            header = b""
            while b"\r\n\r\n" not in header and len(header) < 16384:
                header += exact(sock, 1)
            if b"\r\n\r\n" not in header:
                return
            request = header.split(b"\r\n", 1)[0].decode("ascii")
            row["requests"].append(request)
            path = request.split(" ")[1]
            if path == "/worker.js":
                body = b"onmessage=async()=>postMessage(await fetch('/probe').then(r=>r.text()));"
                mime = b"application/javascript"
            elif path == "/probe":
                body, mime = TOKEN.encode(), b"text/plain"
            else:
                body, mime = b"<!doctype html><title>owned SOCKS fixture</title><body>probe</body>", b"text/html"
            sock.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nCache-Control: no-store\r\nContent-Type: " + mime +
                         b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        except (OSError, EOFError, ValueError, UnicodeError):
            # A canceled preconnect is not a failed application exchange. The
            # assessor still requires the successful and rejection evidence.
            row["closed_early"] = True


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@contextmanager
def fixture(mode="auth"):
    server = Server(("127.0.0.1", 0), Handler)
    server.mode, server.observations = mode, []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def assess(report):
    runs = report.get("runs", [])
    if [run.get("case") for run in runs] != ["authenticated", "wrong_password", "downgrade"]:
        return ["SOCKS authentication matrix incomplete"]
    errors = []
    good, wrong, downgrade = runs
    if any(run.get("error") for run in runs):
        errors.append("SOCKS probe reported a runtime error")
    if good.get("values") != [TOKEN, TOKEN, TOKEN] or good.get("navigation_failed") is True:
        errors.append("window/iframe/worker did not use the authenticated route")
    exchanges = [row for row in good.get("wire", []) if row.get("requests")]
    if not exchanges or any(row.get("authenticated") is not True or row.get("methods") != [2] or
                            row.get("destination") != {"host": HOST, "port": 80, "address_type": 3} for row in exchanges):
        errors.append("missing exact native RFC 1929/domain CONNECT evidence")
    requests = [request for row in exchanges for request in row["requests"]]
    if (not {"GET / HTTP/1.1", "GET /frame HTTP/1.1", "GET /worker.js HTTP/1.1"} <= set(requests) or
            requests.count("GET /probe HTTP/1.1") < 3):
        errors.append("SOCKS wire evidence does not cover all three execution contexts")
    if any(row.get("methods") and row["methods"] != [2] for row in good.get("wire", [])):
        errors.append("authenticated launch offered an unexpected SOCKS method")
    if wrong.get("navigation_failed") is not True or not any(
            row.get("auth_attempted") is True and row.get("methods") == [2] for row in wrong.get("wire", [])):
        errors.append("wrong-password path was not exercised and rejected")
    if any(row.get("authenticated") is True or row.get("destination") or row.get("requests") for row in wrong.get("wire", [])):
        errors.append("wrong credentials authenticated or transmitted CONNECT/application data")
    if downgrade.get("navigation_failed") is not True or not any(row.get("methods") == [2] for row in downgrade.get("wire", [])):
        errors.append("authentication downgrade was not exercised and rejected")
    if any(row.get("auth_attempted") or row.get("authenticated") or row.get("destination") or row.get("requests")
           for row in downgrade.get("wire", [])):
        errors.append("authentication/CONNECT sent after authentication downgrade")
    return errors


def run(browser):
    report = {"schema_version": 1, "browser_sha256": sha256_file(browser), "runs": [], "errors": [],
              "udp_associate": "not_implemented", "external_routes": "not_verified",
              "qualification": "owned TCP fixture, not UDP/ICE/TURN/DNS packet-capture acceptance"}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        for case in ("authenticated", "wrong_password", "downgrade"):
            with fixture("downgrade" if case == "downgrade" else "auth") as server:
                proxy, auth = native_socks_config({"server": f"socks5://127.0.0.1:{server.server_address[1]}",
                                                   "username": "fixture", "password": "wrong" if case == "wrong_password" else "correct"})
                row = {"case": case, "values": [], "navigation_failed": False}
                instance = None
                try:
                    instance = pw.chromium.launch(executable_path=str(browser.resolve()), headless=True, proxy=proxy,
                        env={**os.environ, SOCKS_AUTH_ENV: auth}, chromium_sandbox=True,
                        args=["--disable-background-networking", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
                    report["browser_version"] = instance.version
                    page = instance.new_page()
                    try:
                        page.goto(f"http://{HOST}/", wait_until="load", timeout=10000)
                    except Exception:
                        row["navigation_failed"] = True
                    if not row["navigation_failed"] and case == "authenticated":
                        row["values"] = page.evaluate('''async () => {
                          const frame=document.createElement('iframe'),worker=new Worker('/worker.js');
                          let timer;
                          try {return await Promise.race([(async()=>{
                            const loaded=new Promise((resolve,reject)=>{frame.onload=resolve;frame.onerror=reject});
                            frame.src='/frame';document.body.appendChild(frame);await loaded;
                            return [await fetch('/probe').then(r=>r.text()),
                              await frame.contentWindow.fetch('/probe').then(r=>r.text()),
                              await new Promise((resolve,reject)=>{worker.onmessage=e=>resolve(e.data);worker.onerror=reject;worker.postMessage(1)})];
                          })(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('proxy probe timeout')),10000)})]);}
                          finally {clearTimeout(timer);worker.terminate();frame.remove();}
                        }''')
                except Exception as error:
                    row["error"] = str(error)
                    report["errors"].append(case + ": " + str(error))
                finally:
                    if instance is not None:
                        instance.close()
                row["wire"] = server.observations
                report["runs"].append(row)
    report["errors"].extend(assess(report))
    if sha256_file(browser) != report["browser_sha256"]:
        report["errors"].append("browser executable changed")
    report["status"] = "failed" if report["errors"] else "passed"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.browser.is_file() or args.output.exists():
        parser.error("use an existing browser and a new report path")
    report = run(args.browser)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({"status": report["status"], "errors": report["errors"]}))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())

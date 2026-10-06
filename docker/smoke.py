#!/usr/bin/env python3
"""Run the actual container browser against a local page and sandbox diagnostics."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

VERSION = "154.0.8037.57"
ROOT = Path(__file__).resolve().parent
MARKER = '<p id="result">chromix-docker-js-ok</p>'


def run_container(image: str, options: list[str], arguments: list[str]) -> str:
    name = f"chromix-smoke-{uuid.uuid4().hex}"
    command = ["docker", "run", "--rm", "--name", name, *options, image, *arguments]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=90)
        if result.returncode:
            raise RuntimeError(
                f"Container exited {result.returncode}: {command}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result.stdout
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name], capture_output=True, timeout=20,
            check=False,
        )


def check_version(output: str) -> None:
    if not re.search(rf"\b{re.escape(VERSION)}\b", output):
        raise RuntimeError(f"Unexpected browser version: {output!r}")


def check_dom(output: str) -> None:
    if MARKER not in output:
        raise RuntimeError(f"Local page JavaScript did not execute: {output!r}")


def check_sandbox(output: str) -> None:
    text = re.sub(r"<[^>]*>", " ", output)
    if not re.search(r"Layer 1 Sandbox\s+Namespace\b", text):
        raise RuntimeError(f"Namespace sandbox is not enabled: {output!r}")
    for feature in ("PID namespaces", "Network namespaces", "Seccomp-BPF sandbox"):
        if not re.search(rf"{re.escape(feature)}\s+Yes\b", text):
            raise RuntimeError(f"{feature} is not enabled: {output!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--apparmor-profile", help="An already loaded host AppArmor profile")
    args = parser.parse_args()
    options = [
        "--init", "--network=none", "--shm-size=1g", "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--security-opt=seccomp={ROOT / 'seccomp.json'}",
    ]
    if args.apparmor_profile:
        options.append(f"--security-opt=apparmor={args.apparmor_profile}")
    uid = run_container(args.image, [*options, "--entrypoint=/usr/bin/id"], ["-u"])
    if uid.strip() != "10001":
        raise RuntimeError(f"Expected non-root UID 10001, got {uid!r}")
    version = run_container(args.image, options, ["--version"])
    check_version(version)
    print(version.strip(), flush=True)
    with tempfile.TemporaryDirectory(prefix="chromix-docker-smoke-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        page = root / "index.html"
        page.write_text(
            '<!doctype html><html><body><p id="result">pending</p>'
            '<script>document.getElementById("result").textContent = '
            '"chromix-docker-js-ok";</script></body></html>', encoding="utf-8",
        )
        page.chmod(0o644)
        dom = run_container(
            args.image,
            [*options, "--mount", f"type=bind,src={root},dst=/smoke,readonly"],
            ["--headless", "--disable-gpu", "--dump-dom", "file:///smoke/index.html"],
        )
        check_dom(dom)
    print("Local HTML + JavaScript: passed", flush=True)
    sandbox = run_container(
        args.image, options,
        ["--headless", "--disable-gpu", "--allow-chrome-scheme-url", "--dump-dom", "chrome://sandbox"],
    )
    check_sandbox(sandbox)
    print("Non-root + namespace sandbox + seccomp-BPF sandbox: passed", flush=True)


if __name__ == "__main__":
    main()

"""Dependency-free tests; actual browser smoke runs separately on native CI runners."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("docker_smoke", ROOT / "smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)
DIGESTS = {
    "amd64": ("x64", "SHA256SUMS", "9b769a5b151b0778a42e6883dd12454817fcd0bef0268b1a93008b25052e0669"),
    "arm64": ("arm64", "SHA256SUMS-linux-arm64", "26be9806543e2957ed82469830c17d4b38bc018b5c85dcaefd434fa2e50c2a60"),
}


class EntrypointTests(unittest.TestCase):
    def run_entrypoint(self, arguments, exit_code=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = root / "browser"
            browser.write_text('#!/bin/sh\nprintf "<%s>\\n" "$@"\nexit "$BROWSER_EXIT"\n')
            browser.chmod(0o755)
            entry = root / "entrypoint.sh"
            entry.write_text((ROOT / "entrypoint.sh").read_text().replace(
                "/opt/chromix/chromix", str(browser),
            ))
            return subprocess.run(
                ["sh", str(entry), *arguments], text=True, capture_output=True,
                env={**os.environ, "BROWSER_EXIT": str(exit_code)}, check=False,
            )

    def test_default_is_version(self):
        result = self.run_entrypoint([])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "<--version>\n")

    def test_arguments_are_not_reparsed_or_augmented(self):
        arguments = ["--headless", "--dump-dom", "data:text/html,<p>a b;$HOME</p>"]
        result = self.run_entrypoint(arguments)
        self.assertEqual(result.stdout, "".join(f"<{arg}>\n" for arg in arguments))
        self.assertNotIn("--no-sandbox", result.stdout)

    def test_explicit_no_sandbox_is_forwarded(self):
        self.assertEqual(self.run_entrypoint(["--no-sandbox"]).stdout, "<--no-sandbox>\n")

    def test_browser_exit_status_is_preserved(self):
        self.assertEqual(self.run_entrypoint(["--version"], 23).returncode, 23)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "TEST_LOG": str(self.root / "log"), "TEST_MANIFEST": ""}
        self.command("curl", '''#!/bin/sh
while [ "$#" -gt 0 ]; do
    case "$1" in
        https://*) url="$1" ;;
        -o) shift; output="$1" ;;
    esac
    shift
done
printf '%s\\n' "$url" >> "$TEST_LOG"
case "$url" in
    *.zip) printf 'mock archive' > "$output" ;;
    *) printf '%s\\n' "$TEST_MANIFEST" > "$output" ;;
esac
exit "${CURL_EXIT:-0}"
''')
        self.command("sha256sum", '''#!/bin/sh
printf 'sha256sum %s\\n' "$*" >> "$TEST_LOG"
cat >> "$TEST_LOG"
exit "${HASH_EXIT:-0}"
''')
        self.command("unzip", '''#!/bin/sh
printf 'unzip\\n' >> "$TEST_LOG"
while [ "$#" -gt 0 ]; do
    if [ "$1" = -d ]; then shift; dest="$1"; fi
    shift
done
mkdir -p "$dest/chromix"
for file in chromix chrome chrome-sandbox chrome_crashpad_handler; do
    touch "$dest/chromix/$file"
done
''')

    def command(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def download(self, arch):
        return subprocess.run(
            ["sh", str(ROOT / "download.sh"), arch, str(self.root / "out")],
            env=self.env, capture_output=True, text=True, check=False,
        )

    def test_both_architectures_pin_asset_and_correct_manifest(self):
        for arch, (asset_arch, manifest, digest) in DIGESTS.items():
            with self.subTest(arch=arch):
                self.env["TEST_MANIFEST"] = f"{digest}  chromix-linux-{asset_arch}.zip"
                result = self.download(arch)
                self.assertEqual(result.returncode, 0, result.stderr)
                log = (self.root / "log").read_text()
                self.assertIn(f"/v{smoke.VERSION}/{manifest}\n", log)
                self.assertIn(f"{digest}  chromix-linux-{asset_arch}.zip\n", log)
                self.assertIn("sha256sum --check --strict -", log)
                self.assertLess(log.index("sha256sum"), log.index("unzip"))
                (self.root / "log").unlink()

    def test_unknown_architecture_fails_before_download(self):
        self.assertEqual(self.download("ppc64le").returncode, 2)
        self.assertFalse((self.root / "log").exists())

    def test_bad_missing_or_duplicate_manifest_entries_fail(self):
        digest = DIGESTS["arm64"][2]
        for manifest in ("", f"{'0' * 64}  chromix-linux-arm64.zip",
                         f"{digest}  chromix-linux-x64.zip",
                         f"{digest}  chromix-linux-arm64.zip\n" * 2):
            with self.subTest(manifest=manifest):
                self.env["TEST_MANIFEST"] = manifest
                self.assertNotEqual(self.download("arm64").returncode, 0)
                self.assertNotIn("unzip", (self.root / "log").read_text())

    def test_hash_failure_blocks_extraction(self):
        self.env["TEST_MANIFEST"] = f"{DIGESTS['amd64'][2]}  chromix-linux-x64.zip"
        self.env["HASH_EXIT"] = "1"
        self.assertNotEqual(self.download("amd64").returncode, 0)
        self.assertNotIn("unzip", (self.root / "log").read_text())

    def test_failed_download_blocks_verification(self):
        self.env["CURL_EXIT"] = "22"
        self.assertNotEqual(self.download("amd64").returncode, 0)
        self.assertNotIn("sha256sum", (self.root / "log").read_text())


class SmokeTests(unittest.TestCase):
    def test_version_requires_full_release(self):
        smoke.check_version(f"Chromium {smoke.VERSION}\n")
        with self.assertRaises(RuntimeError):
            smoke.check_version("Chromium 154.0.8037.5")

    def test_dom_requires_javascript_result(self):
        smoke.check_dom(f"<html>{smoke.MARKER}</html>")
        with self.assertRaises(RuntimeError):
            smoke.check_dom('<p id="result">pending</p>')

    def test_sandbox_must_be_active(self):
        output = (
            '<tr><td>Layer 1 Sandbox</td><td>Namespace</td></tr>'
            '<tr><td>PID namespaces</td><td>Yes</td></tr>'
            '<tr><td>Network namespaces</td><td>Yes</td></tr>'
            '<tr><td>Seccomp-BPF sandbox</td><td>Yes</td></tr>'
        )
        smoke.check_sandbox(output)
        for invalid in ("", output.replace("Namespace</td>", "None</td>"),
                        output.replace("Yes", "No"),
                        output.replace("PID namespaces", "missing"),
                        output.replace("Network namespaces", "missing"),
                        output.replace("Seccomp-BPF sandbox", "missing")):
            with self.assertRaises(RuntimeError):
                smoke.check_sandbox(invalid)

    def test_container_failure_is_reported_and_cleaned_up(self):
        with patch.object(smoke.subprocess, "run") as run:
            run.side_effect = [subprocess.CompletedProcess([], 1, "", "sandbox denied"),
                               subprocess.CompletedProcess([], 0)]
            with self.assertRaisesRegex(RuntimeError, "sandbox denied"):
                smoke.run_container("image", [], ["--version"])
            self.assertEqual(run.call_args_list[1].args[0][:3], ["docker", "rm", "--force"])

    def test_container_timeout_is_cleaned_up(self):
        with patch.object(smoke.subprocess, "run") as run:
            run.side_effect = [subprocess.TimeoutExpired("docker", 90),
                               subprocess.CompletedProcess([], 0)]
            with self.assertRaises(subprocess.TimeoutExpired):
                smoke.run_container("image", [], ["--version"])
            self.assertEqual(run.call_args_list[1].args[0][:3], ["docker", "rm", "--force"])

    def test_seccomp_allows_userns_and_clone3_fallback(self):
        profile = json.loads((ROOT / "seccomp.json").read_text())
        self.assertEqual(profile["defaultAction"], "SCMP_ACT_ERRNO")
        allowed = set()
        for rule in profile["syscalls"]:
            if rule["action"] == "SCMP_ACT_ALLOW" and not rule.get("includes") and not rule.get("args"):
                allowed.update(rule["names"])
        self.assertTrue({"clone", "unshare", "setns", "chroot"}.issubset(allowed))
        clone3 = [rule for rule in profile["syscalls"] if "clone3" in rule["names"]]
        self.assertEqual(len(clone3), 1)
        self.assertEqual(clone3[0]["errnoRet"], 38)
        self.assertEqual(clone3[0]["action"], "SCMP_ACT_ERRNO")


if __name__ == "__main__":
    unittest.main()

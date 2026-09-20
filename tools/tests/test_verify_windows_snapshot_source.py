"""Real Git and patch-stack checks for the read-only Windows153 resume gate."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_windows_snapshot_source as verify

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "third_party/blink/fixture.cc"
LITE = "v8/test/torque/test-torque.tq"
PATCH = "patches/0001-fixture.patch"


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *args],
                          env=env, check=True, capture_output=True).stdout


def commit(root):
    if not (root / ".git").exists():
        git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD").decode().strip()


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns, stat.S_IMODE(p.stat().st_mode))
            for p in root.rglob("*") if p.is_file()}


class Fixture:
    def __init__(self, root, monkeypatch):
        self.previous, self.repo, self.work = (root / name for name in ("previous", "current", "work"))
        self.src = self.work / "src"
        self.core = self.work / "tooling/ungoogled-chromium"
        self.tooling = self.work / "tooling/ungoogled-chromium-windows"
        put(self.core, "domain_regex.list", rb"example\.com#blocked.test" + b"\n")
        put(self.core, "flags.gn", "is_debug = false\n")
        put(self.core, "utils/domain_substitution.py", "raise RuntimeError('donor code executed')\n")
        core_sha = commit(self.core)
        put(self.tooling, "domain_substitution.list", SOURCE + "\n")
        put(self.tooling, "flags.windows.gn", 'target_cpu = "x64"\n')
        put(self.tooling, "download.py", "raise RuntimeError('donor code executed')\n")
        platform_sha = commit(self.tooling)
        for name in ("CHROMIUM_VERSION", "CHROMIUM_WINDOWS_VERSION", "CHROMIUM_MACOS_VERSION", "CHROMIUM_LINUX_VERSION",
                     "build/ungoogled-revisions.psd1", "build/upstream-cache.json", "build/args.windows.gn"):
            path = ROOT / name
            if path.exists():
                data = path.read_text().replace("31e6f2dd3bb2f113800d25ae359f024684addb51", core_sha)
                data = data.replace("657b9731b68aae35d4ee02428684ab8bdceb9181", platform_sha)
                put(self.previous, name, data)
        put(self.previous, "build/windows/prepare-ungoogled.ps1", "throw 'donor code executed'\n")
        put(self.previous, "tools/prepare_restored_build.py", "raise RuntimeError('donor code executed')\n")
        put(self.previous, "assets/fixture.dat", b"asset\0")
        put(self.previous, verify.arp.LITE + "/" + LITE, "lite payload\n")
        put(self.previous, "patches/series", PATCH + "\n")
        put(self.previous, PATCH, f"diff --git a/{SOURCE} b/{SOURCE}\n--- a/{SOURCE}\n+++ b/{SOURCE}\n"
            "@@ -1,3 +1,3 @@\n context\n-base example.com\n+patched example.com\n tail\n")
        self.sha = commit(self.previous)
        shutil.copytree(self.previous, self.repo)
        monkeypatch.setattr(verify, "DONOR_SHA", self.sha)
        monkeypatch.setattr(verify, "TRUSTED_REPO", self.repo)
        put(self.src, SOURCE, "context\npatched blocked.test\ntail\n")
        put(self.src, LITE, "lite payload\n")
        put(self.src, verify.MIDL_SOURCE, "# pinned wrapper, not executed by the verifier\n")
        put(self.src, "chrome/VERSION", "MAJOR=153\nMINOR=0\nBUILD=8010\nPATCH=47\n")
        put(self.src, "BUILD.gn", "# fixture\n")
        for name in ("build.ninja", ".ninja_log", ".ninja_deps", "obj/cached.obj"):
            put(self.src, "out/Default/" + name, "cached state\n")
        original = 'target_cpu = "x64"\ntarget_os = "win"\nthin_lto_enable_optimizations = true\n'
        put(self.src, "out/Default/args.gn", original)
        repo_identity, _, manifest = verify.upstream.identities(self.repo, "windows", "x64")
        self.receipt = {"schema_version": 1, "owner": verify.upstream.OWNER, "status": "restored",
                        "platform": "windows", "arch": "x64", "identity": repo_identity, "manifest": manifest,
                        "extraction_scope": verify.upstream.fetcher.SOURCE_SCOPE,
                        "external_symlink_paths": [], "original_args": verify.upstream.source_args(self.src, repo_identity)}
        put(self.src, verify.upstream.MARKER, json.dumps(self.receipt))
        args = original + (self.core / "flags.gn").read_text() + (self.repo / "build/args.windows.gn").read_text()
        values = verify.upstream.parse_gn_assignments(args)
        put(self.src, "out/Default/args.gn", "\n".join(f"{k} = {v}" for k, v in values.items()) + "\n")
        pins = verify.load_pins(self.repo, "windows")
        key = verify._ready_key(self.repo, pins)
        for name, value in {
            ".chromix-source-unpacked": verify.VERSION, ".chromix-ungoogled-core": core_sha,
            ".chromix-ungoogled-windows": platform_sha, ".chromix-binaries-pruned": core_sha,
            ".chromix-domain-substituted": core_sha, ".chromix-toolchain-ready": core_sha + "|" + platform_sha,
            ".chromix-patches": key.rsplit("|", 1)[1], ".chromix-source-ready": key,
        }.items():
            put(self.src, name, value + "\n")
        identity, _, _ = verify.arp._load(self.repo, self.core, self.tooling, "windows")
        put(self.src, verify.arp.MARKER, verify.arp._json({
            "schema_version": 1, "identity": identity, "identity_sha256": verify.arp._sha(verify.arp._json(identity)),
            "outputs": {name: hashlib.sha256((self.src / name).read_bytes()).hexdigest() for name in (SOURCE, LITE)},
        }))

    def run(self, **kwargs):
        return verify.verify(self.work, self.previous, self.repo, kwargs.pop("sha", self.sha), **kwargs)


@pytest.fixture
def fx(tmp_path, monkeypatch):
    return Fixture(tmp_path, monkeypatch)


def test_verification_is_read_only_and_repeatable_with_real_git_stack(fx):
    before = snapshot(fx.work)
    result = fx.run()
    assert result["status"] == "verified"
    assert result["changed_files"] == []
    assert result["verification"]["method"] == "reverse-forward-in-scratch"
    assert result["verification"]["patch_count"] == 1
    assert result == fx.run()
    assert snapshot(fx.work) == before


@pytest.mark.parametrize("name", sorted(verify.ALLOWED_CHANGES))
@pytest.mark.parametrize("committed", [False, True])
def test_exact_allowlisted_repair_and_orchestration_changes_are_allowed(fx, name, committed):
    put(fx.repo, name, "# changed target helper; never run by verifier\n")
    if committed:
        commit(fx.repo)
    assert fx.run()["status"] == "verified"


@pytest.mark.parametrize("name", ["build/upstream-cache.json", "build/ungoogled-revisions.psd1", "CHROMIUM_WINDOWS_VERSION",
                                   "build/args.windows.gn", "build/windows/prepare-ungoogled.ps1", "assets/fixture.dat",
                                   PATCH, "patches/series", verify.arp.LITE + "/" + LITE, "tools/unknown.py",
                                   ".github/workflows/build-win-arm64-github.yml", "README.md"])
@pytest.mark.parametrize("committed", [False, True])
def test_other_repository_changes_fail_closed(fx, name, committed):
    put(fx.repo, name, "unexpected change\n")
    if committed:
        commit(fx.repo)
    before = snapshot(fx.work)
    with pytest.raises((ValueError, verify.arp.ApplyError)):
        fx.run()
    assert snapshot(fx.work) == before


@pytest.mark.parametrize("mutation", ["sha", "fast", "release", "arm64", "previous-dirty", "previous-extra", "previous-head",
    "tooling", "tooling-head", "marker", "toolchain-marker", "progress", "transaction", "migration", "arch-marker",
    "cold", "receipt", "manifest", "upstream-run", "upstream-artifact", "missing-receipt", "patch-receipt",
    "missing-patch-receipt", "source", "lite", "args-fast", "args-extra", "args-duplicate", "args-arm64", "version", "midl-missing"])
def test_invalid_snapshot_or_identity_is_rejected_without_mutation(fx, mutation):
    kwargs = {}
    if mutation == "sha":
        kwargs["sha"] = "f" * 40
    elif mutation in ("fast", "release"):
        kwargs["build_profile"] = mutation
    elif mutation == "arm64":
        kwargs["arch"] = "arm64"
    elif mutation.startswith("previous-"):
        put(fx.previous, "CHROMIUM_WINDOWS_VERSION" if mutation == "previous-dirty" else "extra.py", "changed\n")
        if mutation == "previous-head":
            commit(fx.previous)
    elif mutation.startswith("tooling"):
        put(fx.core, "flags.gn", "is_debug = true\n")
        if mutation == "tooling-head":
            commit(fx.core)
    elif mutation in ("marker", "toolchain-marker", "progress", "migration"):
        name = {"marker": ".chromix-source-ready", "toolchain-marker": ".chromix-toolchain-ready",
                "progress": ".chromix-layer-in-progress", "migration": verify.identity.RECEIPT}[mutation]
        put(fx.src, name, "invalid\n")
    elif mutation == "transaction":
        (fx.work / ".chromix-upstream-restore-interrupted").mkdir()
    elif mutation == "arch-marker":
        put(fx.work, ".chromix-target-arch", "arm64\n")
    elif mutation == "cold":
        (fx.src / "out/Chromix").mkdir()
    elif mutation in ("receipt", "manifest", "upstream-run", "upstream-artifact"):
        if mutation == "receipt":
            fx.receipt["arch"] = "arm64"
        else:
            key = {"manifest": "sha256", "upstream-run": "run_id", "upstream-artifact": "artifact_id"}[mutation]
            fx.receipt["manifest"][key] = "incorrect"
        put(fx.src, verify.upstream.MARKER, json.dumps(fx.receipt))
    elif mutation in ("missing-receipt", "missing-patch-receipt", "midl-missing"):
        name = {"missing-receipt": verify.upstream.MARKER, "missing-patch-receipt": verify.arp.MARKER,
                "midl-missing": verify.MIDL_SOURCE}[mutation]
        (fx.src / name).unlink()
    elif mutation == "patch-receipt":
        put(fx.src, verify.arp.MARKER, "{}")
    elif mutation in ("source", "lite", "version"):
        put(fx.src, {"source": SOURCE, "lite": LITE, "version": "chrome/VERSION"}[mutation], "changed\n")
    elif mutation.startswith("args-"):
        path = fx.src / "out/Default/args.gn"
        text = path.read_text()
        text = {"args-fast": text.replace("thin_lto_enable_optimizations = true", "thin_lto_enable_optimizations = false"),
                "args-extra": text + "unexpected = true\n", "args-duplicate": text + 'target_cpu = "x64"\n',
                "args-arm64": text.replace('"x64"', '"arm64"')}[mutation]
        path.write_text(text)
    before = snapshot(fx.work)
    with pytest.raises((ValueError, verify.arp.ApplyError, OSError)):
        fx.run(**kwargs)
    assert snapshot(fx.work) == before


def test_matching_output_receipt_cannot_replace_structural_stack_check(fx):
    put(fx.src, SOURCE, "context\nstale blocked.test\ntail\n")
    path = fx.src / verify.arp.MARKER
    receipt = json.loads(path.read_bytes())
    receipt["outputs"][SOURCE] = hashlib.sha256((fx.src / SOURCE).read_bytes()).hexdigest()
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="stale/incompatible source"):
        fx.run()


@pytest.mark.parametrize("target", ["repo", "previous", "source"])
def test_linked_inputs_are_rejected(fx, target):
    root, name = (fx.src, SOURCE) if target == "source" else (getattr(fx, target), PATCH)
    path = root / name
    destination = root / "linked-content"
    path.rename(destination)
    path.symlink_to(destination)
    with pytest.raises((ValueError, verify.arp.ApplyError)):
        fx.run()


def test_source_or_repository_race_is_not_certified(fx, monkeypatch):
    real_verify = verify.stack.verify

    def mutate_after_stack(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        put(fx.src, verify.MIDL_SOURCE, "changed concurrently\n")
        return result

    monkeypatch.setattr(verify.stack, "verify", mutate_after_stack)
    with pytest.raises(verify.arp.ApplyError, match="changed concurrently"):
        fx.run()


def test_cli_writes_read_only_success_and_failure_reports(fx):
    report = fx.work / "diagnostics/verified.json"
    args = ["--workdir", str(fx.work), "--previous-repo", str(fx.previous), "--repo", str(fx.repo),
            "--expected-previous-sha", fx.sha, "--report", str(report)]
    before = snapshot(fx.src)
    assert verify.main(args) == 0
    assert json.loads(report.read_bytes())["status"] == "verified"
    assert snapshot(fx.src) == before
    with pytest.raises(SystemExit):
        verify.main(args)
    args[-1] = str(fx.work / "diagnostics/failed.json")
    put(fx.src, ".chromix-source-ready", "wrong\n")
    assert verify.main(args) == 1
    assert json.loads(Path(args[-1]).read_bytes())["status"] == "failed"


def test_windows_crlf_checkout_representation_does_not_change_identity(fx, monkeypatch):
    monkeypatch.setattr(verify.identity, "WINDOWS", True)
    for root in (fx.previous, fx.repo):
        path = root / "build/windows/prepare-ungoogled.ps1"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    for path in fx.src.glob(".chromix-*"):
        if path.name.endswith(".json"):
            continue
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    assert fx.run()["status"] == "verified"


def test_current_base_has_216_patches_and_no_midl_overlap():
    assert len(verify.identity._series(ROOT)) == 216
    for name in verify.identity._series(ROOT):
        _, entries = verify.arp.transform_patch((ROOT / name).read_bytes(), set(), [])
        assert verify.MIDL_SOURCE not in {entry[0] for entry in entries}
    assert verify.load_pins(ROOT, "windows")["ChromiumVersion"] == verify.VERSION

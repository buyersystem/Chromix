#!/usr/bin/env python3
"""Read-only, unchanged-source verification of the exact Windows 153 x64 donor.

Run only from the target checkout after authenticated extraction, before prepare.
The previous checkout is data, never a source of executable Python or scripts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import apply_restored_patches as arp
import merge_gn_args
import migrate_windows_snapshot as identity
from platform_pins import load_pins
import restore_upstream_cache as upstream
import verify_patch_stack as stack

DONOR_SHA = "e5b29c58b44e381924a2dd4bd60f54d01abb9d9c"
VERSION = "153.0.8010.47"
UPSTREAM_RUN_ID = "35059013905"
UPSTREAM_ARTIFACT_ID = 10523508661
TRUSTED_REPO = Path(__file__).resolve().parents[1]
MIDL_SOURCE = "build/toolchain/win/midl.py"
# Preparation fixes may vary, but no patch, lite, pin or other repository input may.
ALLOWED_CHANGES = frozenset({
    ".github/actions/restore-windows-snapshot/action.yml",
    ".github/workflows/build-win-x64-github.yml",
    "build/windows/ci-stage.ps1",
    "tools/verify_windows_snapshot_source.py",
    "tools/repair_windows_midl.py",
    "tools/prepare_restored_build.py",
    "tools/tests/test_verify_windows_snapshot_source.py",
    "tools/tests/test_windows_snapshot_workflow.py",
    "tools/tests/test_windows_upstream_cache.py",
    "tools/tests/test_repair_windows_midl.py",
    "tools/tests/fixtures/windows153_midl.py",
    "tools/tests/test_prepare_restored_build.py",
})


def _inventory(root: Path) -> set[str]:
    result = set()
    caches = {".pytest_cache", "tools/__pycache__", "tools/tests/__pycache__"}
    for directory, dirs, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        for name in list(dirs):
            path = Path(directory) / name
            key = path.relative_to(root).as_posix()
            if key == ".git" or key in caches:
                dirs.remove(name)
            else:
                identity._safe(path, directory=True)
        for name in files:
            if relative == Path(".") and name == ".git":
                continue
            path = identity._safe(Path(directory) / name)
            result.add(path.relative_to(root).as_posix())
    return result


def _inputs(previous: Path, repo: Path, old_tree: dict, new_tree: dict) -> tuple[dict, dict]:
    if _inventory(previous) != set(old_tree):
        raise ValueError("previous checkout has extra or missing files")
    actual = _inventory(repo)
    if set(new_tree) - actual or actual - set(new_tree) - ALLOWED_CHANGES:
        raise ValueError("target checkout has extra or missing files outside the allowlist")
    if (set(old_tree) ^ set(new_tree)) - ALLOWED_CHANGES:
        raise ValueError("repository inventory differs outside the allowlist")
    for name in set(old_tree) | set(new_tree):
        if name not in ALLOWED_CHANGES and old_tree.get(name) != new_tree.get(name):
            raise ValueError("repository input differs outside the allowlist: " + name)
    old = identity._tracked(previous, old_tree)
    new = identity._tracked(repo, {name: entry for name, entry in new_tree.items()
                                   if name not in ALLOWED_CHANGES})
    new.update(identity._capture(repo, actual & ALLOWED_CHANGES))
    for name in actual & ALLOWED_CHANGES:
        if name in new_tree and new_tree[name][:2] not in (("100644", "blob"), ("100755", "blob")):
            raise ValueError("unsupported allowlisted Git entry: " + name)
    return old, new


def _ready_key(repo: Path, pins: dict) -> str:
    digest = hashlib.sha256()
    for name in identity._series(repo):
        digest.update(identity._file(repo, name).read_bytes())
    lite = identity._walk_files(repo, arp.LITE)
    if lite != {arp.LITE + "/v8/test/torque/test-torque.tq"}:
        raise ValueError("unsupported pinned Windows lite inventory")
    for name in sorted(lite):
        digest.update(name[len(arp.LITE) + 1:].encode("utf-8"))
        digest.update(identity._file(repo, name).read_bytes())
    return "|".join((VERSION, pins["UngoogledCommit"], pins["UngoogledWindowsCommit"], digest.hexdigest()))


def _markers(work: Path, src: Path, pins: dict, key: str) -> dict:
    for root in (work, src):
        for path in root.iterdir():
            name = path.name.casefold()
            if (name.startswith(".chromix") and re.search(r"in[-_]?progress", name)
                    or name.startswith(".chromix-upstream-restore-")
                    or name == identity.RECEIPT):
                raise ValueError("interrupted or migrated snapshot: " + str(path))
    arch = identity._file(work, ".chromix-target-arch", missing=True)
    if arch.exists() and identity._text(arch) != "x64":
        raise ValueError("snapshot target architecture is not x64")
    if (src / "out/Chromix").exists():
        raise ValueError("verify-source requires upstream out/Default, not cold out/Chromix")
    values = {
        ".chromix-source-unpacked": VERSION,
        ".chromix-ungoogled-core": pins["UngoogledCommit"],
        ".chromix-ungoogled-windows": pins["UngoogledWindowsCommit"],
        ".chromix-binaries-pruned": pins["UngoogledCommit"],
        ".chromix-domain-substituted": pins["UngoogledCommit"],
        ".chromix-toolchain-ready": pins["UngoogledCommit"] + "|" + pins["UngoogledWindowsCommit"],
        ".chromix-patches": key.rsplit("|", 1)[1],
        ".chromix-source-ready": key,
    }
    for name, value in values.items():
        if identity._text(identity._file(src, name)) != value:
            raise ValueError("missing/mismatched prepared layer marker: " + name)
    return identity._capture(src, values)


def _native_args(src: Path, repo: Path, core: Path, tooling: Path, receipt: dict) -> None:
    expected = dict(receipt["original_args"]["assignments"])
    for root, name in ((core, "flags.gn"), (tooling, "flags.windows.gn"), (repo, "build/args.windows.gn")):
        _, values = merge_gn_args.parse(identity._file(root, name))
        expected.update(upstream.parse_gn_assignments("\n".join(values.values())))
    path = identity._file(src, "out/Default/args.gn")
    # The checkpoint is generated by merge_gn_args: duplicate assignments are not valid here.
    text = path.read_text(encoding="utf-8")
    keys = re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", text)
    actual = upstream.parse_gn_assignments(text)
    if (len(keys) != len(set(keys)) or actual != expected
            or actual.get("target_cpu") != '"x64"' or actual.get("target_os") != '"win"'
            or actual.get("v8_target_cpu", '"x64"') != '"x64"'):
        raise ValueError("snapshot GN args do not match the pinned native Windows x64 configuration")


def verify(workdir: Path, previous_repo: Path, repo: Path, expected_previous_sha: str,
           *, build_profile: str = "native", arch: str = "x64") -> dict:
    if expected_previous_sha != DONOR_SHA or build_profile != "native" or arch != "x64":
        raise ValueError("verify-source requires the fixed donor and native Windows x64 profile")
    work, previous, repo = (identity._safe(Path(p), directory=True) for p in (workdir, previous_repo, repo))
    if repo != TRUSTED_REPO:
        raise ValueError("--repo must be the running trusted helper checkout")
    roots = (work, previous, repo)
    if len(set(roots)) != 3 or any(a.is_relative_to(b) for a in roots for b in roots if a != b):
        raise ValueError("workdir and repositories must be separate, non-nested directories")
    if any(Path(tempfile.gettempdir()).resolve().is_relative_to(root) for root in roots):
        raise ValueError("temporary directory must be outside workdir/repositories")
    git = identity._host_git(roots)
    old_head, old_tree = identity._tree(git, previous, DONOR_SHA)
    new_head, new_tree = identity._tree(git, repo)
    old_inputs, new_inputs = _inputs(previous, repo, old_tree, new_tree)
    pins = load_pins(repo, "windows")
    if pins != load_pins(previous, "windows") or pins["ChromiumVersion"] != VERSION:
        raise ValueError("Windows153 preparation pins differ")
    src = identity._safe(work / "src", directory=True)
    core = identity._safe(work / "tooling/ungoogled-chromium", directory=True)
    tooling = identity._safe(work / "tooling/ungoogled-chromium-windows", directory=True)
    tool_state = []
    for root, pin, submodule in ((core, pins["UngoogledCommit"], None),
                                 (tooling, pins["UngoogledWindowsCommit"], pins["UngoogledCommit"])):
        head, tree = identity._tree(git, root, pin)
        tool_state.append((root, head, tree, identity._tracked(root, tree, core_pin=submodule)))
    key = _ready_key(repo, pins)
    if key != _ready_key(previous, pins):
        raise ValueError("source-ready inputs differ")
    markers = _markers(work, src, pins, key)
    arch_state = identity._capture(work, {".chromix-target-arch"})
    receipt = upstream.verify_restored(work, "windows", "x64", repo=repo)
    if (receipt["manifest"].get("run_id") != int(UPSTREAM_RUN_ID)
            or receipt["manifest"].get("artifact_id") != UPSTREAM_ARTIFACT_ID):
        raise ValueError("upstream receipt is not the pinned run/artifact")
    _native_args(src, repo, core, tooling, receipt)
    old_identity, _, old_lite = arp._load(previous, core, tooling, "windows")
    current_identity, patches, lite = arp._load(repo, core, tooling, "windows")
    if old_identity != current_identity or old_lite != lite:
        raise ValueError("patch/lite/domain inputs differ")
    names = set(lite) | {entry[0] for _, _, entries in patches for entry in entries}
    if MIDL_SOURCE in names:
        raise ValueError("MIDL repair must not overlap the patch/lite source stack")
    identity._file(src, MIDL_SOURCE)
    before = identity._capture(src, names | set(markers) | {
        upstream.MARKER, arp.MARKER, "out/Default/args.gn", "chrome/VERSION", MIDL_SOURCE})
    if not arp._completed(src, current_identity, names):
        raise ValueError("restored patch completion receipt is missing")
    proof = stack.verify(src, repo, core=core, tooling=tooling, platform="windows")
    identity._unchanged(src, before)
    if identity._tree(git, previous, DONOR_SHA) != (old_head, old_tree) or identity._tree(git, repo) != (new_head, new_tree):
        raise ValueError("repository Git identity changed concurrently")
    if _inputs(previous, repo, old_tree, new_tree) != (old_inputs, new_inputs):
        raise ValueError("preparation inputs changed concurrently")
    for root, head, tree, captured in tool_state:
        if identity._tree(git, root, head) != (head, tree):
            raise ValueError("tooling Git identity changed concurrently")
        identity._unchanged(root, captured)
    identity._unchanged(work, arch_state)
    _markers(work, src, pins, key)
    return {"schema_version": 1, "status": "verified", "operation": "windows-unchanged-source",
            "previous_sha": old_head, "target_sha": new_head, "platform": "windows", "arch": arch,
            "version": VERSION, "build_profile": build_profile, "key": key, "changed_files": [],
            "verification": proof}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=TRUSTED_REPO)
    parser.add_argument("--previous-repo", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--expected-previous-sha", required=True)
    parser.add_argument("--arch", default="x64")
    parser.add_argument("--build-profile", default="native")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists() or any(args.report.resolve().is_relative_to(root.resolve())
                                  for root in (args.workdir / "src", args.repo, args.previous_repo)):
        parser.error("report must be a new file outside source/repositories")
    try:
        report = verify(args.workdir, args.previous_repo, args.repo, args.expected_previous_sha,
                        build_profile=args.build_profile, arch=args.arch)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report = {"status": "failed", "error": str(exc)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({"status": report["status"], "error": report.get("error"), "report": str(args.report)}))
    return int(report["status"] != "verified")


if __name__ == "__main__":
    raise SystemExit(main())

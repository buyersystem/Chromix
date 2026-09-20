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


STAGE8_SHA = "2a55082adb89cb8bac7aa7ab8bb61162b53f4c35"
STAGE8_PROFILE = "windows-153-x64-stage8"
STAGE8_DONOR = {
    "repository": "xiaozhou26/Chromix", "run_id": 35485726877, "attempt": 1,
    "stage": 8, "job_id": 106081168976, "head_sha": STAGE8_SHA,
    "branch": "fix/win153-midl-stat-20260920",
}
STAGE8_ARTIFACTS = [
    {"id": 10608631230, "name": "tree-s8-attempt-1-part1", "size_in_bytes": 9663676664,
     "expired": False, "digest": "sha256:6375422031b0970a87ad6c787226a9cae4ff1af15d4785ef82f7810ec8208cf6"},
    {"id": 10608606403, "name": "tree-s8-attempt-1-part2", "size_in_bytes": 3270419872,
     "expired": False, "digest": "sha256:ba2b2d3d4ec07c379df43522eb0b57ae24193600c6e4e96a8b9d45c1436b4eeb"},
]
STAGE8_ALLOWED_CHANGES = frozenset({
    ".github/actions/restore-windows-snapshot/action.yml",
    ".github/workflows/build-win-x64-github.yml",
    "build/windows/ci-stage.ps1",
    "tools/verify_windows_snapshot_source.py",
    "tools/tests/test_verify_windows_snapshot_source.py",
    "tools/tests/test_windows_snapshot_workflow.py",
    "tools/tests/test_windows_upstream_cache.py",
    "tools/fingerprint_transport_audit.py",
    "tools/fingerprint_transport_lifecycle_audit.py",
    "tools/tests/test_transport_lifecycle.py",
    "tools/tests/test_transport_lifecycle_diagnostics.py",
})


def verify_stage8_origin(manifest: dict) -> None:
    expected = {key: value for key, value in STAGE8_DONOR.items() if key != "branch"}
    expected.update(platform="windows", arch="x64", workflow="build-win-x64-github",
                    pattern="tree-s8-attempt-1-part*", artifacts=STAGE8_ARTIFACTS)
    if manifest != expected:
        raise ValueError("stage8 origin differs from the exact checkpoint profile")


def stage8_consumer() -> dict:
    run = {key: os.environ.get(key, "") for key in
           ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_SHA")}
    if (any(not re.fullmatch(r"[1-9][0-9]*", run[key]) for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
            or run["GITHUB_JOB"] != "build-8" or not re.fullmatch(r"[0-9a-f]{40}", run["GITHUB_SHA"])):
        raise ValueError("stage8 recovery requires an exact consumer run/attempt/job/SHA")
    return run


def publish_stage8_hop(directory: Path) -> None:
    directory = identity._safe(directory, directory=True)
    consumer = stage8_consumer()
    expected_suffix = ("fingerprint-diagnostics", "recovery-hops", "d35485726877-a1-s8-j106081168976",
                       f'c{consumer["GITHUB_RUN_ID"]}-a{consumer["GITHUB_RUN_ATTEMPT"]}-{consumer["GITHUB_JOB"]}-{consumer["GITHUB_SHA"]}')
    if directory.parts[-4:] != expected_suffix:
        raise ValueError("stage8 recovery report directory is not bound to the current donor/consumer")
    names = ("windows-snapshot.json", "windows-snapshot-download.json", "windows-unchanged-source.json")
    before = identity._capture(directory, names)
    reports = {}
    data = {}
    for key, name in zip(("origin", "download", "unchanged_source"), names):
        payload = identity._file(directory, name).read_bytes()
        data[key] = json.loads(payload)
        reports[key] = {"path": name, "sha256": hashlib.sha256(payload).hexdigest()}
    verify_stage8_origin(data["origin"])
    download = data["download"]
    if (any(download.get(key) != value for key, value in
            {"status": "success", "phase": "complete", "publication": "published",
             "repository": STAGE8_DONOR["repository"], "run_id": STAGE8_DONOR["run_id"],
             "head_sha": STAGE8_SHA}.items())
            or [{key: item.get(key) for key in STAGE8_ARTIFACTS[0]} for item in download.get("artifacts", [])]
            != STAGE8_ARTIFACTS):
        raise ValueError("stage8 download is not the current successful published checkpoint")
    source = data["unchanged_source"]
    if any(source.get(key) != value for key, value in
           {"status": "verified", "operation": "windows-unchanged-source", "profile": STAGE8_PROFILE,
            "previous_sha": STAGE8_SHA, "target_sha": consumer["GITHUB_SHA"], "run": consumer,
            "changed_files": []}.items()):
        raise ValueError("stage8 source report is not bound to the current recovery hop")
    report = {"schema_version": 1, "status": "verified", "profile": STAGE8_PROFILE,
              "donor": STAGE8_DONOR, "consumer": consumer, "reports": reports,
              "source_verification": "source-verification.json", "runtime_acceptance": "runtime/acceptance.json"}
    identity._unchanged(directory, before)
    with identity._file(directory, "recovery-hop.json", missing=True).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)


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


def _inputs(previous: Path, repo: Path, old_tree: dict, new_tree: dict,
            allowed: frozenset[str]) -> tuple[dict, dict]:
    if _inventory(previous) != set(old_tree):
        raise ValueError("previous checkout has extra or missing files")
    actual = _inventory(repo)
    if set(new_tree) - actual or actual - set(new_tree) - allowed:
        raise ValueError("target checkout has extra or missing files outside the allowlist")
    if (set(old_tree) ^ set(new_tree)) - allowed:
        raise ValueError("repository inventory differs outside the allowlist")
    for name in set(old_tree) | set(new_tree):
        if name not in allowed and old_tree.get(name) != new_tree.get(name):
            raise ValueError("repository input differs outside the allowlist: " + name)
    old = identity._tracked(previous, old_tree)
    new = identity._tracked(repo, {name: entry for name, entry in new_tree.items()
                                   if name not in allowed})
    new.update(identity._capture(repo, actual & allowed))
    for name in actual & allowed:
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
    if expected_previous_sha not in (DONOR_SHA, STAGE8_SHA) or build_profile != "native" or arch != "x64":
        raise ValueError("verify-source requires an exact donor and native Windows x64 profile")
    stage8 = expected_previous_sha == STAGE8_SHA
    allowed = STAGE8_ALLOWED_CHANGES if stage8 else ALLOWED_CHANGES
    consumer = stage8_consumer() if stage8 else None
    work, previous, repo = (identity._safe(Path(p), directory=True) for p in (workdir, previous_repo, repo))
    if repo != TRUSTED_REPO:
        raise ValueError("--repo must be the running trusted helper checkout")
    roots = (work, previous, repo)
    if len(set(roots)) != 3 or any(a.is_relative_to(b) for a in roots for b in roots if a != b):
        raise ValueError("workdir and repositories must be separate, non-nested directories")
    if any(Path(tempfile.gettempdir()).resolve().is_relative_to(root) for root in roots):
        raise ValueError("temporary directory must be outside workdir/repositories")
    git = identity._host_git(roots)
    old_head, old_tree = identity._tree(git, previous, expected_previous_sha)
    new_head, new_tree = identity._tree(git, repo)
    if stage8:
        commit_headers = identity._git(git, repo, "cat-file", "commit", new_head).split(b"\n\n", 1)[0]
        parents = [line[7:].decode("ascii") for line in commit_headers.splitlines() if line.startswith(b"parent ")]
        if parents != [expected_previous_sha] or consumer["GITHUB_SHA"] != new_head:
            raise ValueError("stage8 target must be the consumer SHA and a direct single-parent child of the donor")
    old_inputs, new_inputs = _inputs(previous, repo, old_tree, new_tree, allowed)
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
    if identity._tree(git, previous, expected_previous_sha) != (old_head, old_tree) or identity._tree(git, repo) != (new_head, new_tree):
        raise ValueError("repository Git identity changed concurrently")
    if _inputs(previous, repo, old_tree, new_tree, allowed) != (old_inputs, new_inputs):
        raise ValueError("preparation inputs changed concurrently")
    for root, head, tree, captured in tool_state:
        if identity._tree(git, root, head) != (head, tree):
            raise ValueError("tooling Git identity changed concurrently")
        identity._unchanged(root, captured)
    identity._unchanged(work, arch_state)
    _markers(work, src, pins, key)
    result = {"schema_version": 1, "status": "verified", "operation": "windows-unchanged-source",
              "previous_sha": old_head, "target_sha": new_head, "platform": "windows", "arch": arch,
              "version": VERSION, "build_profile": build_profile, "key": key, "changed_files": [],
              "verification": proof}
    if stage8:
        result.update(profile=STAGE8_PROFILE, run=consumer,
                      repository_changed_files=sorted(name for name in set(old_inputs) | set(new_inputs)
                                                      if old_tree.get(name) != new_tree.get(name)
                                                      or (old_inputs.get(name) or (None,))[-2:]
                                                      != (new_inputs.get(name) or (None,))[-2:]))
    return result


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

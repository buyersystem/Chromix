from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools import patch_selection as selection

ROOT = Path(__file__).resolve().parents[2]


def clone_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "patches", repo / "patches")
    (repo / "build").mkdir()
    for name in ("build/ungoogled-revisions.psd1", "CHROMIUM_VERSION",
                 "CHROMIUM_LINUX_VERSION", "CHROMIUM_WINDOWS_VERSION",
                 "CHROMIUM_MACOS_VERSION"):
        source = ROOT / name
        if source.is_file():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return repo


def set_linux_version(repo: Path, version: str) -> None:
    text = (repo / "build/ungoogled-revisions.psd1").read_text()
    replacements = {
        'LinuxChromiumVersion = "154.0.8037.57"': f'LinuxChromiumVersion = "{version}"',
        'LinuxUngoogledVersion = "154.0.8037.57-1"': f'LinuxUngoogledVersion = "{version}-1"',
        'UngoogledLinuxVersion = "154.0.8037.57-1"': f'UngoogledLinuxVersion = "{version}-1"',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    (repo / "build/ungoogled-revisions.psd1").write_text(text)
    (repo / "CHROMIUM_LINUX_VERSION").write_text(version + "\n")


def test_exact_154_selects_reviewed_override_bytes_for_linux_and_windows():
    linux_identity, linux_patches = selection.select(ROOT, "linux")
    windows_identity, windows_patches = selection.select(ROOT, "windows")
    assert len(linux_patches) == len(windows_patches) == 216
    assert linux_identity["selection"] == {
        "schema_version": 1,
        "version": selection.VERSION,
        "platform": "linux",
        "core_commit": selection.CORE,
        "platform_commit": selection.PLATFORM_COMMITS["linux"],
        "base_series_sha256": linux_identity["selection"]["base_series_sha256"],
    }
    assert windows_identity["selection"]["platform"] == "windows"
    for identity, patches in ((linux_identity, linux_patches), (windows_identity, windows_patches)):
        selected = {entry["path"] for entry in identity["patches"]}
        assert all(path.startswith("patches/chromium154/") or path.startswith("patches/")
                   for path in selected)
        assert len(selected & {"patches/chromium154/" + name for name in selection.OVERRIDES}) == 6
        assert [entry["sha256"] for entry in identity["patches"]] == [
            selection.digest(data) for _, data in patches]


def test_mac152_and_historical153_keep_root_patch_identity(tmp_path):
    mac_identity, mac_patches = selection.select(ROOT, "macos")
    assert "selection" not in mac_identity
    assert all(path.startswith("patches/") and not path.startswith("patches/chromium154/")
               for path, _ in mac_patches)

    repo = clone_repo(tmp_path)
    set_linux_version(repo, "153.0.8010.36")
    identity, patches = selection.select(repo, "linux")
    assert "selection" not in identity
    assert all(path.startswith("patches/") for path, _ in patches)


def test_platform_swap_changes_selection_identity_and_preparation_key():
    linux_identity, _ = selection.select(ROOT, "linux")
    windows_identity, _ = selection.select(ROOT, "windows")
    assert linux_identity != windows_identity
    assert linux_identity["selection"]["platform"] != windows_identity["selection"]["platform"]
    assert selection.preparation_key(ROOT, "linux", "posix") != selection.preparation_key(ROOT, "windows", "posix")
    assert selection.preparation_key(ROOT, "linux", "windows") != selection.preparation_key(ROOT, "windows", "windows")


@pytest.mark.parametrize("mutation", ["missing", "tampered", "extra"])
def test_override_inventory_and_bytes_fail_closed(tmp_path, mutation):
    repo = clone_repo(tmp_path)
    directory = repo / selection.OVERRIDE_ROOT
    if mutation == "missing":
        (directory / next(iter(selection.OVERRIDES))).unlink()
    elif mutation == "tampered":
        path = directory / next(iter(selection.OVERRIDES))
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        (directory / "9999-extra.patch").write_bytes(b"unexpected")
    with pytest.raises(selection.SelectionError, match="partial|changed|override"):
        selection.select(repo, "linux")


def test_wrong_154_platform_and_core_pin_fail_closed(tmp_path):
    repo = clone_repo(tmp_path)
    text = (repo / "build/ungoogled-revisions.psd1").read_text()
    text = text.replace('MacOSChromiumVersion = "152.0.7977.82"',
                        'MacOSChromiumVersion = "154.0.8037.57"')
    text = text.replace('MacOSUngoogledVersion = "152.0.7977.82-1"',
                        'MacOSUngoogledVersion = "154.0.8037.57-1"')
    text = text.replace('UngoogledMacOSVersion = "152.0.7977.82-1.1"',
                        'UngoogledMacOSVersion = "154.0.8037.57-1.1"')
    (repo / "build/ungoogled-revisions.psd1").write_text(text)
    (repo / "CHROMIUM_MACOS_VERSION").write_text("154.0.8037.57\n")
    with pytest.raises(selection.SelectionError, match="Windows or Linux"):
        selection.select(repo, "macos")

    repo = clone_repo(tmp_path / "core")
    text = (repo / "build/ungoogled-revisions.psd1").read_text()
    text = text.replace('LinuxUngoogledCommit = "800d0bb5078472e4442c1fd73373172754a60939"',
                        'LinuxUngoogledCommit = "' + "0" * 40 + '"')
    (repo / "build/ungoogled-revisions.psd1").write_text(text)
    with pytest.raises(selection.SelectionError, match="core/platform pins"):
        selection.select(repo, "linux")


def test_unknown_version_partial_pin_and_source_mismatch_fail_closed(tmp_path):
    repo = clone_repo(tmp_path)
    set_linux_version(repo, "155.0.1.2")
    with pytest.raises(selection.SelectionError, match="unsupported Chromium patch version"):
        selection.select(repo, "linux")

    repo = clone_repo(tmp_path / "partial")
    text = (repo / "build/ungoogled-revisions.psd1").read_text()
    text = text.replace('  LinuxChromiumVersion = "154.0.8037.57"\n', "")
    (repo / "build/ungoogled-revisions.psd1").write_text(text)
    with pytest.raises(selection.SelectionError, match="overrides"):
        selection.select(repo, "linux")

    repo = clone_repo(tmp_path / "source")
    src = tmp_path / "src/chrome"
    src.mkdir(parents=True)
    (src / "VERSION").write_text("MAJOR=153\nMINOR=0\nBUILD=8010\nPATCH=36\n")
    with pytest.raises(selection.SelectionError, match="source version"):
        selection.select(repo, "linux", src=src.parent)


def test_override_symlink_is_rejected(tmp_path):
    repo = clone_repo(tmp_path)
    name = next(iter(selection.OVERRIDES))
    path = repo / selection.OVERRIDE_ROOT / name
    path.unlink()
    path.symlink_to(repo / "patches" / name)
    with pytest.raises(selection.SelectionError, match="symlink|changed"):
        selection.select(repo, "linux")

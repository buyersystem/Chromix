#!/usr/bin/env python3
"""Collect native DevTools typeface digests and bind them to local font files."""
import argparse
import json
from pathlib import Path

from device_p0_audit import font_sources, font_sample_errors, launch
from chromix._device_host import host_inventory
from chromix._font_provenance import bind_font_sources, font_file_record


def run(browser, font_roots=(), headed=False):
    report = {"schema_version": 1, "browser_sha256": launch.pool.file_hash(browser),
              "files": [], "inventory_errors": [], "samples": [], "errors": [],
              "physical_backend_equivalence": "not_verified"}
    paths = sorted({p for root in font_roots for p in Path(root).rglob("*")
                    if p.suffix.lower() in (".ttf", ".otf", ".ttc")}) if font_roots else [
        Path(row["path"]) for row in host_inventory().get("fonts", {}).get("files", [])
        if Path(row["path"]).suffix.lower() in (".ttf", ".otf", ".ttc")]
    for path in paths:
        try:
            report["files"].append(font_file_record(path))
        except (OSError, ValueError) as error:
            report["inventory_errors"].append({"path": str(path), "error": str(error)})
    try:
        from playwright.sync_api import sync_playwright
        with launch.probe_server() as origin, sync_playwright() as pw:
            instance = pw.chromium.launch(executable_path=str(browser.resolve()), headless=not headed,
                                          args=launch.NATIVE_ARGS, chromium_sandbox=True)
            try:
                report["browser_version"] = instance.version
                context = instance.new_context(no_viewport=True)
                try:
                    page = context.new_page()
                    page.goto(origin)
                    report["samples"] = font_sources(context, page)
                finally:
                    context.close()
            finally:
                instance.close()
    except Exception as error:
        report["errors"].append(str(error))
    report["errors"].extend(font_sample_errors(report["samples"]))
    report["binding"] = bind_font_sources(report["samples"], report["files"])
    if launch.pool.file_hash(browser) != report["browser_sha256"]:
        report["errors"].append("browser executable changed")
    # Recheck file receipts instead of trusting a path collected before launch.
    for record in report["files"]:
        try:
            if launch.pool.file_hash(Path(record["path"])) != record["sha256"]:
                report["errors"].append("font file changed after inventory: " + record["path"])
        except OSError:
            report["errors"].append("font file disappeared after inventory: " + record["path"])
    report["status"] = ("failed" if report["errors"] else
                        "incomplete" if report["inventory_errors"] and report["binding"]["status"] == "passed"
                        else report["binding"]["status"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font-root", type=Path, action="append", default=[])
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args(argv)
    if not args.browser.is_file() or args.output.exists() or any(not root.is_dir() for root in args.font_root):
        parser.error("use an existing browser/font directory and a new output path")
    report = run(args.browser, args.font_root, args.headed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
    print(json.dumps({"status": report["status"], "files": len(report["files"]),
                      "bindings": len(report["binding"]["bindings"]), "errors": report["errors"]}))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())

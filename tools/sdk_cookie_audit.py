#!/usr/bin/env python3
"""Verify encrypted Cookie migration across two owned Chromium processes."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk/python"))
from chromix import cookies
from fingerprint_smoke import sha256_file


# Independent readback oracle: keeping four names alone does not demonstrate
# session/persistent, host-only/domain, HttpOnly or CHIPS migration.
COOKIE_EXPECTATIONS = {
    "session": {"domain": "localhost", "path": "/", "httpOnly": True, "secure": False,
                "priority": "Medium", "sourceScheme": "NonSecure", "sourcePort": 80},
    "__Host-fixture": {"domain": "example.test", "path": "/", "httpOnly": False, "secure": True,
                       "priority": "Medium", "sourceScheme": "Secure", "sourcePort": 443},
    "domain": {"domain": ".example.test", "path": "/", "httpOnly": False, "secure": True,
               "sameSite": "None", "priority": "High", "sourceScheme": "Secure", "sourcePort": 443},
    "partition": {"domain": "example.test", "path": "/", "httpOnly": False, "secure": True,
                  "sameSite": "None", "priority": "Medium", "sourceScheme": "Secure", "sourcePort": 443,
                  "partitionKey": {"topLevelSite": "https://example.test", "hasCrossSiteAncestor": True}},
}


def assess(report):
    errors = []
    runs = report.get("runs", [])
    if len(runs) != 2 or [row.get("phase") for row in runs] != ["export", "import"]:
        return ["Cookie process/restart matrix incomplete"]
    for row in runs:
        try:
            observed = cookies.normalize_cookies(row.get("cookies", []))
            if len(observed) != 4 or {cookie["name"] for cookie in observed} != set(COOKIE_EXPECTATIONS):
                errors.append("Cookie fixture set incomplete")
            for cookie in observed:
                expected = COOKIE_EXPECTATIONS.get(cookie["name"], {})
                if (not expected or cookie["value"] != "chromix-owned-fixture" or
                        any(cookie.get(key) != value for key, value in expected.items()) or
                        ("expires" in cookie) != (cookie["name"] == "__Host-fixture") or
                        ("partitionKey" in cookie) != (cookie["name"] == "partition")):
                    errors.append("Cookie fixture lost host-only/domain, expiry, security or partition semantics")
        except (ValueError, TypeError, KeyError):
            errors.append("invalid Cookie readback")
        if row.get("sibling_unchanged") is not True:
            errors.append("Cookie migration touched or selected a sibling context")
    try:
        cookies._verify(runs[0]["cookies"], runs[1]["cookies"])
    except (KeyError, ValueError, TypeError):
        errors.append("Cookie attributes did not round-trip")
    if report.get("import_result") != {"imported": 4, "skippedExpired": 0, "formatVersion": 1}:
        errors.append("Cookie import did not complete")
    if report.get("export_result") != {"exported": 4, "formatVersion": 1}:
        errors.append("Cookie export did not complete")
    if report.get("encrypted_not_plaintext") is not True:
        errors.append("Cookie envelope missing or contains plaintext fixture")
    return errors


def read(context):
    page = context.new_page()
    session = context.new_cdp_session(page)
    try:
        return cookies.normalize_cookies(session.send("Network.getAllCookies")["cookies"])
    finally:
        session.detach(); page.close()


def seed(context, values):
    page = context.new_page()
    session = context.new_cdp_session(page)
    try:
        session.send("Network.setCookies", {"cookies": values})
    finally:
        session.detach(); page.close()


def run(browser):
    report = {"schema_version": 1, "browser_sha256": sha256_file(browser), "runs": [], "errors": [],
              "qualification": "owned synthetic Cookies; SDK migration, not OSCrypt portability"}
    fixtures = [
        {"name": "session", "value": "chromix-owned-fixture", "url": "http://localhost/", "httpOnly": True},
        {"name": "__Host-fixture", "value": "chromix-owned-fixture", "url": "https://example.test/", "path": "/",
         "secure": True, "expires": int(time.time()) + 600},
        {"name": "domain", "value": "chromix-owned-fixture", "domain": ".example.test", "path": "/", "secure": True,
         "sameSite": "None", "priority": "High"},
        {"name": "partition", "value": "chromix-owned-fixture", "url": "https://example.test/", "path": "/", "secure": True,
         "sameSite": "None", "partitionKey": {"topLevelSite": "https://example.test", "hasCrossSiteAncestor": True}},
    ]
    from playwright.sync_api import sync_playwright
    try:
        with tempfile.TemporaryDirectory(prefix="chromix-cookie-audit-") as temporary, sync_playwright() as pw:
            envelope = Path(temporary) / "cookies.enc"
            password = "ephemeral owned fixture passphrase"
            for phase in ("export", "import"):
                instance = pw.chromium.launch(executable_path=str(browser.resolve()), headless=True,
                                              args=["--disable-background-networking"], chromium_sandbox=True)
                try:
                    report["browser_version"] = instance.version
                    context, sibling = instance.new_context(), instance.new_context()
                    seed(sibling, [{"name": "sibling", "value": "unrelated-owned-fixture", "url": "http://localhost/"}])
                    before = read(sibling)
                    if phase == "export":
                        seed(context, fixtures)
                        report["export_result"] = cookies.export_cookies(context, envelope, passphrase=password)
                        encrypted = envelope.read_bytes()
                        report["encrypted_not_plaintext"] = encrypted.startswith(cookies.MAGIC) and b"chromix-owned-fixture" not in encrypted
                    else:
                        report["import_result"] = cookies.import_cookies(context, envelope, passphrase=password)
                    report["runs"].append({"phase": phase, "cookies": read(context), "sibling_unchanged": before == read(sibling)})
                    context.close(); sibling.close()
                finally:
                    instance.close()
    except Exception as error:
        report["errors"].append(str(error))
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
        parser.error("use an existing browser and new report path")
    report = run(args.browser)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({"status": report["status"], "errors": report["errors"]}))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())

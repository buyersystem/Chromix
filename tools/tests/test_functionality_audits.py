from copy import deepcopy

import pytest

import fingerprint_acceptance as gate
import socks5_browser_audit as socks
import sdk_cookie_audit as cookies
from chromix import _font_provenance as font


def socks_report():
    return {"runs": [
        {"case": "authenticated", "values": [socks.TOKEN] * 3,
         "wire": [{"methods": [2], "authenticated": True,
                   "requests": ["GET / HTTP/1.1", "GET /frame HTTP/1.1", "GET /worker.js HTTP/1.1",
                                *["GET /probe HTTP/1.1"] * 3],
                   "destination": {"host": socks.HOST, "port": 80, "address_type": 3}}]},
        {"case": "wrong_password", "navigation_failed": True, "wire": [{"methods": [2], "auth_attempted": True}]},
        {"case": "downgrade", "navigation_failed": True, "wire": [{"methods": [2]}]},
    ]}


def test_socks_wire_assessment_does_not_trust_top_level_status():
    value = socks_report()
    assert not socks.assess(value)
    for change in ("values", "auth", "downgrade", "wrong-password", "wrong-accepted", "wire-matrix"):
        bad = deepcopy(value)
        bad["status"] = "passed"
        if change == "values":
            bad["runs"][0]["values"] = []
        elif change == "auth":
            bad["runs"][0]["wire"][0]["authenticated"] = False
        elif change == "downgrade":
            bad["runs"][2]["wire"][0]["destination"] = {"host": socks.HOST}
        elif change == "wrong-password":
            bad["runs"][1]["wire"] = []
        elif change == "wrong-accepted":
            bad["runs"][1]["wire"][0]["authenticated"] = True
        else:
            bad["runs"][0]["wire"][0]["requests"] = ["GET /probe HTTP/1.1"]
        assert socks.assess(bad)


def cookie_report():
    data = [{"name": name, "value": "chromix-owned-fixture", **deepcopy(fields),
             **({"expires": 1800000000} if name == "__Host-fixture" else {})}
            for name, fields in cookies.COOKIE_EXPECTATIONS.items()]
    return {"encrypted_not_plaintext": True,
             "export_result": {"exported": 4, "formatVersion": 1},
             "import_result": {"imported": 4, "skippedExpired": 0, "formatVersion": 1},
             "runs": [{"phase": phase, "cookies": deepcopy(data), "sibling_unchanged": True} for phase in ("export", "import")]}


def test_cookie_raw_roundtrip_requires_all_phases_and_sibling_isolation():
    value = cookie_report()
    assert not cookies.assess(value)
    bad = deepcopy(value)
    bad["runs"][1]["cookies"][0]["domain"] = ".localhost"
    assert cookies.assess(bad)
    bad = deepcopy(value)
    bad["runs"][0]["sibling_unchanged"] = False
    assert cookies.assess(bad)
    assert cookies.assess({"status": "passed"})


@pytest.mark.parametrize("name,field,value", [
    ("partition", "partitionKey", None), ("partition", "partitionKey", {"topLevelSite": "https://wrong.test", "hasCrossSiteAncestor": True}),
    ("session", "domain", ".localhost"), ("domain", "domain", "example.test"),
    ("__Host-fixture", "expires", None), ("session", "httpOnly", False), ("domain", "priority", "Low"),
])
def test_cookie_matching_roundtrip_cannot_hide_missing_fixture_semantics(name, field, value):
    report = cookie_report()
    for run in report["runs"]:
        cookie = next(cookie for cookie in run["cookies"] if cookie["name"] == name)
        if value is None:
            cookie.pop(field)
        else:
            cookie[field] = value
    # Both phases have the same corruption: equality alone cannot catch this.
    assert cookies.assess(report)


def font_report():
    digest = "b" * 64
    return {"browser_sha256": "a" * 64, "browser_version": "152.0.7977.82", "status": "passed", "errors": [],
            "binding": {"status": "passed", "unavailable": [], "file_binding_verified": True},
            "samples": [{"family": family, "text": text, "platformFonts": [{"glyphCount": len(text),
                "fontTableHash": digest, "fontTableHashAlgorithm": font.ALGORITHM}]}
                for family in ("serif", "sans-serif", "monospace", "system-ui")
                for text in ("Aa09", "\u4e2d\u6587", "\U0001f600", "\u2211", "\u0378")],
            "files": [{"path": "synthetic-font-fixture.ttf", "sha256": "c" * 64, "size": 128,
                       "faces": [{"face_index": 0, "table_hash": digest, "algorithm": font.ALGORITHM, "table_count": 1}]}]}


@pytest.mark.parametrize("fault", ["native", "unmatched", "ambiguous", "inventory"])
def test_font_recomputed_gaps_cannot_be_hidden_by_saved_summary(fault):
    report = font_report()
    assert gate.assess_suite("font_provenance", report, "a" * 64, "152.0.7977.82") == ([], [])
    if fault == "native":
        sample = report["samples"][0]["platformFonts"][0]
        sample.pop("fontTableHash"); sample.pop("fontTableHashAlgorithm")
    elif fault == "unmatched":
        report["files"] = []
    elif fault == "ambiguous":
        report["files"].append({**deepcopy(report["files"][0]), "path": "duplicate-font-fixture.ttf"})
    else:
        report["inventory_errors"] = [{"path": "unreadable.ttf", "error": "synthetic unreadable fixture"}]
    errors, gaps = gate.assess_suite("font_provenance", report, "a" * 64, "152.0.7977.82")
    assert not errors and gaps


@pytest.mark.parametrize("fault", ["repeated-matrix", "glyph-count", "file-hash", "face-index"])
def test_font_invalid_raw_evidence_is_not_optional(fault):
    report = font_report()
    if fault == "repeated-matrix":
        report["samples"] = [deepcopy(report["samples"][0])] * 20
    elif fault == "glyph-count":
        report["samples"][0]["platformFonts"][0]["glyphCount"] = 0
    elif fault == "file-hash":
        report["files"][0]["sha256"] = "not a file hash"
    else:
        report["files"][0]["faces"][0]["face_index"] = True
    assert gate.assess_suite("font_provenance", report, "a" * 64, "152.0.7977.82")[0]


def test_new_suites_are_in_gate_and_forged_passes_fail():
    names = {name for name, _, _ in gate.SUITES}
    assert {"sdk_cookies", "socks_auth", "font_provenance"} <= names
    for name in ("sdk_cookies", "socks_auth", "font_provenance"):
        report = {"browser_sha256": "a" * 64, "browser_version": "152.0.7977.82", "status": "passed", "errors": []}
        assert gate.assess_suite(name, report, "a" * 64, "152.0.7977.82")[0]

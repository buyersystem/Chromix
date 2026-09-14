"""Pinned patch roundtrips and native SOCKS method byte/async contracts.

Socket buffers, scheduling and networking dependencies are test shims, not a
Chromium build or actual socket-route acceptance. No network connections occur.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
from test_canvas_native_paths import apply, patch_path
from test_fingerprint_canvas import CXX, sanitizer_flags, sanitizer_env
from test_fingerprint_features import block

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
EVIDENCE = json.loads((FIXTURES / "followup_native_sources.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def native_sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp("followup-native-source")
    result = {}
    for number, evidence in EVIDENCE["sources"].items():
        lines = []
        for section in evidence["sections"]:
            start = section["line"] - 1
            assert start >= len(lines)
            lines.extend("// unrelated pinned source line\n" for _ in range(start - len(lines)))
            lines.extend(section["text"].splitlines(True))
        original = "".join(lines) + ("\n// not EOF\n" if number != "0155" else "")
        path = directory / evidence["target"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original.encode())
        apply(directory, patch_path(number))
        result[number] = path.read_text(encoding="utf-8")
        apply(directory, patch_path(number), reverse=True)
        assert path.read_bytes() == original.encode()
    return result


@pytest.mark.parametrize("number", list(EVIDENCE["sources"]))
def test_patch_roundtrip(native_sources, number):
    assert native_sources[number]


@pytest.mark.parametrize("number", list(EVIDENCE["sources"]))
def test_independent_pinned_preimage(tmp_path, number):
    root = os.environ.get("CHROMIX_FOLLOWUP_UPSTREAM_ROOT")
    if not root:
        pytest.skip("independently acquired Chromium 152 source required")
    evidence = EVIDENCE["sources"][number]
    source = Path(root) / evidence["target"]
    original, mtime = source.read_bytes(), source.stat().st_mtime_ns
    assert hashlib.sha256(original).hexdigest() == evidence["upstream_sha256"]
    lines = original.decode().splitlines(True)
    for section in evidence["sections"]:
        start, count = section["line"] - 1, len(section["text"].splitlines())
        assert "".join(lines[start:start + count]) == section["text"]
    dest = tmp_path / evidence["target"]
    dest.parent.mkdir(parents=True)
    dest.write_bytes(original)
    apply(tmp_path, patch_path(number))
    apply(tmp_path, patch_path(number), reverse=True)
    assert dest.read_bytes() == original
    assert source.read_bytes() == original and source.stat().st_mtime_ns == mtime


@pytest.fixture(scope="module")
def socks_binary(tmp_path_factory, native_sources):
    if not CXX:
        pytest.skip("C++20 compiler required")
    source = native_sources["0155"]
    constants = "\n".join(line for line in source.splitlines() if line.startswith(("const unsigned int SOCKS5", "const uint8_t SOCKS5")))
    constants += "\nstatic constexpr std::array<uint8_t,3> kSOCKS5GreetWriteData{5,1,0};\n"
    constants += "static constexpr std::array<uint8_t,3> kSOCKS5AuthGreetWriteData{5,1,2};\n"
    definitions = ""
    for method in ["Connect", "Disconnect", "DoCallback", "OnIOComplete", "DoLoop", "DoGreetWrite", "DoGreetWriteComplete",
                   "DoGreetRead", "DoGreetReadComplete", "BuildAuthWriteBuffer", "DoAuthWrite", "DoAuthWriteComplete",
                   "DoAuthRead", "DoAuthReadComplete", "BuildHandshakeWriteBuffer", "DoHandshakeWrite",
                   "DoHandshakeWriteComplete", "DoHandshakeRead", "DoHandshakeReadComplete"]:
        match = re.search(r"(?:int|void|scoped_refptr<DrainableIOBuffer>) SOCKS5ClientSocket::" + method + r"\(", source)
        assert match, method
        definitions += block(source, match[0]) + "\n"
    shim = (FIXTURES / "socks5_auth_shim.h").read_text()
    names = sorted(set(re.findall(r"NetLogEventType::(\w+)", definitions)))
    shim = shim.replace("// ENUMS injected here.", "enum class NetLogEventType {" + ",".join(names) + "};")
    directory = tmp_path_factory.mktemp("socks5-native")
    unit, binary = directory / "auth.cc", directory / ("auth.exe" if os.name == "nt" else "auth")
    unit.write_text(shim + constants + definitions + (FIXTURES / "socks5_auth_cases.cc").read_text(), encoding="utf-8")
    result = subprocess.run([CXX, "-std=c++20", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                             *sanitizer_flags(), str(unit), "-o", str(binary)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


@pytest.mark.parametrize("mode", [0, 1, 2])
@pytest.mark.parametrize("chunk", [1, 2, 4096])
@pytest.mark.parametrize("case", ["auth", "noauth", "max-auth", "downgrade", "unknown-method", "auth-rejected",
                                 "bad-auth-version", "auth-eof", "zero-write", "write-error", "empty-user",
                                 "empty-pass", "long-user", "long-pass", "long-host"])
def test_native_socks_state_machine(socks_binary, mode, chunk, case):
    result = subprocess.run([str(socks_binary), case, str(mode), str(chunk)], capture_output=True,
                            text=True, timeout=15, env=sanitizer_env())
    assert result.returncode == 0, result.stdout + result.stderr
    rc, connected, *output = result.stdout.strip().split()
    wire = bytes.fromhex(output[0]) if output else b""
    if case in ("auth", "noauth", "max-auth"):
        assert int(rc) == 0 and connected == "1"
        expected = bytes([5, 1, 0 if case == "noauth" else 2])
        if case != "noauth":
            user, password = (b"u" * 255, b"p" * 255) if case == "max-auth" else (b"user", b"pass")
            expected += bytes([1, len(user)]) + user + bytes([len(password)]) + password
        expected += bytes([5, 1, 0, 3, 9]) + b"localhost" + bytes([0, 80])
        assert wire == expected
    else:
        assert int(rc) < 0 and connected == "0"
        if case in ("empty-user", "empty-pass", "long-user", "long-pass", "long-host"):
            assert not wire
        if case in ("downgrade", "unknown-method"):
            assert wire == bytes([5, 1, 2])


def test_network_service_binding_and_early_error_state(native_sources):
    job = native_sources["0156"]
    assert 'server.host_port_pair() == *endpoint' in job
    assert 'base::JSON_PARSE_RFC' in job and 'raw->size() > 4096' in job
    assert 'value->size() != 5' in job
    assert job.index('if (result != OK)') < job.index('next_state_ = STATE_SOCKS_CONNECT_COMPLETE;')
    assert 'UDP ASSOCIATE' not in job

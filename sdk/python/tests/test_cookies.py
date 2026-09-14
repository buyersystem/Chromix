import asyncio
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chromix import cookies as api

PASSWORD = "test-only passphrase 字符"
COOKIE = {"name": "session", "value": "test-value", "domain": "localhost", "path": "/", "secure": False, "httpOnly": True}
PARTITION_ORIGINS = [
    ("https://example.test", True), ("https://example.test.", True), ("https://[::1]", True),
    ("http://127.0.0.1", True), ("https://example.test:444", True), ("https://under_score.test", True),
    ("https://EXAMPLE.test", False), ("https://example.test:443", False), ("https://example.test:0444", False),
    ("https://example.test:", False), ("https://example.test/", False), ("https://127.1", False),
    ("https://0x7f000001", False), ("https://[0:0:0:0:0:0:0:1]", False), ("https://[fe80::1%lo]", False),
]


def test_authenticated_format():
    first = api.encrypt_cookies([COOKIE], PASSWORD)
    assert first != api.encrypt_cookies([COOKIE], PASSWORD)
    assert COOKIE["value"].encode() not in first
    assert api.decrypt_cookies(first, PASSWORD) == [COOKIE]
    with pytest.raises(ValueError):
        api.decrypt_cookies(first, "incorrect passphrase")
    for offset in (0, 17, 33, 50, len(first) - 1):
        data = bytearray(first)
        data[offset] ^= 1
        with pytest.raises(ValueError):
            api.decrypt_cookies(bytes(data), PASSWORD)
    with pytest.raises(ValueError):
        api.decrypt_cookies(first[:-1], PASSWORD)


@pytest.mark.parametrize("change", [
    {"domain": "host/name"}, {"name": "a;b"}, {"value": "a\nb"}, {"expires": float("nan")},
    {"expires": 0}, {"expires": True}, {"secure": 1}, {"partitionKeyOpaque": True},
    {"sourcePort": 65536}, {"path": "relative"}, {"sameSite": "None"},
    {"name": "__Host-test", "domain": ".example.test", "secure": True},
])
def test_preflight(change):
    with pytest.raises(ValueError):
        api.normalize_cookies([{**COOKIE, **change}])


@pytest.mark.parametrize("site,valid", PARTITION_ORIGINS)
def test_canonical_partition_origin(site, valid):
    value = {**COOKIE, "secure": True, "partitionKey": {"topLevelSite": site, "hasCrossSiteAncestor": True}}
    if valid:
        assert api.normalize_cookies([value]) == [value]
    else:
        with pytest.raises(ValueError):
            api.normalize_cookies([value])


def test_partition_origin_contract_matches_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    module = (Path(__file__).resolve().parents[2] / "node/cookies.js").as_uri()
    script = """const api=await import(process.argv[1]);const cookie=JSON.parse(process.argv[2]);
      process.stdout.write(JSON.stringify(JSON.parse(process.argv[3]).map(site=>{
        try{api.normalizeCookies([{...cookie,secure:true,partitionKey:{topLevelSite:site,hasCrossSiteAncestor:true}}]);return true;}
        catch{return false;}
      })));"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module, json.dumps(COOKIE),
                             json.dumps([site for site, _ in PARTITION_ORIGINS])],
                            capture_output=True, encoding="utf-8", check=True, timeout=10)
    assert json.loads(result.stdout) == [valid for _, valid in PARTITION_ORIGINS]


@pytest.mark.parametrize("password", [None, "", "short", "a" * 1025, "\ud800" * 12])
def test_bad_password(password):
    with pytest.raises((ValueError, TypeError)):
        api.encrypt_cookies([COOKIE], password)


def test_node_interoperability(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    module = (Path(__file__).resolve().parents[2] / "node/cookies.js").as_uri()
    data = [{**COOKIE, "domain": ".example.test", "secure": True, "sameSite": "None", "expires": -1,
             "priority": "High", "sourceScheme": "Secure", "sourcePort": 443,
             "partitionKey": {"topLevelSite": "https://example.test", "hasCrossSiteAncestor": True}}]
    source, output = tmp_path / "python.enc", tmp_path / "node.enc"
    source.write_bytes(api.encrypt_cookies(data, PASSWORD))
    script = """import { readFile,writeFile } from 'node:fs/promises';
      const api=await import(process.argv[1]);
      const data=await api.decryptCookies(await readFile(process.argv[2]),process.argv[4]);
      await writeFile(process.argv[3],await api.encryptCookies(data,process.argv[4]));
      process.stdout.write(JSON.stringify(data));"""
    result = subprocess.run([node, "--input-type=module", "-e", script, module, str(source), str(output), PASSWORD],
                            capture_output=True, encoding="utf-8", check=True, timeout=20)
    assert json.loads(result.stdout) == api.normalize_cookies(data)
    assert api.decrypt_cookies(output.read_bytes(), PASSWORD) == api.normalize_cookies(data)


class Context:
    def __init__(self, values=(), fail=False):
        self.values = copy.deepcopy(list(values))
        self.fail, self.calls, self.closed, self.detached = fail, [], 0, 0

    def new_page(self):
        return self

    def new_cdp_session(self, page):
        return self

    def close(self):
        self.closed += 1

    def detach(self):
        self.detached += 1

    def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"type": "page", "browserContextId": "owned"}}
        assert "browserContextId" not in params
        if method == "Network.getAllCookies":
            return {"cookies": self.values}
        if method == "Network.setCookies":
            if self.fail:
                raise RuntimeError("write failed")
            self.values = []
            for cookie in params["cookies"]:
                cookie = dict(cookie)
                if "url" in cookie:
                    cookie["domain"] = api.urlsplit(cookie.pop("url")).hostname
                self.values.append(cookie)
            return {}
        raise AssertionError(method)


def test_live_api_contract(tmp_path):
    path = tmp_path / "cookies.enc"
    source = Context([COOKIE])
    assert api.export_cookies(source, path, passphrase=PASSWORD)["exported"] == 1
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        api.export_cookies(source, path, passphrase=PASSWORD)
    assert original == path.read_bytes()
    assert len(list(tmp_path.iterdir())) == 1
    target = Context()
    assert api.import_cookies(target, path, passphrase=PASSWORD)["imported"] == 1
    params = next(params for method, params in target.calls if method == "Network.setCookies")
    assert "domain" not in params["cookies"][0]
    assert params["cookies"][0]["url"] == "http://localhost/"
    assert target.closed == target.detached == 1
    occupied = Context([COOKIE])
    with pytest.raises(ValueError, match="empty destination"):
        api.import_cookies(occupied, path, passphrase=PASSWORD)
    assert not any(method == "Network.setCookies" for method, _ in occupied.calls)
    failing = Context(fail=True)
    with pytest.raises(RuntimeError, match="write failed"):
        api.import_cookies(failing, path, passphrase=PASSWORD)
    assert failing.closed == failing.detached == 1


def test_expired_not_imported_and_numeric_verification(tmp_path):
    path = tmp_path / "expired.enc"
    path.write_bytes(api.encrypt_cookies([{**COOKIE, "expires": 1}], PASSWORD))
    target = Context()
    assert api.import_cookies(target, path, passphrase=PASSWORD)["skippedExpired"] == 1
    assert not any(method == "Network.setCookies" for method, _ in target.calls)
    api._verify([{**COOKIE, "expires": 1234.0}], [{**COOKIE, "expires": 1234}])


class AsyncContext(Context):
    async def new_page(self):
        return self

    async def new_cdp_session(self, page):
        return self

    async def close(self):
        super().close()

    async def detach(self):
        super().detach()

    async def send(self, *args):
        return super().send(*args)


def test_async_contract(tmp_path):
    async def run():
        path = tmp_path / "async.enc"
        source, target = AsyncContext([COOKIE]), AsyncContext()
        await api.export_cookies_async(source, path, passphrase=PASSWORD)
        assert (await api.import_cookies_async(target, path, passphrase=PASSWORD))["imported"] == 1
        assert target.closed == target.detached == source.closed == source.detached == 1
    asyncio.run(run())

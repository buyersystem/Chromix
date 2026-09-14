"""Authenticated, explicit migration of a supplied live Chromium context's Cookies.

Format v1 interoperates with the Node SDK. It does not change OSCrypt, read an
offline profile database, or clear/overwrite Cookies in a populated context.
"""
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
from urllib.parse import urlsplit

MAGIC = b"CHROMIX-COOKIES\x00\x01"
HEADER = len(MAGIC) + 16 + 12
MAX_BYTES = 16 * 1024 * 1024


def _invalid():
    return ValueError("Invalid or unsupported portable Cookie data")


def _password(value):
    if not isinstance(value, str):
        raise TypeError("passphrase must be valid Unicode text")
    data = value.encode("utf-8", errors="strict")
    if not 12 <= len(data) <= 1024:
        raise ValueError("passphrase must contain 12–1024 UTF-8 bytes")
    return data


def _text(value, maximum, allow_empty=True):
    if not isinstance(value, str) or (not allow_empty and not value) or re.search(r"[\x00-\x1f\x7f]", value):
        raise _invalid()
    if len(value.encode("utf-8", errors="strict")) > maximum:
        raise _invalid()
    return value


def _host(domain):
    host = domain[1:] if domain.startswith(".") else domain
    bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        if ipaddress.ip_address(bare).version == 6 and not domain.startswith(".") and "%" not in bare:
            return "[" + bare + "]"
    except ValueError:
        pass
    if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*\.?", host) or len(host.rstrip(".")) > 253:
        raise _invalid()
    return host


def normalize_cookies(cookies):
    if not isinstance(cookies, list) or len(cookies) > 10000:
        raise _invalid()
    result, keys = [], set()
    for value in cookies:
        if not isinstance(value, dict):
            raise _invalid()
        cookie = {"name": _text(value.get("name"), 4096), "value": _text(value.get("value"), 16384),
                  "domain": _text(value.get("domain"), 255, False), "path": _text(value.get("path"), 4096, False),
                  "secure": value.get("secure"), "httpOnly": value.get("httpOnly")}
        _host(cookie["domain"])
        if (not cookie["path"].startswith("/") or type(cookie["secure"]) is not bool or
                type(cookie["httpOnly"]) is not bool or re.search(r"[;=]", cookie["name"]) or ";" in cookie["value"]):
            raise _invalid()
        if value.get("partitionKeyOpaque") is True:
            raise ValueError("Opaque partition Cookies cannot be migrated")
        for field, choices in (("sameSite", ("Strict", "Lax", "None")),
                               ("priority", ("Low", "Medium", "High")),
                               ("sourceScheme", ("Unset", "NonSecure", "Secure"))):
            if field in value:
                if value[field] not in choices:
                    raise _invalid()
                cookie[field] = value[field]
        if "expires" in value:
            expires = value["expires"]
            if type(expires) not in (float, int) or not math.isfinite(expires) or (expires != -1 and expires <= 0):
                raise _invalid()
            if expires != -1:
                cookie["expires"] = expires
        if "sourcePort" in value:
            if type(value["sourcePort"]) is not int or not -1 <= value["sourcePort"] <= 65535:
                raise _invalid()
            cookie["sourcePort"] = value["sourcePort"]
        if "partitionKey" in value:
            partition = value["partitionKey"]
            if not isinstance(partition, dict) or type(partition.get("hasCrossSiteAncestor")) is not bool:
                raise _invalid()
            site = _text(partition.get("topLevelSite"), 2048, False)
            url = urlsplit(site)
            if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or
                    url.path or url.query or url.fragment or site != url.scheme + "://" + url.netloc):
                raise _invalid()
            # Require a canonical serialized origin as returned by CDP.
            host = _host(url.hostname)
            if ":" in url.hostname:
                host = "[" + str(ipaddress.IPv6Address(url.hostname)) + "]"
            elif re.fullmatch(r"[0-9]+|0[xX][0-9a-fA-F]+", url.hostname.rstrip(".").rsplit(".", 1)[-1]):
                # Reject WHATWG's noncanonical shorthand/hex/octal IPv4 URLs.
                host = str(ipaddress.IPv4Address(url.hostname))
            port = url.port
            origin = url.scheme + "://" + host.lower()
            if port is not None and port != (443 if url.scheme == "https" else 80):
                origin += ":" + str(port)
            if site != origin:
                raise _invalid()
            cookie["partitionKey"] = {"topLevelSite": site, "hasCrossSiteAncestor": partition["hasCrossSiteAncestor"]}
        if cookie["name"].startswith("__Secure-") and not cookie["secure"]:
            raise _invalid()
        if cookie["name"].startswith("__Host-") and (not cookie["secure"] or cookie["path"] != "/" or cookie["domain"].startswith(".")):
            raise _invalid()
        if (cookie.get("sameSite") == "None" or "partitionKey" in cookie) and not cookie["secure"]:
            raise _invalid()
        key = (cookie["name"], cookie["domain"], cookie["path"], json.dumps(cookie.get("partitionKey"), sort_keys=True))
        if key in keys:
            raise ValueError("Duplicate portable Cookie identity")
        keys.add(key)
        result.append(cookie)
    return result


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise ImportError("Install the Cookie migration extra: pip install 'chromix[cookies]'") from error
    return AESGCM


def _key(password, salt):
    return hashlib.scrypt(password, salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


def encrypt_cookies(cookies, passphrase):
    payload = json.dumps({"format": "chromix.cookies", "version": 1, "cookies": normalize_cookies(cookies)},
                         ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > MAX_BYTES - HEADER - 16:
        raise _invalid()
    password = _password(passphrase)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = MAGIC + salt + nonce
    return header + _aesgcm()(_key(password, salt)).encrypt(nonce, payload, header)


def decrypt_cookies(data, passphrase):
    if not isinstance(data, bytes) or not HEADER + 16 <= len(data) <= MAX_BYTES or not data.startswith(MAGIC):
        raise _invalid()
    password = _password(passphrase)
    salt, nonce = data[len(MAGIC):len(MAGIC) + 16], data[len(MAGIC) + 16:HEADER]
    aes = _aesgcm()(_key(password, salt))
    try:
        payload = json.loads(aes.decrypt(nonce, data[HEADER:], data[:HEADER]).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("format") != "chromix.cookies" or type(payload.get("version")) is not int or payload["version"] != 1:
            raise _invalid()
        return normalize_cookies(payload["cookies"])
    except Exception as error:
        raise ValueError("Cannot decrypt portable Cookies: wrong passphrase, damaged data or unsupported payload") from error


def _read(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise _invalid()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise _invalid()
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise _invalid()
    return data


def _publish(path, data):
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(16) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink()


def _params(cookie):
    result = dict(cookie)
    if not cookie["domain"].startswith("."):
        secure = cookie.get("sourceScheme") == "Secure" or (cookie.get("sourceScheme") != "NonSecure" and cookie["secure"])
        port = ":" + str(cookie["sourcePort"]) if cookie.get("sourcePort", -1) > 0 else ""
        result["url"] = ("https" if secure else "http") + "://" + _host(cookie["domain"]) + port + "/"
        del result["domain"]
    return result


def _context_params(target):
    if target["targetInfo"].get("type") != "page":
        raise ValueError("Cookie migration requires the supplied context's page target")
    # Network methods use this page's storage partition. Storage.* needs a
    # browser target and otherwise may silently select the default context.
    return {}


def _verify(actual, expected):
    # JSON integer/double spellings differ between Node and Python; compare
    # semantic numeric values, not their JSON serialization (1 == 1.0).
    key = lambda value: (value["domain"], value["path"], value["name"], json.dumps(value.get("partitionKey"), sort_keys=True))
    canonical = lambda values: sorted(normalize_cookies(values), key=key)
    if canonical(actual) != canonical(expected):
        raise ValueError("Cookie import verification failed; discard this destination context (no automatic clearing or rollback)")


def export_cookies(context, path, *, passphrase):
    _password(passphrase)
    page, session = context.new_page(), None
    try:
        session = context.new_cdp_session(page)
        params = _context_params(session.send("Target.getTargetInfo"))
        cookies = session.send("Network.getAllCookies", params)["cookies"]
    finally:
        try:
            if session is not None:
                session.detach()
        finally:
            page.close()
    _publish(path, encrypt_cookies(cookies, passphrase))
    return {"exported": len(cookies), "formatVersion": 1}


def import_cookies(context, path, *, passphrase):
    cookies = decrypt_cookies(_read(path), passphrase)
    now = time.time()
    active = [cookie for cookie in cookies if cookie.get("expires", math.inf) > now]
    page, session = context.new_page(), None
    try:
        session = context.new_cdp_session(page)
        params = _context_params(session.send("Target.getTargetInfo"))
        if session.send("Network.getAllCookies", params)["cookies"]:
            raise ValueError("Cookie import requires an empty destination context; existing Cookies are never cleared")
        if active:
            session.send("Network.setCookies", {**params, "cookies": [_params(cookie) for cookie in active]})
        _verify(session.send("Network.getAllCookies", params)["cookies"], active)
        return {"imported": len(active), "skippedExpired": len(cookies) - len(active), "formatVersion": 1}
    finally:
        try:
            if session is not None:
                session.detach()
        finally:
            page.close()


async def export_cookies_async(context, path, *, passphrase):
    import asyncio
    _password(passphrase)
    page, session = await context.new_page(), None
    try:
        session = await context.new_cdp_session(page)
        params = _context_params(await session.send("Target.getTargetInfo"))
        cookies = (await session.send("Network.getAllCookies", params))["cookies"]
    finally:
        try:
            if session is not None:
                await session.detach()
        finally:
            await page.close()
    def save():
        _publish(path, encrypt_cookies(cookies, passphrase))
    await asyncio.get_running_loop().run_in_executor(None, save)
    return {"exported": len(cookies), "formatVersion": 1}


async def import_cookies_async(context, path, *, passphrase):
    import asyncio
    cookies = await asyncio.get_running_loop().run_in_executor(None, lambda: decrypt_cookies(_read(path), passphrase))
    now = time.time()
    active = [cookie for cookie in cookies if cookie.get("expires", math.inf) > now]
    page, session = await context.new_page(), None
    try:
        session = await context.new_cdp_session(page)
        params = _context_params(await session.send("Target.getTargetInfo"))
        if (await session.send("Network.getAllCookies", params))["cookies"]:
            raise ValueError("Cookie import requires an empty destination context; existing Cookies are never cleared")
        if active:
            await session.send("Network.setCookies", {**params, "cookies": [_params(cookie) for cookie in active]})
        _verify((await session.send("Network.getAllCookies", params))["cookies"], active)
        return {"imported": len(active), "skippedExpired": len(cookies) - len(active), "formatVersion": 1}
    finally:
        try:
            if session is not None:
                await session.detach()
        finally:
            await page.close()

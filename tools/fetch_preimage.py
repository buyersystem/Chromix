"""Bounded Gitiles preimage fetches; transport retries never relax integrity."""
import base64
import binascii
import hashlib
import http.client
import re
import socket
import sys
import time
import urllib.error
import urllib.request

MAX_ENCODED_BYTES = 16 * 1024 * 1024
ATTEMPTS = 4
TIMEOUT_SECONDS = 30
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}


def fetch_preimage(url, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("expected preimage SHA256 must be 64 lowercase hex digits")
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
                encoded = response.read(MAX_ENCODED_BYTES + 1)
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as error:
            if isinstance(error, urllib.error.HTTPError):
                retry = error.code in TRANSIENT_HTTP
                if error.fp is not None:
                    error.close()
            elif isinstance(error, urllib.error.URLError):
                reason = error.reason
                retry = isinstance(reason, (TimeoutError, ConnectionError)) or (
                    isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN)
            else:
                retry = True
            if not retry or attempt == ATTEMPTS - 1:
                raise
            delay = 2 ** (attempt + 1)
            print(f"{url}: {error}; retry {attempt + 2}/{ATTEMPTS} in {delay}s",
                  file=sys.stderr, flush=True)
            time.sleep(delay)
    if len(encoded) > MAX_ENCODED_BYTES:
        raise ValueError("encoded preimage exceeds the 16 MiB limit")
    try:
        data = base64.b64decode(encoded, validate=True)
    except binascii.Error as error:
        raise ValueError("preimage response is not valid base64") from error
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("preimage SHA256 mismatch: " + url)
    return data

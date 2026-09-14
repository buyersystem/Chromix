import base64
import hashlib
import http.client
import io
from pathlib import Path
import socket
import ssl
import urllib.error
from unittest import mock

import pytest

from tools import fetch_preimage as fetch

URL = "https://chromium.googlesource.com/chromium/src/+/refs/tags/152.0.7977.82/source.cc?format=TEXT"
DATA = b"independent pinned source fixture\n"
DIGEST = hashlib.sha256(DATA).hexdigest()


def response(data=DATA):
    return io.BytesIO(base64.b64encode(data))


def http_error(code):
    return urllib.error.HTTPError(URL, code, "fixture status", {}, io.BytesIO(b"error"))


@pytest.fixture
def network(monkeypatch):
    opener, sleep = mock.Mock(), mock.Mock()
    monkeypatch.setattr(fetch.urllib.request, "urlopen", opener)
    monkeypatch.setattr(fetch.time, "sleep", sleep)
    return opener, sleep


def test_success_reads_a_bounded_response_and_checks_hash(network):
    opener, sleep = network
    stream = response()
    original = stream.read
    stream.read = mock.Mock(side_effect=original)
    opener.return_value = stream
    assert fetch.fetch_preimage(URL, DIGEST) == DATA
    opener.assert_called_once_with(URL, timeout=30)
    stream.read.assert_called_once_with(16 * 1024 * 1024 + 1)
    assert stream.closed
    sleep.assert_not_called()


@pytest.mark.parametrize("code", sorted(fetch.TRANSIENT_HTTP))
def test_transient_http_retries_without_changing_url_or_expected_digest(network, code):
    opener, sleep = network
    errors = [http_error(code) for _ in range(3)]
    opener.side_effect = [*errors, response()]
    assert fetch.fetch_preimage(URL, DIGEST) == DATA
    assert opener.call_args_list == [mock.call(URL, timeout=30)] * 4
    assert sleep.call_args_list == [mock.call(2), mock.call(4), mock.call(8)]
    assert all(error.closed for error in errors)


def test_exhausted_503_retries_propagate_failure(network):
    opener, sleep = network
    opener.side_effect = [http_error(503) for _ in range(4)]
    with pytest.raises(urllib.error.HTTPError) as error:
        fetch.fetch_preimage(URL, DIGEST)
    assert error.value.code == 503
    assert opener.call_count == 4 and sleep.call_count == 3


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410])
def test_permanent_http_errors_are_not_retried(network, code):
    opener, sleep = network
    opener.side_effect = http_error(code)
    with pytest.raises(urllib.error.HTTPError):
        fetch.fetch_preimage(URL, DIGEST)
    assert opener.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"), ConnectionResetError("reset"), http.client.IncompleteRead(b"partial"),
    urllib.error.URLError(TimeoutError("connect timeout")),
    urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS")),
])
def test_transient_transport_errors_are_retried(network, error):
    opener, sleep = network
    opener.side_effect = [error, response()]
    assert fetch.fetch_preimage(URL, DIGEST) == DATA
    sleep.assert_called_once_with(2)


@pytest.mark.parametrize("error", [
    urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate")),
    urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "unknown name")),
    urllib.error.URLError("unsupported route"),
])
def test_certificate_and_permanent_transport_errors_fail_immediately(network, error):
    opener, sleep = network
    opener.side_effect = error
    with pytest.raises(urllib.error.URLError):
        fetch.fetch_preimage(URL, DIGEST)
    assert opener.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("payload,pattern", [(b"not base64!", "base64"),
                                             (base64.b64encode(b"wrong source"), "SHA256 mismatch")])
def test_bad_content_is_never_retried_or_accepted(network, payload, pattern):
    opener, sleep = network
    opener.return_value = io.BytesIO(payload)
    with pytest.raises(ValueError, match=pattern):
        fetch.fetch_preimage(URL, DIGEST)
    assert opener.call_count == 1
    sleep.assert_not_called()


def test_oversized_response_does_not_decode_or_retry(network, monkeypatch):
    opener, sleep = network
    monkeypatch.setattr(fetch, "MAX_ENCODED_BYTES", 8)
    opener.return_value = io.BytesIO(b"A" * 100)
    with pytest.raises(ValueError, match="exceeds"):
        fetch.fetch_preimage(URL, DIGEST)
    assert opener.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("digest", [None, "", "0" * 63, "g" * 64, "A" * 64])
def test_bad_expected_digest_fails_before_network(network, digest):
    opener, sleep = network
    with pytest.raises(ValueError, match="SHA256"):
        fetch.fetch_preimage(URL, digest)
    opener.assert_not_called()
    sleep.assert_not_called()


def test_workflow_keeps_pinned_fetch_and_does_not_skip_source_verification():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/fingerprint-contracts.yml").read_text()
    assert "from fetch_preimage import fetch_preimage" in workflow
    assert "data = fetch_preimage(url, digest)" in workflow
    assert "refs/tags/{version}/{path}?format=TEXT" in workflow
    assert "CHROMIX_FOLLOWUP_UPSTREAM_ROOT={root}" in workflow
    assert "CHROMIX_CANVAS_UPSTREAM_ROOT={root}" in workflow

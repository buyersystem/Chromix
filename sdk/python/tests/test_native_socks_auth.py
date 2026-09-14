import copy
import json
import os
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chromix._socks_auth import native_socks_config, apply_native_socks_auth, SOCKS_AUTH_ENV
from chromix._fonts import apply_font_env


def test_native_auth_is_bound_and_not_in_browser_proxy_fields(monkeypatch):
    proxy = {"server": "socks5://[::1]:1080", "username": "用户", "password": "päss", "bypass": "<-loopback>"}
    original = copy.deepcopy(proxy)
    kwargs, launch, args = {"proxy": proxy}, {"env": {"KEEP": "yes"}}, ["--proxy-server=socks5://[::1]:1080"]
    monkeypatch.setenv(SOCKS_AUTH_ENV, "other-profile-secret")
    apply_native_socks_auth(kwargs, launch, args)
    assert kwargs["proxy"] == {"server": "socks5://[::1]:1080", "bypass": "<-loopback>"}
    assert json.loads(launch["env"][SOCKS_AUTH_ENV]) == {"version": 1, "host": "::1", "port": 1080, "username": "用户", "password": "päss"}
    assert launch["env"]["KEEP"] == "yes"
    assert args == [] and proxy == original
    assert os.environ[SOCKS_AUTH_ENV] == "other-profile-secret"


def test_unrelated_launch_drops_ambient_credentials(monkeypatch):
    monkeypatch.setenv(SOCKS_AUTH_ENV, "other-profile-secret")
    kwargs, launch = {}, {}
    apply_native_socks_auth(kwargs, launch, [])
    assert SOCKS_AUTH_ENV not in launch["env"]
    apply_font_env("uninstalled-fixture-browser", launch)
    assert SOCKS_AUTH_ENV not in launch["env"]
    assert os.environ[SOCKS_AUTH_ENV] == "other-profile-secret"


def test_font_environment_preserves_only_explicit_bound_auth(monkeypatch):
    monkeypatch.setenv(SOCKS_AUTH_ENV, "other-profile-secret")
    kwargs, launch = {"proxy": "socks5://user:pass@localhost:1080"}, {"env": {"KEEP": "yes"}}
    apply_native_socks_auth(kwargs, launch, [])
    bound = launch["env"][SOCKS_AUTH_ENV]
    apply_font_env("uninstalled-fixture-browser", launch)
    assert launch["env"][SOCKS_AUTH_ENV] == bound
    assert launch["env"]["KEEP"] == "yes"


@pytest.mark.parametrize("key", [SOCKS_AUTH_ENV, SOCKS_AUTH_ENV.lower(), "Chromix_Socks5_Auth"])
def test_reserved_environment_is_case_insensitive(key):
    with pytest.raises(ValueError, match="Use proxy"):
        apply_native_socks_auth({}, {"env": {key: "raw"}}, [])


@pytest.mark.parametrize("username,password", [("", "p"), ("u", ""), ("u" * 256, "p"), ("u", "字" * 86)])
def test_rfc_byte_lengths(username, password):
    with pytest.raises(ValueError):
        native_socks_config({"server": "socks5://localhost:1080", "username": username, "password": password})


@pytest.mark.parametrize("args", [["--no-proxy-server"], ["--proxy-server=socks5://other:1080"], ["--proxy-pac-url=http://local/pac"]])
def test_conflicting_routes_fail(args):
    with pytest.raises(ValueError):
        native_socks_config("socks5://u:p@localhost:1080", args)


def test_http_auth_stays_native_and_raw_env_is_not_accepted():
    config, auth = native_socks_config("http://u:p@localhost:8080")
    assert config["username"] == "u" and auth is None
    with pytest.raises(ValueError, match="Use proxy"):
        apply_native_socks_auth({}, {"env": {SOCKS_AUTH_ENV: "raw"}}, [])

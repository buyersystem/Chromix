"""Endpoint-scoped native SOCKS5 authentication for patched Chromium launches."""
import json
import os
from urllib.parse import urlsplit
from ._network import split_proxy, lookup_proxy

SOCKS_AUTH_ENV = "CHROMIX_SOCKS5_AUTH"


def has_native_socks_env(env):
    return any(key.upper() == SOCKS_AUTH_ENV for key in (env or {}))


def without_native_socks_env(env):
    return {key: value for key, value in env.items() if key.upper() != SOCKS_AUTH_ENV}


def native_socks_config(proxy, args=()):
    config = split_proxy(proxy)
    if (not config or urlsplit(config["server"]).scheme not in ("socks", "socks5", "socks5h") or
            not any(key in config for key in ("username", "password"))):
        return config, None
    lookup_proxy(args, config)
    for key in ("username", "password"):
        value = config.get(key)
        if not isinstance(value, str) or not 1 <= len(value.encode("utf-8", errors="strict")) <= 255:
            raise ValueError("Native SOCKS5 username and password must each contain 1–255 UTF-8 bytes")
    url = urlsplit(config["server"])
    auth = json.dumps({"version": 1, "host": url.hostname, "port": url.port or 1080,
                       "username": config["username"], "password": config["password"]},
                      ensure_ascii=True, separators=(",", ":"))
    clean = {"server": "socks5://" + url.netloc}
    if "bypass" in config:
        clean["bypass"] = config["bypass"]
    return clean, auth


def apply_native_socks_auth(proxy_kwargs, launch_kwargs, args):
    config, auth = native_socks_config(proxy_kwargs.get("proxy"), args)
    env = launch_kwargs.get("env")
    if has_native_socks_env(env):
        raise ValueError("Use proxy for native SOCKS5 credentials, not a raw authentication environment variable")
    # Invoke before font env assembly; remove inherited launch-unrelated auth.
    if auth or has_native_socks_env(os.environ):
        env = {**without_native_socks_env(os.environ), **(env or {})}
        if auth:
            env[SOCKS_AUTH_ENV] = auth
        launch_kwargs["env"] = env
    if config is not None:
        proxy_kwargs["proxy"] = config
    if auth:
        args[:] = [arg for arg in args if arg.partition("=")[0] != "--proxy-server"]

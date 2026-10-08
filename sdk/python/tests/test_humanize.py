"""Paced-input contracts and opt-in local Chrome checks."""
import asyncio
from dataclasses import asdict
from functools import wraps
import os
from types import SimpleNamespace

import pytest

from chromix.humanize import HumanConfig, _merge_config, patch_page, resolve_human_config


FAST = dict(typing_delay=0, typing_delay_spread=0, typing_pause_chance=0,
            mistype_chance=0, click_aim_delay=0, click_hold=0,
            mouse_overshoot_chance=0, mouse_min_steps=1, seed=7)


def mock_page(asynchronous=False):
    calls = []

    def record(name, fn):
        @wraps(fn)
        def sync(*args, **kwargs):
            calls.append((name, args, kwargs))
            return fn(*args, **kwargs)
        @wraps(fn)
        async def async_call(*args, **kwargs):
            return sync(*args, **kwargs)
        return async_call if asynchronous else sync

    mouse = SimpleNamespace(
        move=record("move", lambda x, y, *, steps=None: None),
        click=record("click", lambda x, y, *, button=None, click_count=None, delay=None: None),
        dblclick=record("dblclick", lambda x, y, *, button=None, delay=None: None),
        wheel=record("wheel", lambda delta_x, delta_y: None))
    keyboard = SimpleNamespace(
        type=record("type", lambda text, *, delay=None: None),
        press=record("press", lambda key, *, delay=None: None))
    return SimpleNamespace(mouse=mouse, keyboard=keyboard), calls


@pytest.mark.parametrize("asynchronous", [False, True])
def test_call_config_isolation_and_native_options(monkeypatch, asynchronous):
    import chromix.humanize as sync_module
    import chromix.humanize_async as async_module
    sleeps = []
    monkeypatch.setattr(sync_module.time, "sleep", sleeps.append)
    async def sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(async_module.asyncio, "sleep", sleep)
    page, calls = mock_page(asynchronous)
    cfg = resolve_human_config(overrides=FAST)
    before = asdict(cfg)
    patch_page(page, cfg)
    original = page.mouse.click
    patch_page(page, HumanConfig())
    assert original is page.mouse.click

    def actions():
        yield page.keyboard.type, ("ab",), {"human_config": {"typing_delay": 20}}
        yield page.keyboard.type, ("c",), {}
        yield page.keyboard.type, ("d",), {"delay": 5, "human_config": {"typing_delay": 99}}
        yield page.mouse.click, (1, 1), {"click_count": 3, "delay": 9, "button": "right"}
        yield page.mouse.dblclick, (1, 1), {"delay": 4}
        yield page.keyboard.press, ("Enter",), {"delay": 11}
        yield page.mouse.wheel, (), {"delta_x": 1.25, "delta_y": 2.5}
        yield page.mouse.move, (5, 5), {"steps": 2}

    async def run():
        for fn, args, kwargs in actions():
            result = fn(*args, **kwargs)
            if asynchronous:
                await result
        with pytest.raises(TypeError):
            result = page.mouse.click(1, 1, bogus=True)
            if asynchronous:
                await result
        with pytest.raises(ValueError, match="Unsupported human_config"):
            result = page.keyboard.type("x", human_config={"idle_between_actions": True})
            if asynchronous:
                await result
    asyncio.run(run())
    assert asdict(cfg) == before
    assert sleeps[:4] == [.02, .02, 0, .005]
    clicks = [kw for name, _, kw in calls if name == "click"]
    assert clicks == [dict(button="right", click_count=3, delay=9), dict(button="left", click_count=2, delay=4)]
    assert ("press", ("Enter",), {"delay": 11}) in calls
    wheels = [args for name, args, _ in calls if name == "wheel"]
    assert sum(x for x, _ in wheels) == pytest.approx(1.25)
    assert sum(y for _, y in wheels) == pytest.approx(2.5)


@pytest.mark.parametrize("overrides", [{"mouse_steps_divisor": 0}, {"typing_delay": -1},
    {"typing_delay": float("nan")}, {"mistype_chance": 2}, {"mouse_min_steps": 0},
    {"unknown": 3}])
def test_invalid_config(overrides):
    with pytest.raises(ValueError):
        resolve_human_config(overrides=overrides)


def test_merge_does_not_mutate_presets():
    base = resolve_human_config("careful", {"seed": 4})
    assert _merge_config(base, {"typing_delay": 1}).typing_delay == 1
    assert base.typing_delay == 130
    assert resolve_human_config("careful").typing_delay == 130


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HTML = """<style>#moving {position:relative; animation:slide .2s linear}
@keyframes slide {from {left:0} to {left:30px}}</style>
<button id=moving onclick='window.clicks++'>moving</button>
<button id=late disabled style='display:none' onclick='window.clicks++'>late</button>
<button id=disabled disabled>disabled</button>
<input id=text><input id=readonly readonly><input id=date type=date>
<script>window.clicks=0; setTimeout(()=>{late.style.display='block';late.disabled=false},150)</script>"""


def browser_actions(page):
    yield page.set_content, (HTML,), {}
    yield page.click, ("#moving",), {"timeout": 1500, "human_config": FAST}
    yield page.click, ("#late",), {"timeout": 1500, "human_config": FAST}
    yield page.click, ("#moving",), {"trial": True, "human_config": {"click_aim_delay": 10000}}
    yield page.fill, ("#text", "hello"), {"human_config": FAST}
    yield page.type, ("#text", "!"), {"delay": 0, "human_config": FAST}
    yield page.fill, ("#date", "2026-10-08"), {"human_config": FAST}
    yield page.hover, ("#moving",), {"human_config": FAST, "position": {"x": 2, "y": 2}}
    yield page.click, ("#disabled",), {"force": True, "timeout": 200}
    yield page.keyboard.press, ("Tab",), {"delay": 0, "human_config": FAST}


@pytest.mark.skipif(os.environ.get("CHROMIX_TEST_LOCAL_CHROME") != "1", reason="opt-in local Chrome")
@pytest.mark.parametrize("asynchronous", [False, True])
def test_local_chrome(asynchronous):
    from playwright.sync_api import TimeoutError
    async def async_main():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME, headless=True)
            try:
                page = await browser.new_page()
                patch_page(page, resolve_human_config(overrides=FAST))
                for fn, args, kwargs in browser_actions(page):
                    await fn(*args, **kwargs)
                assert await page.input_value("#text") == "hello!"
                assert await page.input_value("#date") == "2026-10-08"
                assert await page.evaluate("window.clicks") == 2
                for selector in ("#disabled", "#missing"):
                    with pytest.raises(TimeoutError):
                        await page.click(selector, timeout=80)
                with pytest.raises(TimeoutError):
                    await page.fill("#readonly", "x", timeout=80)
                with pytest.raises(TimeoutError):
                    await page.click("#moving", timeout=80, human_config={"click_aim_delay": 5000})
                with pytest.raises(TypeError):
                    await page.click("#moving", unsupported=True)
                await page.click("#moving", timeout=0, human_config=FAST)
                await page.locator("#moving").click()
                assert await page.evaluate("window.clicks") == 4
                page.set_default_timeout(60)
                with pytest.raises(TimeoutError):
                    await page.click("#disabled")
            finally:
                await browser.close()
    if asynchronous:
        asyncio.run(async_main())
    else:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROME, headless=True)
            try:
                page = browser.new_page()
                patch_page(page, resolve_human_config(overrides=FAST))
                for fn, args, kwargs in browser_actions(page):
                    fn(*args, **kwargs)
                assert page.input_value("#text") == "hello!"
                assert page.input_value("#date") == "2026-10-08"
                assert page.evaluate("window.clicks") == 2
                for selector in ("#disabled", "#missing"):
                    with pytest.raises(TimeoutError):
                        page.click(selector, timeout=80)
                with pytest.raises(TimeoutError):
                    page.fill("#readonly", "x", timeout=80)
                with pytest.raises(TimeoutError):
                    page.click("#moving", timeout=80, human_config={"click_aim_delay": 5000})
                with pytest.raises(TypeError):
                    page.click("#moving", unsupported=True)
                page.click("#moving", timeout=0, human_config=FAST)
                page.locator("#moving").click()
                assert page.evaluate("window.clicks") == 4
                page.set_default_timeout(60)
                with pytest.raises(TimeoutError):
                    page.click("#disabled")
            finally:
                browser.close()


@pytest.mark.skipif(os.environ.get("CHROMIX_TEST_LOCAL_CHROME") != "1", reason="opt-in local Chrome")
def test_async_launch_page_paths(monkeypatch, tmp_path):
    from pathlib import Path
    from chromix import api
    monkeypatch.setattr(api, "_prepare", lambda *args, **kwargs: (Path(CHROME), [], {}, None))
    monkeypatch.setattr(api, "apply_font_env", lambda *args, **kwargs: None)

    async def run():
        browser = await api.launch_async(humanize=True, human_config=FAST, stealth_args=False)
        try:
            direct = await browser.new_page()
            context = await browser.new_context()
            nested = await context.new_page()
            for page in (direct, nested):
                await page.set_content("<input id=x>")
                await page.fill("#x", "works", human_config=FAST)
                assert await page.input_value("#x") == "works"
        finally:
            await browser.close()
        context = await api.launch_persistent_context_async(
            user_data_dir=tmp_path / "profile", humanize=True, human_config=FAST,
            stealth_args=False)
        try:
            pages = list(context.pages) + [await context.new_page()]
            assert len(pages) >= 2
            for page in pages:
                await page.set_content("<input id=x>")
                await page.fill("#x", "persistent", human_config=FAST)
                assert await page.input_value("#x") == "persistent"
        finally:
            await context.close()
    asyncio.run(run())


def test_async_sleep_yields_and_cancel_preserves_config():
    page, calls = mock_page(True)
    cfg = resolve_human_config(overrides=FAST)
    patch_page(page, cfg)
    async def run():
        task = asyncio.create_task(page.keyboard.type("ab", human_config={"typing_delay": 5000}))
        await asyncio.sleep(.01)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await page.keyboard.type("c")
    asyncio.run(run())
    assert [args[0] for name, args, _ in calls if name == "type"] == ["a", "c"]
    assert cfg.typing_delay == 0

"""Optional paced input for a limited Playwright page API.

Mouse/keyboard wrappers do not intercept Locator or ElementHandle methods.
Selector wrappers retain native Playwright actions and their option semantics.
"""
from __future__ import annotations

import inspect
import math
import random
import time
from dataclasses import dataclass, fields, replace
from typing import Literal, TypedDict

__all__ = ["HumanConfig", "HumanConfigOverrides", "resolve_human_config",
           "Humanizer", "patch_page", "human_move", "human_click",
           "human_type", "human_press", "human_scroll"]

HumanPreset = Literal["default", "careful"]


class HumanConfigOverrides(TypedDict, total=False):
    typing_delay: float
    typing_delay_spread: float
    typing_pause_chance: float
    mouse_wobble_max: float
    mouse_overshoot_chance: float
    mouse_min_steps: int
    mouse_steps_divisor: float
    click_aim_delay: float
    click_hold: float
    mistype_chance: float
    scroll_pause: float
    seed: int


@dataclass
class HumanConfig:
    """Supported tuning subset; timing values are milliseconds."""
    typing_delay: float = 70
    typing_delay_spread: float = 40
    typing_pause_chance: float = 0.1
    mouse_wobble_max: float = 1.5
    mouse_overshoot_chance: float = 0.35
    mouse_min_steps: int = 8
    mouse_steps_divisor: float = 12.0
    click_aim_delay: float = 80
    click_hold: float = 100
    mistype_chance: float = 0.02
    scroll_pause: float = 300
    seed: int | None = None


def _merge_config(base, overrides=None):
    values = dict(overrides or {})
    unknown = values.keys() - {f.name for f in fields(HumanConfig)}
    if unknown:
        raise ValueError("Unsupported human_config fields: " + ", ".join(sorted(unknown)))
    cfg = replace(base, **values)
    for field in fields(cfg):
        name, value = field.name, getattr(cfg, field.name)
        if name == "seed":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError("seed must be an integer or None")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
        if name.endswith("_chance") and value > 1:
            raise ValueError(f"{name} must be between 0 and 1")
    if cfg.mouse_steps_divisor == 0:
        raise ValueError("mouse_steps_divisor must be positive")
    if not isinstance(cfg.mouse_min_steps, int) or cfg.mouse_min_steps < 1:
        raise ValueError("mouse_min_steps must be a positive integer")
    return cfg


def resolve_human_config(preset: HumanPreset = "default",
                         overrides: HumanConfigOverrides | None = None) -> HumanConfig:
    if preset not in ("default", "careful"):
        raise ValueError(f"Unsupported human preset: {preset}")
    cfg = HumanConfig()
    if preset == "careful":
        cfg.typing_delay, cfg.typing_delay_spread = 130, 60
        cfg.mouse_steps_divisor, cfg.mouse_overshoot_chance = 8.0, 0.15
        cfg.click_aim_delay, cfg.click_hold = 200, 180
        cfg.mistype_chance = 0.04
    cfg = _merge_config(cfg, overrides)
    if cfg.seed is None:
        cfg.seed = random.randrange(1 << 31)
    return cfg


def _ease(t):
    return t * t * (3 - 2 * t)


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return tuple(u**3*p0[i] + 3*u*u*t*p1[i] + 3*u*t*t*p2[i] + t**3*p3[i]
                 for i in (0, 1))


class _Plan:
    """Input events shared by synchronous and asynchronous runners."""

    def __init__(self, cfg, pos=(0.0, 0.0), rng=None):
        self.cfg, self.pos = cfg, pos
        self.rng = rng if rng is not None else random.Random(cfg.seed)

    def pause(self, ms):
        return ("sleep", (self.rng.uniform(0.5, 1.5) * ms / 1000,), {})

    def move(self, x, y, duration=None, steps=None):
        start = self.pos
        dist = math.hypot(x - start[0], y - start[1])
        if steps is not None and (not isinstance(steps, int) or steps < 1):
            raise ValueError("steps must be a positive integer")
        if dist < 1:
            yield "move", (x, y), {}
            self.pos = (x, y)
            return
        count = steps or max(self.cfg.mouse_min_steps, int(dist / self.cfg.mouse_steps_divisor))
        duration = duration if duration is not None else 0.08 + min(0.62, dist / 2500)
        dx, dy = x - start[0], y - start[1]
        bow = self.rng.uniform(-0.3, 0.3)
        c1 = (start[0] + dx*.3 - dy*bow, start[1] + dy*.3 + dx*bow)
        c2 = (start[0] + dx*.7 - dy*bow, start[1] + dy*.7 + dx*bow)
        for i in range(1, count + 1):
            px, py = _bezier(start, c1, c2, (x, y), _ease(i/count))
            wobble = self.cfg.mouse_wobble_max
            yield "move", (px + self.rng.uniform(-wobble, wobble), py + self.rng.uniform(-wobble, wobble)), {}
            self.pos = (px, py)
            yield "sleep", (max(0, duration) / count,), {}
        if dist > 60 and self.rng.random() < self.cfg.mouse_overshoot_chance:
            yield "move", (x + self.rng.uniform(2, 6), y + self.rng.uniform(2, 6)), {}
            yield "sleep", (0.03,), {}
        yield "move", (x, y), {}
        self.pos = (x, y)

    def click(self, x=None, y=None, button="left", double=False, click_count=None, delay=None):
        if x is not None and y is not None:
            yield from self.move(x, y)
        yield self.pause(self.cfg.click_aim_delay)
        # Native click preserves click_count, event.detail and button semantics.
        yield "click", self.pos, {"button": button, "click_count": click_count if click_count is not None else (2 if double else 1),
                                  "delay": self.cfg.click_hold if delay is None else delay}

    def type(self, text, cps=None, mistype=None, delay=None):
        cfg = self.cfg
        for ch in text:
            if mistype is not False and ch.isalpha() and self.rng.random() < cfg.mistype_chance:
                yield "type", (self.rng.choice("qwertyuiopasdfghjklzxcvbnm"),), {"delay": 0}
                yield "sleep", (0.15,), {}
                yield "press", ("Backspace",), {}
            yield "type", (ch,), {"delay": 0}
            ms = delay if delay is not None else (1000/cps if cps else self.rng.uniform(cfg.typing_delay-cfg.typing_delay_spread, cfg.typing_delay+cfg.typing_delay_spread))
            yield "sleep", (max(0, ms)/1000,), {}
            if delay is None and self.rng.random() < cfg.typing_pause_chance:
                yield "sleep", (self.rng.uniform(0.4, 1),), {}

    def press(self, key, **options):
        yield self.pause(50)
        yield "press", (key,), options

    def scroll(self, total_dy, duration=None, dx=0):
        if not total_dy:
            yield "wheel", (dx, total_dy), {}
            return
        count = max(4, int(abs(total_dy)/120))
        duration = duration if duration is not None else min(2, abs(total_dy)/900 + .3)
        done_x = done_y = 0
        for i in range(1, count + 1):
            target_x, target_y = dx*_ease(i/count), total_dy*_ease(i/count)
            yield "wheel", (target_x-done_x, target_y-done_y), {}
            done_x, done_y = target_x, target_y
            yield "sleep", (max(0, duration)/count,), {}
            if self.rng.random() < .06:
                yield self.pause(self.cfg.scroll_pause)


class _Budget:
    def __init__(self, page, timeout):
        if timeout is None:
            settings = getattr(getattr(page, "_impl_obj", None), "_timeout_settings", None)
            timeout = settings.timeout() if settings else 30000
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        self.end = time.monotonic() + timeout/1000 if timeout else None

    def remaining(self):
        if self.end is None:
            return 0
        left = (self.end-time.monotonic())*1000
        if left <= 0:
            from playwright.sync_api import TimeoutError
            raise TimeoutError("Humanized action exceeded timeout")
        return left

    def sleep_time(self, seconds):
        remaining = self.remaining()
        return min(seconds, remaining/1000) if self.end is not None else seconds


def _run(events, raw, budget=None):
    for name, args, kwargs in events:
        if budget:
            budget.remaining()
        if name == "sleep":
            time.sleep(budget.sleep_time(args[0]) if budget else args[0])
        else:
            raw[name](*args, **kwargs)
    if budget:
        budget.remaining()


class Humanizer:
    """Synchronous coordinate/keyboard input helper."""

    def __init__(self, page, seed=None, cfg=None, raw_move=None, raw_type=None,
                 raw_press=None, raw_wheel=None):
        self.page = page
        self.cfg = _merge_config(cfg or HumanConfig(), {"seed": seed} if seed is not None else None)
        self._plan = _Plan(self.cfg)
        self.rng = self._plan.rng
        self._raw = {"move": raw_move or page.mouse.move,
                     "type": raw_type or page.keyboard.type,
                     "press": raw_press or page.keyboard.press,
                     "wheel": raw_wheel or page.mouse.wheel,
                     "click": getattr(page.mouse, "click", self._click_fallback)}

    def _click_fallback(self, x, y, button="left", click_count=1, delay=0):
        for _ in range(click_count):
            self.page.mouse.down(button=button)
            time.sleep(delay/1000)
            self.page.mouse.up(button=button)

    @property
    def pos(self):
        return self._plan.pos

    def _call(self, name, *args, human_config=None, **kwargs):
        plan = _Plan(_merge_config(self.cfg, human_config), self.pos,
                     None if human_config and "seed" in human_config else self.rng)
        try:
            _run(getattr(plan, name)(*args, **kwargs), self._raw)
        finally:
            self._plan.pos = plan.pos
        return self

    def move(self, x, y, duration=None, steps=None, *, human_config=None):
        return self._call("move", x, y, duration=duration, steps=steps,
                          human_config=human_config)

    def click(self, x=None, y=None, button="left", double=False, *, human_config=None):
        return self._call("click", x, y, button=button, double=double,
                          human_config=human_config)

    def type(self, text, cps=None, mistype=None, *, human_config=None):
        return self._call("type", text, cps=cps, mistype=mistype,
                          human_config=human_config)

    def press(self, key, *, human_config=None, **kwargs):
        return self._call("press", key, human_config=human_config, **kwargs)

    def scroll(self, total_dy, duration=None, *, human_config=None):
        return self._call("scroll", total_dy, duration=duration,
                          human_config=human_config)


def _validate_call(original, args, options):
    # Bind before any input so unsupported native options cannot be swallowed.
    inspect.signature(original).bind(*args, **options)


def _selector_options(plan, name, options):
    options = dict(options)
    if name == "click" and options.get("delay") is None:
        options["delay"] = plan.cfg.click_hold
    if name == "type" and options.get("delay") is None:
        options["delay"] = max(0, plan.rng.uniform(plan.cfg.typing_delay-plan.cfg.typing_delay_spread,
                                                  plan.cfg.typing_delay+plan.cfg.typing_delay_spread))
    return options


def _selector_action(page, name, original, args, options, plan, raw):
    _validate_call(original, args, options)
    if options.get("force") or options.get("trial"):
        return original(*args, **options)
    budget = _Budget(page, options.get("timeout"))
    selector = args[0] if args else options["selector"]
    element = page.wait_for_selector(selector, state="attached", strict=options.get("strict"), timeout=budget.remaining())
    try:
        for state in ("visible", "enabled", "stable") + (("editable",) if name == "fill" else ()):
            element.wait_for_element_state(state, timeout=budget.remaining())
        if name in ("click", "hover"):
            trial = dict(options, trial=True, timeout=budget.remaining())
            original(*args, **trial)
            box = element.bounding_box()
            if box:
                position = options.get("position")
                # Native position is relative to the padding box, not bounding_box.
                if position is None:
                    _run(plan.move(box["x"]+box["width"]/2, box["y"]+box["height"]/2), raw, budget)
        _run(iter([plan.pause(plan.cfg.click_aim_delay)]), raw, budget)
        native = _selector_options(plan, name, options)
        native["timeout"] = budget.remaining()
        return original(*args, **native)
    finally:
        element.dispose()


def _install(page, cfg, asynchronous=False):
    if getattr(page, "_chromix_humanized", False):
        return page
    cfg = _merge_config(cfg or HumanConfig())
    driver = Humanizer(page, cfg=cfg)
    raw = driver._raw
    if asynchronous:
        from .humanize_async import run_async, selector_action_async

    def wrap(original, name, selector=False):
        def prepare(args, options):
            options = dict(options)
            overrides = options.pop("human_config", None)
            plan = _Plan(_merge_config(cfg, overrides), driver.pos,
                         None if overrides and "seed" in overrides else driver.rng)
            if not selector:
                _validate_call(original, args, options)
            return plan, options

        def events(plan, args, options):
            if name == "dblclick":
                return plan.click(*args, double=True, **options)
            if name == "wheel":
                bound = inspect.signature(original).bind(*args, **options)
                values = list(bound.arguments.values())
                return plan.scroll(values[1], dx=values[0])
            return getattr(plan, name)(*args, **options)

        if asynchronous:
            async def call(*args, **kwargs):
                plan, options = prepare(args, kwargs)
                try:
                    if selector:
                        return await selector_action_async(page, name, original, args, options, plan, raw)
                    await run_async(events(plan, args, options), raw)
                finally:
                    driver._plan.pos = plan.pos
        else:
            def call(*args, **kwargs):
                plan, options = prepare(args, kwargs)
                try:
                    if selector:
                        return _selector_action(page, name, original, args, options, plan, raw)
                    _run(events(plan, args, options), raw)
                finally:
                    driver._plan.pos = plan.pos
        return call

    for target, names in ((page.mouse, ("move", "click", "dblclick", "wheel")),
                          (page.keyboard, ("type", "press"))):
        for name in names:
            original = getattr(target, name, None)
            if original is None and name == "click":
                original = driver._click_fallback
            if original is not None:
                setattr(target, name, wrap(original, name))
    for name in ("click", "hover", "type", "fill"):
        original = getattr(page, name, None)
        if original is not None:
            setattr(page, name, wrap(original, name, selector=True))
    page._chromix_humanized = True
    return page


def patch_page(page, cfg: HumanConfig | None = None):
    """Patch mouse/keyboard and page.click/hover/type/fill; auto-detect async pages."""
    return _install(page, cfg, asynchronous=inspect.iscoroutinefunction(page.mouse.move))


def human_move(page, x, y, **kw):
    return Humanizer(page).move(x, y, **kw)


def human_click(page, x=None, y=None, **kw):
    return Humanizer(page).click(x, y, **kw)


def human_type(page, keyboard_or_text, text=None, **kw):
    return Humanizer(page).type(keyboard_or_text if text is None else text, **kw)


def human_press(page, key, **kw):
    return Humanizer(page).press(key, **kw)


def human_scroll(page, dy, **kw):
    return Humanizer(page).scroll(dy, **kw)

"""Asynchronous runners for the shared paced-input plans."""
from __future__ import annotations

import asyncio

from .humanize import _Budget, _install, _selector_options, _validate_call


def patch_page(page, cfg=None):
    """Install the same limited input wrappers on an async Playwright page."""
    return _install(page, cfg, asynchronous=True)


async def run_async(events, raw, budget=None):
    for name, args, kwargs in events:
        if budget:
            budget.remaining()
        if name == "sleep":
            await asyncio.sleep(budget.sleep_time(args[0]) if budget else args[0])
        else:
            await raw[name](*args, **kwargs)
    if budget:
        budget.remaining()


async def selector_action_async(page, name, original, args, options, plan, raw):
    _validate_call(original, args, options)
    if options.get("force") or options.get("trial"):
        return await original(*args, **options)
    budget = _Budget(page, options.get("timeout"))
    selector = args[0] if args else options["selector"]
    element = await page.wait_for_selector(selector, state="attached", strict=options.get("strict"), timeout=budget.remaining())
    try:
        for state in ("visible", "enabled", "stable") + (("editable",) if name == "fill" else ()):
            await element.wait_for_element_state(state, timeout=budget.remaining())
        if name in ("click", "hover"):
            trial = dict(options, trial=True, timeout=budget.remaining())
            await original(*args, **trial)
            box = await element.bounding_box()
            if box and options.get("position") is None:
                await run_async(plan.move(box["x"]+box["width"]/2, box["y"]+box["height"]/2), raw, budget)
        await run_async(iter([plan.pause(plan.cfg.click_aim_delay)]), raw, budget)
        native = _selector_options(plan, name, options)
        native["timeout"] = budget.remaining()
        return await original(*args, **native)
    finally:
        await element.dispose()

import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { humanizePage, humanizeBrowser, resolveHumanConfig } from "../index.js";

const fast = { seed: 7, aimDelay: 0, hold: 0, mistype: 0, typingDelay: 0,
  typingSpread: 0, pauseChance: 0, wobble: 0, overshoot: 0 };
function fixture() {
  const calls = [];
  const record = name => async (...args) => { calls.push([name, ...args]); };
  const page = {
    mouse: Object.fromEntries(["move", "click", "dblclick", "wheel", "down", "up"].map(name => [name, record(name)])),
    keyboard: { type: record("type"), press: record("press") },
    click: record("page.click"), dblclick: record("page.dblclick"),
    $: async () => ({ boundingBox: async () => ({ x: 0, y: 0, width: 0, height: 0 }), dispose: record("dispose") }),
  };
  return { page, calls };
}

test("per-call humanConfig is merged without mutating preset, page or caller config", async () => {
  const before = resolveHumanConfig();
  const cfg = Object.freeze({ ...fast, hold: 20 });
  const { page, calls } = fixture();
  humanizePage(page, cfg);
  const options = Object.freeze({ humanConfig: Object.freeze({ hold: 0 }) });
  await page.mouse.click(0, 0, options);
  await page.mouse.click(0, 0);
  const clicks = calls.filter(row => row[0] === "click");
  assert.equal(clicks[0][3].delay, 0);
  assert.ok(clicks[1][3].delay >= 10);
  assert.equal(cfg.hold, 20);
  assert.deepEqual(resolveHumanConfig(), before);
  assert.equal(resolveHumanConfig("careful").hold, 180);
});

test("overlapping calls retain separate configs after awaiting actionability", async () => {
  const { page, calls } = fixture();
  let release;
  const original = page.click;
  page.click = async (selector, opts) => {
    if (selector === "#slow" && opts.trial) await new Promise(resolve => { release = resolve; });
    return original(selector, opts);
  };
  humanizePage(page, { ...fast, hold: 20 });
  const slow = page.click("#slow", { humanConfig: { hold: 0 } });
  await page.click("#fast");
  release();
  await slow;
  const final = calls.filter(row => row[0] === "page.click" && !row[2].trial);
  assert.ok(final[0][2].delay >= 10);
  assert.equal(final[1][2].delay, 0);
});

test("Playwright click and dblclick use native trial then final dispatch", async () => {
  const { page, calls } = fixture();
  humanizePage(page, fast);
  for (const name of ["click", "dblclick"]) {
    await page[name]("#target", { timeout: 500, strict: true, humanConfig: { hold: 0 } });
    const rows = calls.filter(row => row[0] === `page.${name}`);
    assert.equal(rows.length, 2);
    assert.equal(rows[0][2].trial, true);
    assert.equal(rows[1][2].trial, undefined);
    assert.equal(rows[1][2].strict, true);
    assert.ok(rows[1][2].timeout <= rows[0][2].timeout);
    assert.equal(rows[1][2].humanConfig, undefined);
  }
});

test("force, trial and other native options bypass extra actions without being lost", async () => {
  for (const options of [{ force: true }, { trial: true }, { modifiers: ["Shift"] },
    { position: { x: 1, y: 2 } }, { button: "right" }, { delay: 0 },
    { clickCount: 3 }, { noWaitAfter: true }, { futureOption: "native" }]) {
    const { page, calls } = fixture();
    humanizePage(page, fast);
    await page.click("#target", { ...options, humanConfig: { hold: 0 } });
    assert.deepEqual(calls, [["page.click", "#target", options]]);
  }
});

test("mouse and keyboard native options are forwarded exactly", async () => {
  const { page, calls } = fixture();
  humanizePage(page, fast);
  await page.mouse.move(10, 20, { steps: 4, humanConfig: { wobble: 0 } });
  await page.mouse.click(10, 20, { clickCount: 3, delay: 12, button: "right" });
  await page.mouse.dblclick(10, 20, { delay: 13 });
  await page.keyboard.type("abc", { delay: 9, humanConfig: { typingDelay: 0 } });
  await page.keyboard.press("Shift+A", { delay: 8 });
  assert.deepEqual(calls, [
    ["move", 10, 20, { steps: 4 }],
    ["click", 10, 20, { clickCount: 3, delay: 12, button: "right" }],
    ["dblclick", 10, 20, { delay: 13 }], ["type", "abc", { delay: 9 }],
    ["press", "Shift+A", { delay: 8 }],
  ]);
});

test("timeouts during pacing do not dispatch a click later", async () => {
  const { page, calls } = fixture();
  humanizePage(page, { ...fast, aimDelay: 200 });
  await assert.rejects(page.click("#target", { timeout: 20 }), { name: "TimeoutError" });
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(calls.filter(row => row[0] === "page.click" && !row[2].trial).length, 0);
});

test("invalid or unsupported human config fails explicitly", async () => {
  for (const config of [null, [], { typo: 0 }, { typing_delay: 0 }, { mistype: 2 },
    { minSteps: 0 }, { minSteps: 1.5 }, { stepsDivisor: 0 }, { hold: NaN }, { hold: -1 }])
    assert.throws(() => resolveHumanConfig("default", config), TypeError);
  assert.throws(() => resolveHumanConfig("missing"), /humanPreset/);
  const { page, calls } = fixture();
  humanizePage(page, fast);
  await assert.rejects(page.click("#target", { humanConfig: { unsupported: 1 } }), /Unsupported/);
  await assert.rejects(page.mouse.wheel(0, 1, { unsupported: true }), /Unsupported/);
  await assert.rejects(page.click("#target", { timeout: -1 }), /timeout/);
  assert.equal(calls.length, 0);
});

test("page/browser/context wrapping is idempotent and covers existing and new Playwright pages", async () => {
  const existing = fixture().page;
  const ctx = Object.assign(new EventEmitter(), { pages: () => [existing], newPage: async () => fixture().page });
  const browser = { contexts: () => [ctx], newPage: async () => fixture().page, newContext: async () => ctx };
  await humanizeBrowser(browser, fast);
  const click = existing.click, newPage = browser.newPage;
  humanizePage(existing, fast);
  await humanizeBrowser(browser, fast);
  assert.equal(existing.click, click);
  assert.equal(browser.newPage, newPage);
  assert.equal(ctx.listenerCount("page"), 1);
  const popup = fixture().page, original = popup.click;
  ctx.emit("page", popup);
  assert.notEqual(popup.click, original);
  for (const page of [await browser.newPage(), await (await browser.newContext()).newPage()])
    await assert.rejects(page.click("x", { humanConfig: { unsupported: 1 } }), /Unsupported/);
});

test("wheel totals preserve fractional and negative deltas", async () => {
  for (const dy of [0.5, -0.5, 12.25, -12.25]) {
    const { page, calls } = fixture();
    humanizePage(page, fast);
    await page.mouse.wheel(0.25, dy, { humanConfig: { scrollPause: 0 } });
    const wheels = calls.filter(row => row[0] === "wheel");
    assert.equal(wheels.reduce((sum, row) => sum + row[1], 0), 0.25);
    assert.equal(wheels.reduce((sum, row) => sum + row[2], 0), dy);
  }
});

test("native Puppeteer mouse internals are not humanized twice", async () => {
  const { page, calls } = fixture();
  delete page.mouse.dblclick;
  page.mouse.click = async (x, y, opts) => {
    await page.mouse.move(x, y);
    calls.push(["native.click", opts]);
  };
  humanizePage(page, fast, { driver: "puppeteer" });
  await page.mouse.click(10, 20, { count: 3, delay: 12 });
  assert.deepEqual(calls, [["move", 10, 20, {}], ["native.click", { count: 3, delay: 12 }]]);
  calls.length = 0;
  await page.mouse.dblclick(10, 20, { delay: 12 });
  assert.deepEqual(calls.at(-1), ["native.click", { delay: 12, count: 2 }]);
});

test("Puppeteer browser helper uses native context APIs and preserves wheel/click options", async () => {
  const { page, calls } = fixture();
  page.browserContext = () => ctx;
  const nativeClick = page.click;
  const ctx = { pages: async () => [page], newPage: async () => page };
  const browser = { browserContexts: () => [ctx], newPage: async () => page, createBrowserContext: async () => ctx };
  await humanizeBrowser(browser, fast);
  assert.equal(page.click, nativeClick);
  assert.equal(await (await browser.createBrowserContext()).newPage(), page);
  await page.mouse.wheel({ deltaX: 5, deltaY: 12, humanConfig: { scrollPause: 0 } });
  const wheels = calls.filter(row => row[0] === "wheel");
  assert.equal(wheels.reduce((total, row) => total + row[1].deltaX, 0), 5);
  assert.equal(wheels.reduce((total, row) => total + row[1].deltaY, 0), 12);
  await page.mouse.click(0, 0, { count: 2, delay: 10 });
  assert.deepEqual(calls.at(-1), ["click", 0, 0, { count: 2, delay: 10 }]);
});

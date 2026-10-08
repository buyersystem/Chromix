import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, writeFileSync, mkdirSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const chrome = process.env.CHROMIX_TEST_CHROME;
const playwright = process.env.CHROMIX_TEST_PLAYWRIGHT;
const fast = { seed: 11, aimDelay: 0, hold: 0, mistype: 0, typingDelay: 0,
  typingSpread: 0, pauseChance: 0, wobble: 0, overshoot: 0 };

test("humanized SDK on local Chrome and local pages", { skip: !chrome || !playwright, timeout: 60000 }, async t => {
  const root = mkdtempSync(join(tmpdir(), "chromix-humanize-local-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = fileURLToPath(new URL("../", import.meta.url));
  for (const name of readdirSync(source).filter(name => name.endsWith(".js")))
    writeFileSync(join(root, name), readFileSync(join(source, name)));
  writeFileSync(join(root, "package.json"), JSON.stringify({ type: "module" }));
  mkdirSync(join(root, "node_modules"));
  symlinkSync(playwright, join(root, "node_modules", "playwright-core"), "dir");
  symlinkSync(join(source, "node_modules", "yauzl"), join(root, "node_modules", "yauzl"), "dir");
  const api = await import(pathToFileURL(join(root, "index.js")).href);
  const options = { humanize: true, humanConfig: fast, stealthArgs: false,
    startMaximized: false, launchOptions: { executablePath: chrome, headless: true } };
  const browser = await api.launch(options);
  t.after(() => browser.close());
  t.diagnostic(`Local Chrome ${browser.version()}; no external page or detection service`);
  const page = await browser.newPage();

  await t.test("waits for visible, enabled and stable then dispatches one click", async () => {
    await page.setContent(`<button id="target" disabled style="display:none;position:relative">target</button>
      <script>
        window.clicks = []; window.started = performance.now();
        var target = document.querySelector('#target');
        target.onclick = () => clicks.push(performance.now() - started);
        setTimeout(() => target.style.display = 'block', 80);
        setTimeout(() => target.disabled = false, 160);
        target.animate([{left:'0px'}, {left:'140px'}], {duration:320, fill:'forwards'});
      </script>`);
    await page.click("#target", { timeout: 2500, humanConfig: { hold: 0 } });
    const clicks = await page.evaluate(() => window.clicks);
    assert.equal(clicks.length, 1);
    assert.ok(clicks[0] >= 300, JSON.stringify(clicks));
  });

  await t.test("hidden, disabled, moving, occluded and absent targets time out without later clicks", async () => {
    for (const mode of ["hidden", "disabled", "moving", "occluded", "absent"]) {
      await page.setContent(`<button id="target" style="position:absolute;left:20px;top:20px">target</button>
        <script>window.clicks=0; document.querySelector('#target').onclick=()=>clicks++;</script>`);
      await page.evaluate(mode => {
        const target = document.querySelector("#target");
        if (mode === "hidden") target.style.display = "none";
        if (mode === "disabled") target.disabled = true;
        if (mode === "moving") target.animate([{left:"20px"}, {left:"300px"}], {duration:400, iterations:Infinity});
        if (mode === "occluded") {
          const cover = document.createElement("div");
          cover.style.cssText = "position:fixed;inset:0;background:white";
          document.body.append(cover);
        }
        if (mode === "absent") target.remove();
      }, mode);
      await assert.rejects(page.click("#target", { timeout: 130 }), { name: "TimeoutError" }, mode);
      await page.waitForTimeout(160);
      assert.equal(await page.evaluate(() => window.clicks), 0, mode);
    }
  });

  await t.test("trial, force, modifiers, position and double-click keep native semantics", async () => {
    await page.setContent(`<button id="target">target</button><script>
      window.events=[]; var target=document.querySelector('#target');
      for(const type of ['click','dblclick']) target.addEventListener(type,e=>events.push({type,shift:e.shiftKey,detail:e.detail}));
      </script>`);
    await page.click("#target", { trial: true, timeout: 1000, humanConfig: { hold: 0 } });
    assert.deepEqual(await page.evaluate(() => window.events), []);
    await page.click("#target", { modifiers: ["Shift"], position: { x: 5, y: 5 } });
    assert.equal((await page.evaluate(() => window.events))[0].shift, true);
    await page.dblclick("#target", { timeout: 2000 });
    assert.ok((await page.evaluate(() => window.events)).some(event => event.type === "dblclick" && event.detail === 2));
    await page.evaluate(() => {
      window.events = [];
      const cover = document.createElement("div");
      cover.style.cssText = "position:fixed;inset:0;background:white";
      cover.id = "cover";
      cover.onclick = () => window.events.push({ type: "cover" });
      document.body.append(cover);
    });
    await page.click("#target", { force: true, timeout: 1000 });
    assert.deepEqual(await page.evaluate(() => window.events), [{ type: "cover" }]);
  });

  await t.test("typing, mouse counts, timeout during pacing and selector replacement", async () => {
    await page.setContent(`<input id="input"><button id="target">target</button><script>
      window.clicks=0; document.addEventListener('click', e=>{if(e.target.id==='target')clicks++;});
      </script>`);
    await page.locator("#input").focus();
    await page.keyboard.type("abc", { humanConfig: { mistype: 0, typingDelay: 0 } });
    await page.keyboard.type("d", { delay: 1 });
    assert.equal(await page.inputValue("#input"), "abcd");
    await assert.rejects(page.click("#target", { timeout: 100, humanConfig: { aimDelay: 1000 } }), { name: "TimeoutError" });
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(() => window.clicks), 0);
    await page.evaluate(() => setTimeout(() => {
      const target = document.querySelector("#target");
      target.replaceWith(target.cloneNode(true));
    }, 80));
    await page.click("#target", { timeout: 2000, humanConfig: { aimDelay: 200 } });
    assert.equal(await page.evaluate(() => window.clicks), 1);
    const box = await page.locator("#target").boundingBox();
    await page.mouse.click(box.x + 5, box.y + 5, { clickCount: 2, delay: 5 });
    assert.equal(await page.evaluate(() => window.clicks), 3);
  });

  await t.test("existing/new contexts and popup pages are wrapped exactly once", async () => {
    const click = page.click;
    await api.humanizeBrowser(browser, fast);
    assert.equal(page.click, click);
    const context = await browser.newContext();
    const other = await context.newPage();
    await assert.rejects(other.click("#x", { humanConfig: { unsupported: 1 } }), /Unsupported/);
    await page.setContent("<button onclick=\"window.open('about:blank')\">popup</button>");
    const pending = page.waitForEvent("popup");
    await page.click("button", { timeout: 2000 });
    const popup = await pending;
    await assert.rejects(popup.click("#x", { humanConfig: { unsupported: 1 } }), /Unsupported/);
    await context.close();
  });

  await t.test("launchContext and persistent initial pages inherit human config", async () => {
    for (const persistent of [false, true]) {
      const context = persistent
        ? await api.launchPersistentContext({ ...options, userDataDir: join(root, "profile") })
        : await api.launchContext(options);
      try {
        const target = context.pages()[0] ?? await context.newPage();
        await target.setContent("<button onclick='window.done=true'>ok</button>");
        await target.click("button", { timeout: 2000 });
        assert.equal(await target.evaluate(() => window.done), true);
        await assert.rejects(target.click("button", { humanConfig: { unsupported: 1 } }), /Unsupported/);
      } finally { await context.close(); }
    }
  });
});

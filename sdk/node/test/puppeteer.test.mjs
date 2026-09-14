import { after, test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = mkdtempSync(join(tmpdir(), "chromix-puppeteer-test-"));
// Exercise a copy with an isolated driver, never overwrite installed packages.
const source = fileURLToPath(new URL("../", import.meta.url));
for (const name of readdirSync(source).filter(name => name.endsWith(".js")))
  writeFileSync(join(root, name), readFileSync(join(source, name)));
writeFileSync(join(root, "package.json"), JSON.stringify({ type: "module" }));
const zipStub = join(root, "node_modules", "yauzl");
mkdirSync(zipStub, { recursive: true });
writeFileSync(join(zipStub, "package.json"), JSON.stringify({ type: "module", exports: "./index.js" }));
writeFileSync(join(zipStub, "index.js"), "export default {open(){throw Error('Unexpected binary archive access')}};");
const fixture = join(root, "node_modules", "puppeteer-core");
mkdirSync(fixture, { recursive: true });
writeFileSync(join(fixture, "package.json"), JSON.stringify({ type: "module", exports: "./index.js" }));
writeFileSync(join(fixture, "index.js"), `
export const state = { calls: [], closes: 0, disconnects: 0, contextCloses: 0, wheels: [], failPage: false, failContext: false, failClose: false };
function page() { return state.failPage ? {} : {mouse: {wheel: async opts => state.wheels.push(opts),
  move: async () => {}, down: async () => {}, up: async () => {}}, keyboard: {type: async () => {}, press: async () => {}}}; }
function context() { return { pages: async () => state.failPage ? [{}] : [], newPage: async () => page(),
  close: async () => { state.contextCloses++; if(state.failClose) throw Error('context close failure'); } }; }
function browser() { const ctx=context();return {
  close: async () => {state.closes++;}, disconnect: async () => {state.disconnects++;}, defaultBrowserContext: () => ctx,
  browserContexts: () => [ctx], newPage: async () => page(),
  createBrowserContext: async () => { if(state.failContext) throw Error('context creation failure'); return context(); }
}; }
export default {
  launch: async options => { state.calls.push(options); return browser(); },
  connect: async options => {state.calls.push(options);return browser();}
};`);
const { state } = await import(pathToFileURL(join(fixture, "index.js")).href);
const api = await import(pathToFileURL(join(root, "puppeteer.js")).href);
after(() => rmSync(root, { recursive: true, force: true }));

function offline(t) {
  const saved = { ...process.env };
  process.env.CLOAKBROWSER_BINARY_PATH = fileURLToPath(new URL("../index.js", import.meta.url));
  process.env.CLOAKBROWSER_WIDEVINE = "0";
  t.after(() => {
    for (const key of ["CLOAKBROWSER_BINARY_PATH", "CLOAKBROWSER_WIDEVINE"])
      if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
  });
  t.mock.method(globalThis, "fetch", async () => { throw Error("Unexpected network request"); });
  Object.assign(state, { calls: [], closes: 0, disconnects: 0, contextCloses: 0, wheels: [], failPage: false, failContext: false, failClose: false });
}

test("Puppeteer export is packaged and default viewport stays native", async t => {
  offline(t);
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url)));
  assert.equal(pkg.exports["./puppeteer"], "./puppeteer.js");
  assert.ok(pkg.files.includes("puppeteer.js") && pkg.files.includes("_profile.js"));
  const browser = await api.launch({ args: ["--fingerprint=42"] });
  assert.equal(state.calls[0].defaultViewport, null);
  assert.ok(state.calls[0].args.includes("--fingerprint=42"));
  assert.deepEqual(state.calls[0].ignoreDefaultArgs, ["--enable-automation"]);
  assert.equal("proxy" in state.calls[0], false);
  await browser.close();
});

test("Puppeteer maps a proxy to native switches without mutating options", async t => {
  offline(t);
  const options = { proxy: { server: "socks5://localhost:1080", bypass: "<-loopback>" },
    timezone: "UTC", viewport: { width: 1000, height: 700 }, args: ["--fingerprint=42"] };
  const before = structuredClone(options);
  const result = await api.buildLaunchOptions(options);
  assert.ok(result.args.includes("--proxy-server=socks5://localhost:1080"));
  assert.ok(result.args.includes("--force-webrtc-ip-handling-policy=disable_non_proxied_udp"));
  assert.ok(result.args.includes("--proxy-bypass-list=<-loopback>"));
  assert.ok(result.args.includes("--fingerprint-timezone=UTC"));
  assert.deepEqual(result.defaultViewport, { width: 1000, height: 700 });
  assert.deepEqual(options, before);
});

test("disabling fingerprint defaults does not discard top-level launch features", async t => {
  offline(t);
  const result = await api.buildLaunchOptions({ stealthArgs: false, args: ["--fingerprint=off"],
    extensionPaths: ["/owned-extension-fixture"], startMaximized: true });
  assert.ok(result.args.some(arg => arg.startsWith("--load-extension=")));
  assert.ok(result.args.some(arg => arg.startsWith("--disable-extensions-except=")));
  assert.ok(result.args.includes("--start-maximized"));
  assert.ok(result.args.includes("--fingerprint=off"));
});

test("persistent adapter reuses the same profile identity", async t => {
  offline(t);
  const userDataDir = join(root, "persistent");
  const ctx = await api.launchPersistentContext({ userDataDir });
  await Promise.all([ctx.close(), ctx.close()]);
  assert.equal(state.closes, 1);
  assert.equal(state.contextCloses, 0);
  const seed = readFileSync(join(userDataDir, ".chromix-fingerprint-seed"), "utf8").trim();
  const second = await api.launchPersistentContext({ userDataDir });
  await second.close();
  assert.ok(state.calls.every(call => call.args.includes(`--fingerprint=${seed}`)));
  assert.ok(state.calls.every(call => call.userDataDir === userDataDir));
});

test("owned context closes its browser even if context close fails", async t => {
  offline(t);
  const ctx = await api.launchContext();
  state.failClose = true;
  await assert.rejects(ctx.close(), /context close failure/);
  await assert.rejects(ctx.close(), /context close failure/);
  assert.equal(state.closes, 1);
  assert.equal(state.contextCloses, 1);
});

test("context creation failure releases the owned browser", async t => {
  offline(t);
  state.failContext = true;
  await assert.rejects(api.launchContext(), /context creation failure/);
  assert.equal(state.closes, 1);
});

for (const options of [
  { devicePool: {} }, { contextOptions: { locale: "en-US" } },
  { proxy: "http://user:pass@localhost:1080" },
  { proxy: "http://localhost:8080", args: ["--proxy-server=http://elsewhere:8080"] },
  { args: ["--user-data-dir=profile"] }, { launchOptions: { chromiumSandbox: true } },
  { userAgent: "custom" }, { viewport: null, defaultViewport: null },
  { launchOptions: { userAgent: "custom" } }, { launchOptions: { colorScheme: "dark" } },
  { userDataDir: 42 }, { launchOptions: { env: { chromix_socks5_auth: "raw" } } },
]) {
  test(`invalid Puppeteer configuration fails before launch: ${JSON.stringify(options)}`, async t => {
    offline(t);
    await assert.rejects(api.launch(options));
    assert.equal(state.calls.length, 0);
  });
}

test("connect preserves connection options and has no launch identity side effects", async t => {
  offline(t);
  await api.connect({ browserWSEndpoint: "ws://127.0.0.1:9222/devtools/browser/test" });
  assert.deepEqual(state.calls[0], { defaultViewport: null, browserWSEndpoint: "ws://127.0.0.1:9222/devtools/browser/test" });
});

for (const key of ["timezoneId", "geoip", "fontsDir", "executablePath", "contextOptions"])
  test(`connect rejects unapplied launch identity: ${key}`, async t => {
    offline(t);
    await assert.rejects(api.connect({ [key]: "fixture" }), /launch-scoped option/);
    assert.equal(state.calls.length, 0);
  });

test("Puppeteer humanized wheel preserves object API on launch and connect", async t => {
  offline(t);
  for (const method of ["launch", "connect"]) {
    const browser = await api[method]({ humanize: true });
    const page = await browser.newPage();
    await page.mouse.wheel({ deltaX: 5, deltaY: 10 });
    assert.equal(state.wheels.reduce((total, row) => total + row.deltaX, 0), 5);
    assert.equal(state.wheels.reduce((total, row) => total + row.deltaY, 0), 10);
    state.wheels = [];
    if (method === "connect") {
      assert.equal(Object.hasOwn(state.calls.at(-1), "humanize"), false);
      await browser.disconnect();
    } else await browser.close();
  }
});

test("failed connected-page preparation disconnects, never closes caller browser", async t => {
  offline(t);
  state.failPage = true;
  await assert.rejects(api.connect({ humanize: true }));
  assert.equal(state.disconnects, 1);
  assert.equal(state.closes, 0);
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { nativeSocksConfig, nativeSocksEnv, SOCKS_AUTH_ENV } from "../_socks_auth.js";
import { fontLaunchEnv } from "../_fonts.js";
import { buildLaunchOptions } from "../index.js";
import { fileURLToPath } from "node:url";

test("native SOCKS auth uses a bound environment, never Playwright auth fields", () => {
  const proxy = { server: "socks5://[::1]:1080", username: "用户", password: "päss", bypass: "<-loopback>" };
  const original = structuredClone(proxy);
  const result = nativeSocksConfig(proxy);
  assert.deepEqual(result.proxy, { server: proxy.server, bypass: proxy.bypass });
  assert.deepEqual(JSON.parse(result.auth), { version: 1, host: "::1", port: 1080, username: "用户", password: "päss" });
  assert.deepEqual(proxy, original);
  assert.equal(nativeSocksEnv({ KEEP: "yes" }, result.auth).KEEP, "yes");
});

test("ambient auth is cleared on unrelated launches without mutating process.env", t => {
  const before = process.env[SOCKS_AUTH_ENV];
  t.after(() => { if (before === undefined) delete process.env[SOCKS_AUTH_ENV]; else process.env[SOCKS_AUTH_ENV] = before; });
  process.env[SOCKS_AUTH_ENV] = "other-profile-secret";
  assert.equal(Object.hasOwn(nativeSocksEnv(undefined, null), SOCKS_AUTH_ENV), false);
  assert.equal(process.env[SOCKS_AUTH_ENV], "other-profile-secret");
  assert.throws(() => nativeSocksEnv({ [SOCKS_AUTH_ENV]: "raw" }, null));
  assert.throws(() => nativeSocksEnv({ [SOCKS_AUTH_ENV.toLowerCase()]: "raw" }, null));
  const withFonts = fontLaunchEnv("uninstalled-fixture-browser", { KEEP: "yes" });
  assert.equal(Object.hasOwn(withFonts, SOCKS_AUTH_ENV), false);
  assert.equal(withFonts.KEEP, "yes");
});

test("launch builder binds native auth after font env merge and rejects raw aliases", async t => {
  const before = process.env[SOCKS_AUTH_ENV];
  t.after(() => { if (before === undefined) delete process.env[SOCKS_AUTH_ENV]; else process.env[SOCKS_AUTH_ENV] = before; });
  process.env[SOCKS_AUTH_ENV] = "other-profile-secret";
  const launchOptions = { executablePath: fileURLToPath(new URL("../index.js", import.meta.url)), env: { KEEP: "yes" } };
  const unrelated = await buildLaunchOptions({ launchOptions, stealthArgs: false });
  assert.equal(Object.hasOwn(unrelated.env, SOCKS_AUTH_ENV), false);
  const bound = await buildLaunchOptions({ launchOptions, proxy: "socks5://user:pass@localhost:1080" });
  assert.equal(JSON.parse(bound.env[SOCKS_AUTH_ENV]).username, "user");
  assert.equal(bound.proxy.username, undefined);
  assert.equal(bound.env.KEEP, "yes");
  await assert.rejects(buildLaunchOptions({ launchOptions: { ...launchOptions,
    env: { [SOCKS_AUTH_ENV.toLowerCase()]: "raw" } } }), /Use proxy/);
  assert.deepEqual(launchOptions.env, { KEEP: "yes" });
});

for (const [username, password] of [["", "p"], ["u", ""], ["u".repeat(256), "p"], ["u", "字".repeat(86)]])
  test(`RFC 1929 byte limits: ${username.length}/${password.length}`, () => {
    assert.throws(() => nativeSocksConfig({ server: "socks5://localhost:1080", username, password }));
  });

for (const args of [["--no-proxy-server"], ["--proxy-server=socks5://other:1080"], ["--proxy-pac-url=http://local/pac"]])
  test(`native SOCKS route conflict: ${args}`, () => {
    assert.throws(() => nativeSocksConfig("socks5://u:p@localhost:1080", args));
  });

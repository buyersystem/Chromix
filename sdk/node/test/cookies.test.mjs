import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { encryptCookies, decryptCookies, normalizeCookies, exportCookies, importCookies } from "../cookies.js";

const passphrase = "test-only passphrase 字符";
const cookie = { name: "session", value: "test-value", domain: "localhost", path: "/", secure: false, httpOnly: true };

test("authenticated encryption roundtrip, fresh nonces, and tamper rejection", async () => {
  const first = await encryptCookies([cookie], passphrase);
  const second = await encryptCookies([cookie], passphrase);
  assert.notDeepEqual(first, second);
  assert.equal(first.includes(Buffer.from(cookie.value)), false);
  assert.deepEqual(await decryptCookies(first, passphrase), [cookie]);
  await assert.rejects(decryptCookies(first, "incorrect passphrase"), /Cannot decrypt/);
  for (const offset of [0, 17, 33, 50, first.length - 1]) {
    const damaged = Buffer.from(first); damaged[offset] ^= 1;
    await assert.rejects(decryptCookies(damaged, passphrase));
  }
  await assert.rejects(decryptCookies(first.subarray(0, -1), passphrase));
});

for (const invalid of [
  { domain: "host/name" }, { name: "a;b" }, { value: "a\nb" }, { expires: NaN }, { expires: 0 },
  { secure: 1 }, { partitionKeyOpaque: true }, { sourcePort: 65536 }, { path: "relative" },
  { name: "__Host-test", domain: ".example.test", secure: true }, { sameSite: "None" },
  { partitionKey: { topLevelSite: "https://example.test", hasCrossSiteAncestor: true } },
]) test(`Cookie preflight rejects invalid semantics: ${JSON.stringify(invalid)}`, () => {
  assert.throws(() => normalizeCookies([{ ...cookie, ...invalid }]));
});

test("partition keys and session/domain distinctions survive serialization", async () => {
  const cookies = [{ ...cookie, domain: ".example.test", secure: true, sameSite: "None", expires: -1,
    priority: "High", sourceScheme: "Secure", sourcePort: 443,
    partitionKey: { topLevelSite: "https://example.test", hasCrossSiteAncestor: true } }];
  assert.deepEqual(await decryptCookies(await encryptCookies(cookies, passphrase), passphrase), normalizeCookies(cookies));
  assert.throws(() => normalizeCookies([cookie, cookie]), /Duplicate/);
});

for (const [site, valid] of [
  ["https://example.test", true], ["https://example.test.", true], ["https://[::1]", true],
  ["http://127.0.0.1", true], ["https://example.test:444", true], ["https://under_score.test", true],
  ["https://EXAMPLE.test", false], ["https://example.test:443", false], ["https://example.test:0444", false],
  ["https://example.test:", false], ["https://example.test/", false], ["https://127.1", false],
  ["https://0x7f000001", false], ["https://[0:0:0:0:0:0:0:1]", false], ["https://[fe80::1%lo]", false],
]) test(`canonical Cookie partition origin: ${site}`, () => {
  const value = { ...cookie, secure: true, partitionKey: { topLevelSite: site, hasCrossSiteAncestor: true } };
  if (valid) assert.deepEqual(normalizeCookies([value]), [value]);
  else assert.throws(() => normalizeCookies([value]));
});

function fakeContext(initial = [], failWrite = false) {
  let stored = structuredClone(initial);
  const state = { calls: [], detached: 0, closed: 0, pages: 0 };
  const session = { detach: async () => { state.detached++; }, send: async (method, params) => {
    state.calls.push([method, params]);
    if (method === "Target.getTargetInfo") return { targetInfo: { type: "page", browserContextId: "owned" } };
    assert.equal(Object.hasOwn(params, "browserContextId"), false);
    if (method === "Network.getAllCookies") return { cookies: stored };
    if (method === "Network.setCookies") {
      if (failWrite) throw Error("write failed");
      stored = params.cookies.map(value => {
        const result = { ...value };
        if (result.url) { result.domain = new URL(result.url).hostname; delete result.url; }
        return result;
      }); return {};
    }
    throw Error("Unexpected CDP call");
  } };
  return { state, newPage: async () => { state.pages++; return { close: async () => { state.closed++; } }; },
    newCDPSession: async () => session };
}

test("export is exclusive; import verifies the owned empty context and preserves host-only", async t => {
  const root = await mkdtemp(join(tmpdir(), "chromix-cookies-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "cookies.enc"), source = fakeContext([cookie]);
  assert.deepEqual(await exportCookies(source, path, { passphrase }), { exported: 1, formatVersion: 1 });
  const bytes = await readFile(path);
  await assert.rejects(exportCookies(source, path, { passphrase }), { code: "EEXIST" });
  assert.deepEqual(await readFile(path), bytes);
  const target = fakeContext();
  assert.equal((await importCookies(target, path, { passphrase })).imported, 1);
  const write = target.state.calls.find(([method]) => method === "Network.setCookies")[1];
  assert.equal(write.cookies[0].domain, undefined);
  assert.equal(write.cookies[0].url, "http://localhost/");
  assert.equal(target.state.closed, 1); assert.equal(target.state.detached, 1);
  const occupied = fakeContext([cookie]);
  await assert.rejects(importCookies(occupied, path, { passphrase }), /empty destination/);
  assert.equal(occupied.state.calls.some(([method]) => method === "Network.setCookies"), false);
  const failing = fakeContext([], true);
  await assert.rejects(importCookies(failing, path, { passphrase }), /write failed/);
  assert.equal(failing.state.closed, 1); assert.equal(failing.state.detached, 1);
  const corrupted = fakeContext();
  await writeFile(path, Buffer.from("corrupt"));
  await assert.rejects(importCookies(corrupted, path, { passphrase }));
  assert.equal(corrupted.state.pages, 0);
});

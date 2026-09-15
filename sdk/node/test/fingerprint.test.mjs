import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeFingerprintArgs } from "../_fingerprint.js";
import { lookupProxy, networkArgs, resolveWebrtcArgs } from "../_network.js";
import { buildArgs } from "../index.js";

for (const mode of ["off", "false", "0", "disable", "disabled", "OFF", "FALSE"]) {
  test(`off canonicalization: ${mode}`, () => {
    const args = ["--fingerprint-platform=windows", `--fingerprint=${mode}`, "--fingerprint-timezone=UTC"];
    assert.deepEqual(normalizeFingerprintArgs(args), ["--fingerprint=off", "--fingerprint-timezone=UTC"]);
    assert.equal(args[0], "--fingerprint-platform=windows");
  });
}

for (const prefix of ['--fingerprint-', '--uxr-']) {
  for (const flag of ['timer-resolution=7', 'timer-resolution=0', 'max-touch-points=16',
    'gpu-backend=native', 'audio-render=isolated', 'audio-seed=18446744073709551615',
    'pointer=fine', 'hover=none', 'color-scheme=dark', 'forced-colors=active',
    'preferred-contrast=less', 'keyboard-layout=native', 'codec-h264=disabled',
    'codec-av1=native', 'codec-vp9=supported,smooth,power-efficient']) {
    test(`backend option syntax ${prefix}${flag}`, () => {
      assert.deepEqual(normalizeFingerprintArgs([prefix + flag]), [prefix + flag]);
    });
  }
  for (const flag of ['timer-resolution=+1', 'timer-resolution=1.5', 'timer-resolution=1001',
    'max-touch-points=17', 'audio-seed=0', 'audio-seed=18446744073709551616', 'audio-render=noise',
    'gpu-backend=software', 'hdr=high', 'color-scheme=auto', 'codec-h264=smooth',
    'codec-h264=supported,', 'codec-vp8= supported', 'codec-vp9=supported,unknown',
    'keyboard-layout=us', 'font-policy=restricted']) {
    test(`invalid backend value ${prefix}${flag}`, () => assert.throws(() => normalizeFingerprintArgs([prefix + flag])));
  }
}

test('effective aliases, mixed pointer and synthetic keyboard', () => {
  const valid = ['--fingerprint-pointer=none', '--uxr-pointer=fine', '--fingerprint-max-touch-points=5'];
  assert.deepEqual(normalizeFingerprintArgs(valid), valid);
  for (const [pointer, touch] of [['none', '1'], ['coarse', '0']])
    assert.throws(() => normalizeFingerprintArgs([`--uxr-pointer=${pointer}`, `--fingerprint-max-touch-points=${touch}`]), /inconsistent/);
  assert.ok(normalizeFingerprintArgs(['--uxr-synthetic-device-tests=true', '--fingerprint-keyboard-layout=us']));
});

test('restricted font pool preserves Unicode and spaces and rejects empty entries', () => {
  const flags = ['--fingerprint-font-policy=restricted', '--fingerprint-font-whitelist=Arial Narrow, ＭＳ ゴシック'];
  assert.deepEqual(normalizeFingerprintArgs(flags), flags);
  for (const families of ['', ',Arial', 'Arial,', 'Arial, ,Serif', 'Arial\nInjected', 'A,'.repeat(256) + 'Z'])
    assert.throws(() => normalizeFingerprintArgs([flags[0], `--fingerprint-font-whitelist=${families}`]), /font-policy/);
});

test('audio seed dependency is checked after launch defaults', () => {
  const mode = ['--fingerprint-audio-render=isolated'];
  assert.deepEqual(normalizeFingerprintArgs(mode), mode);
  assert.throws(() => buildArgs({stealthArgs:false, extraArgs:mode}), /seed/);
  assert.ok(buildArgs({extraArgs:mode}).includes(mode[0]));
  assert.ok(buildArgs({stealthArgs:false, extraArgs:[...mode, '--fingerprint=42']}));
  assert.ok(buildArgs({stealthArgs:false, extraArgs:[...mode, '--fingerprint-audio-seed=42']}));
  assert.ok(!buildArgs({headless:false}).includes('--ignore-gpu-blocklist'));
  assert.ok(buildArgs({extraArgs:['--ignore-gpu-blocklist']}).includes('--ignore-gpu-blocklist'));
});

for (const flag of ['--proxy-pac-url=https://example.test/proxy.pac', '--proxy-auto-detect']) {
  test(`proxy policy for ${flag}`, () => {
    assert.equal(networkArgs([flag]).at(-1), '--force-webrtc-ip-handling-policy=disable_non_proxied_udp');
    assert.deepEqual(networkArgs([flag, '--no-proxy-server']), [flag, '--no-proxy-server']);
  });
}

for (const brand of ["Chrome", "Edge", "Opera", "Vivaldi"]) {
  test(`brand canonicalization: ${brand}`, () => {
    assert.equal(normalizeFingerprintArgs([`--fingerprint-brand=${brand.toLowerCase()}`])[0], `--fingerprint-brand=${brand}`);
  });
}

for (const flag of ["--fingerprint=18446744073709551616", "--fingerprint=-1", "--fingerprint=abc",
  "--fingerprint-brand=Unknown", "--fingerprint-brand-version=1.2.3.4.5", "--fingerprint-brand-version=x\nInjected:1",
  "--fingerprint-hardware-concurrency=129", "--fingerprint-device-memory=nan", "--fingerprint-device-memory=inf",
  "--fingerprint-device-memory=0", "--fingerprint-screen-width=-1", "--fingerprint-taskbar-height=-1",
  "--fingerprint-storage-quota=8796093022208", "--fingerprint-noise=maybe"]) {
  test(`invalid public switch: ${flag}`, () => assert.throws(() => normalizeFingerprintArgs([flag])));
}

for (const ip of ["198.51.100.7", "2001:db8::7"]) {
  test(`explicit IP has no lookup: ${ip}`, async () => {
    const args = [`--fingerprint-webrtc-ip=${ip}`];
    assert.deepEqual(await resolveWebrtcArgs(args, null, { lookup: () => assert.fail("lookup attempted") }), args);
  });
}

for (const ip of ["", "example.com", "1.2.3.4:80", "[::1]", "fe80::1%eth0", "127.1", "1.2.3.999", "1.2.3.4\r\n"]) {
  test(`invalid IP: ${JSON.stringify(ip)}`, () => assert.throws(() => networkArgs([`--fingerprint-webrtc-ip=${ip}`]), /IPv4\/IPv6/));
}

test("auto resolves once through the explicit proxy and keeps region flags", async () => {
  const requests = [];
  const args = ["--fingerprint=42", "--fingerprint-webrtc-ip=auto", "--fingerprint-locale=fr-FR"];
  const proxy = { server: "http://proxy.example:8080", username: "u@", password: "p:", bypass: "*" };
  const result = await resolveWebrtcArgs(args, proxy, { lookup: async (route) => {
    requests.push(route); return { timezone: "Asia/Tokyo", locale: "ja-JP", exitIp: "203.0.113.9" };
  } });
  assert.deepEqual(requests, ["http://u%40:p%3A@proxy.example:8080"]);
  assert.ok(result.includes("--fingerprint-webrtc-ip=203.0.113.9"));
  assert.ok(result.includes("--fingerprint-locale=fr-FR"));
  assert.ok(!result.some((arg) => arg.includes("timezone")));
  assert.equal(args[1], "--fingerprint-webrtc-ip=auto");
});

test("GeoIP result is reused, and explicit native alias wins", async () => {
  const options = { geoip: true, exitIp: "203.0.113.9", lookup: () => assert.fail("duplicate lookup") };
  assert.ok((await resolveWebrtcArgs(["--fingerprint=42"], null, options)).includes("--fingerprint-webrtc-ip=203.0.113.9"));
  const args = ["--fingerprint-webrtc-ip=auto", "--uxr-webrtc-ip=198.51.100.8"];
  assert.deepEqual(await resolveWebrtcArgs(args, null, options), args);
});

test("off does not resolve auto", async () => {
  const result = await resolveWebrtcArgs(["--fingerprint=false", "--fingerprint-webrtc-ip=auto"], null,
    { lookup: () => assert.fail("lookup in off mode") });
  assert.ok(result.includes("--fingerprint=off"));
});

test("raw proxy flag is honored without direct fallback", async () => {
  const args = ["--proxy-server=http://proxy.example:8080", "--fingerprint-webrtc-ip=auto"];
  const routes = [];
  const result = await resolveWebrtcArgs(args, null, { lookup: async (route) => {
    routes.push(route); return { exitIp: "203.0.113.5" };
  } });
  assert.deepEqual(routes, ["http://proxy.example:8080"]);
  assert.ok(result.includes("--fingerprint-webrtc-ip=203.0.113.5"));
  await assert.rejects(resolveWebrtcArgs(args, null, { lookup: () => { throw new Error("lookup failed"); } }), /lookup failed/);
});

for (const arg of ['--proxy-server=', '--proxy-server=http://one:80,direct://',
  '--proxy-server=http=one:80;https=two:80', '--proxy-server=http://u:p@one:80',
  '--proxy-pac-url=https://proxy.example/proxy.pac', '--proxy-auto-detect']) {
  for (const proxy of [undefined, 'http://one:80']) {
    test(`ambiguous auto route rejects before lookup: ${arg}, ${proxy}`, async () => {
      await assert.rejects(resolveWebrtcArgs([arg, '--fingerprint-webrtc-ip=auto'], proxy,
        { lookup: () => assert.fail('ambiguous route reached lookup') }), /GeoIP\/auto/);
    });
  }
}

test('proxy route conflicts and no-proxy precedence', () => {
  const proxy = { server: 'http://one:8080', username: 'u', password: 'p' };
  assert.deepEqual(lookupProxy(['--proxy-server=http://one:8080'], proxy), proxy);
  assert.throws(() => lookupProxy(['--proxy-server=http://two:8080'], proxy), /conflicts/);
  assert.throws(() => lookupProxy(['--no-proxy-server'], proxy), /conflicts/);
  const args = ['--proxy-server=http://one:8080', '--no-proxy-server'];
  assert.equal(lookupProxy(args), undefined);
  assert.deepEqual(networkArgs(args, proxy), args);
  assert.equal(networkArgs(args.slice(0, 1)).at(-1), '--force-webrtc-ip-handling-policy=disable_non_proxied_udp');
  assert.equal(networkArgs([args[0], '--force-webrtc-ip-handling-policy=default']).at(-1),
    '--force-webrtc-ip-handling-policy=default');
});

for (const [raw, configured] of [
  ['http://one:80', 'http://ONE'],
  ['https://one:443', 'https://one'],
  ['socks5://one:1080', 'socks5://one'],
  ['http://[2001:db8:0:0:0:0:0:1]:80', 'http://[2001:db8::1]'],
]) {
  test(`equivalent proxy endpoints keep credentials: ${raw}`, () => {
    const proxy = { server: configured, username: 'u', password: 'p' };
    assert.deepEqual(lookupProxy([`--proxy-server=${raw}`], proxy), proxy);
  });
}

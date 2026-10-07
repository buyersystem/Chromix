#!/usr/bin/env python3
"""Compare bounded opaque WebGL seeded reads using an explicitly supplied browser.

Requires Playwright and an already-built browser; never downloads binaries.
PBO/FLOAT/FBO, alpha:true, clipping, shared arrays and reads over 16 MiB are
intentionally native in 0219. No GPU identity/limits acceptance is claimed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROBE = r'''() => {
  const rows = [];
  for (const kind of ['webgl', 'webgl2']) {
    const canvas = document.createElement('canvas');
    canvas.width = 8; canvas.height = 4;
    const gl = canvas.getContext(kind, {alpha:false, antialias:false, preserveDrawingBuffer:true});
    if (!gl) throw new Error(kind + ' unavailable');
    gl.clearColor(0.25, 0.5, 0.75, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    const read = (x=0, y=0, w=8, h=4) => {
      const a = new Uint8Array(w*h*4);
      gl.readPixels(x, y, w, h, gl.RGBA, gl.UNSIGNED_BYTE, a);
      return Array.from(a);
    };
    const first = read(), again = read(), crop = read(1,1,3,2);
    if (gl.getError() !== gl.NO_ERROR) throw new Error(kind + ' valid read failed');
    const sentinel = new Uint8Array(64).fill(165);
    gl.readPixels(0,0,-1,1,gl.RGBA,gl.UNSIGNED_BYTE,sentinel);
    const invalidError = gl.getError();
    const short = new Uint8Array(3).fill(165);
    gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,short);
    const shortError = gl.getError();
    // An older pending error must not prevent a successful read or be consumed.
    gl.enable(0xffffffff);
    const pending = read(), pendingError = gl.getError();
    const transparentCanvas = document.createElement('canvas');
    transparentCanvas.width = 2; transparentCanvas.height = 2;
    const transparent = transparentCanvas.getContext(kind, {alpha:true, antialias:false});
    if (!transparent) throw new Error(kind + ' alpha:true unavailable');
    transparent.clearColor(0,0,0,0); transparent.clear(transparent.COLOR_BUFFER_BIT);
    const alpha = new Uint8Array(16);
    transparent.readPixels(0,0,2,2,transparent.RGBA,transparent.UNSIGNED_BYTE,alpha);
    const packed = [];
    if (kind === 'webgl2') {
      for (const alignment of [1,2,4,8]) {
        gl.pixelStorei(gl.PACK_ALIGNMENT, alignment);
        gl.pixelStorei(gl.PACK_ROW_LENGTH, 7);
        gl.pixelStorei(gl.PACK_SKIP_PIXELS, 2);
        gl.pixelStorei(gl.PACK_SKIP_ROWS, 1);
        const all = new Uint8Array(200).fill(165), view = all.subarray(5,185);
        gl.readPixels(1,1,3,2,gl.RGBA,gl.UNSIGNED_BYTE,view,7);
        packed.push({alignment, bytes:Array.from(all), error:gl.getError()});
      }
      gl.pixelStorei(gl.PACK_ROW_LENGTH, 0);
      gl.pixelStorei(gl.PACK_SKIP_PIXELS, 0);
      gl.pixelStorei(gl.PACK_SKIP_ROWS, 0);
      gl.pixelStorei(gl.PACK_ALIGNMENT, 4);
    }
    rows.push({kind, first, again, crop, pending, pendingError,
      sentinel:Array.from(sentinel), invalidError, short:Array.from(short), shortError,
      alpha:Array.from(alpha), packed});
  }
  return rows;
}'''


def analyze(launches):
    failures = []
    expected_modes = {'native', 'seed-a', 'seed-a-repeat', 'seed-b', 'off', 'noise-false'}
    by_mode = {item['mode']: item for item in launches}
    if set(by_mode) != expected_modes or len(launches) != len(expected_modes):
        return ['launch matrix incomplete']
    for mode, launch in by_mode.items():
        rows = launch.get('rows', [])
        if {row.get('kind') for row in rows} != {'webgl', 'webgl2'} or len(rows) != 2:
            failures.append(f'{mode}: missing WebGL context'); continue
        for row in rows:
            label = f'{mode}/{row["kind"]}'
            first = row['first']
            if len(first) != 128 or row['again'] != first or row['pending'] != first:
                failures.append(label + ': unstable or incomplete read')
            if first[3::4] != [255] * 32 or row['alpha'] != [0] * 16:
                failures.append(label + ': alpha modified')
            expected_crop = [v for y in (1, 2) for x in (1, 2, 3)
                             for v in first[(y * 8 + x) * 4:(y * 8 + x + 1) * 4]]
            if row['crop'] != expected_crop:
                failures.append(label + ': crop/Y inconsistency')
            if row['sentinel'] != [165] * 64 or row['short'] != [165] * 3:
                failures.append(label + ': failed read mutated sentinel')
            if (row['invalidError'], row['shortError'], row['pendingError']) != (1281, 1282, 1280):
                failures.append(label + ': native errors changed')
            if len(row['packed']) != (4 if row['kind'] == 'webgl2' else 0):
                failures.append(label + ': missing PACK cases')
            for packed in row['packed']:
                alignment = packed['alignment']
                stride = (28 + alignment - 1) // alignment * alignment
                expected = [165] * 200
                for y in range(2):
                    start = 5 + 7 + stride + 8 + y * stride
                    expected[start:start + 12] = expected_crop[y * 12:(y + 1) * 12]
                if packed['error'] or packed['bytes'] != expected:
                    failures.append(label + f': PACK/dstOffset alignment={alignment}')
    if failures:
        return failures
    for index, kind in enumerate(('webgl', 'webgl2')):
        native = by_mode['native']['rows'][index]['first']
        seeded = by_mode['seed-a']['rows'][index]['first']
        for mode in ('off', 'noise-false'):
            if by_mode[mode]['rows'][index]['first'] != native:
                failures.append(f'{kind}/{mode}: not native')
        if by_mode['seed-a-repeat']['rows'][index]['first'] != seeded:
            failures.append(kind + ': seed changed across launches')
        if seeded == native or seeded == by_mode['seed-b']['rows'][index]['first']:
            failures.append(kind + ': seed had no observable effect')
        if any(abs(a - b) > 1 for a, b in zip(native, seeded)):
            failures.append(kind + ': RGB delta exceeds one')
    return failures


def collect(browser_path):
    from playwright.sync_api import sync_playwright

    modes = [
        ('native', ['--fingerprint-pixel-noise=native']),
        ('seed-a', ['--fingerprint-pixel-noise=seeded', '--uxr-canvas-seed=42']),
        ('seed-a-repeat', ['--fingerprint-pixel-noise=seeded', '--uxr-canvas-seed=42']),
        ('seed-b', ['--fingerprint-pixel-noise=seeded', '--uxr-canvas-seed=43']),
        ('off', ['--fingerprint-pixel-noise=seeded', '--uxr-canvas-seed=42', '--fingerprint=off']),
        ('noise-false', ['--fingerprint-pixel-noise=seeded', '--uxr-canvas-seed=42',
                         '--fingerprint-noise=false']),
    ]
    with browser_path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'schema_version': 1, 'browser': str(browser_path), 'browser_sha256': digest,
              'qualification': 'Opaque RGBA8 default-buffer mechanism only; not full GPU acceptance.',
              'launches': []}
    with sync_playwright() as playwright:
        for mode, flags in modes:
            launch = {'mode': mode, 'args': flags}
            browser = None
            try:
                browser = playwright.chromium.launch(executable_path=str(browser_path), headless=True,
                    args=['--no-first-run', '--disable-background-networking'] + flags, timeout=30000)
                launch['version'] = browser.version
                page = browser.new_page()
                page.goto('about:blank', timeout=10000)
                launch['rows'] = page.evaluate(PROBE)
            except Exception as error:
                launch['error'] = f'{type(error).__name__}: {error}'
            finally:
                if browser is not None:
                    browser.close()
            report['launches'].append(launch)
    report['failures'] = analyze(report['launches'])
    report['status'] = 'failed' if report['failures'] else 'passed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.browser.is_file():
        parser.error('--browser must name an existing executable')
    if args.output.exists():
        parser.error('--output must be new')
    report = collect(args.browser.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'failures': report['failures']}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())

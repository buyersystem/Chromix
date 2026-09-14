#!/usr/bin/env python3
"""Bounded Canvas mechanism comparison with an explicit, already installed browser.

No SDK persona flags, downloads or validator threshold changes. This checks RGBA8
opaque uploads and OOB readback, not full Canvas/device-pool acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


PROBE = r'''async () => {
  const run = async () => {
    const rows = [];
    for (const kind of ['html', 'offscreen']) {
      for (const frequently of [null, false, true]) {
        const make = alpha => {
          const canvas = kind === 'html' ? document.createElement('canvas') : new OffscreenCanvas(4, 2);
          canvas.width = 4; canvas.height = 2;
          const attrs = {alpha, colorSpace: 'srgb'};
          if (frequently !== null) attrs.willReadFrequently = frequently;
          const context = canvas.getContext('2d', attrs);
          if (!context) throw new Error('2D context unavailable');
          return [canvas, context];
        };
        const [canvas, context] = make(false), input = context.createImageData(4, 2);
        for (let row = 0; row < 2; ++row)
          input.data.set([48,96,160,255,160,80,48,128,40,200,100,64,200,30,160,0], row * 16);
        context.putImageData(input, 0, 0);
        const first = Array.from(context.getImageData(0, 0, 4, 2).data);
        const again = Array.from(context.getImageData(0, 0, 4, 2).data);
        const blob = canvas.convertToBlob ? await canvas.convertToBlob({type:'image/png'}) :
          await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        if (!blob || blob.type !== 'image/png') throw new Error('PNG export unavailable');
        const bitmap = await createImageBitmap(blob), [, decode] = make(true);
        decode.drawImage(bitmap, 0, 0); bitmap.close();
        const decoded = Array.from(decode.getImageData(0, 0, 4, 2).data), edges = [];
        for (const history of ['fresh', 'full', 'crop']) for (const explicit of [false, true]) {
          const [, edge] = make(true);
          edge.fillStyle = 'rgb(48,96,160)'; edge.fillRect(0, 0, 4, 2);
          if (history !== 'fresh') edge.getImageData(0, 0, 4, 2);
          if (history === 'crop') edge.getImageData(1, 0, 2, 1);
          const args = [-1, -1, 6, 4];
          if (explicit) args.push({colorSpace:'srgb'});
          edges.push({history, explicit, pixels:Array.from(edge.getImageData(...args).data)});
        }
        rows.push({kind, frequently, attributes:context.getContextAttributes(),
          first, again, decoded, source:Array.from(input.data), edges});
      }
    }
    return rows;
  };
  let timer;
  try {
    return await Promise.race([run(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Canvas probe exceeded 30 seconds')), 30000);
    })]);
  } finally { clearTimeout(timer); }
}'''

SOURCE = [48, 96, 160, 255, 160, 80, 48, 128, 40, 200, 100, 64, 200, 30, 160, 0] * 2
OPAQUE = [255 if i % 4 == 3 else value for i, value in enumerate(SOURCE)]
PADDED = [value for y in range(4) for x in range(6)
          for value in ([48, 96, 160, 255] if 1 <= x <= 4 and 1 <= y <= 2 else [0, 0, 0, 0])]
CASES = {(kind, frequently) for kind in ('html', 'offscreen') for frequently in (None, False, True)}
EDGES = {(history, explicit) for history in ('fresh', 'full', 'crop') for explicit in (False, True)}


def analyze_rows(rows):
    failures, seen = [], set()

    def fail(case, check):
        failures.append({'case': case, 'check': check})

    def pixels(value, expected):
        return (isinstance(value, list) and len(value) == len(expected) and
                all(type(v) is int and 0 <= v <= 255 for v in value))

    if not isinstance(rows, list) or len(rows) != len(CASES):
        return [{'case': 'matrix', 'check': 'missing-or-invalid-rows'}]
    for index, row in enumerate(rows):
        case = f'row-{index}'
        if not isinstance(row, dict):
            fail(case, 'invalid-row'); continue
        key = row.get('kind'), row.get('frequently')
        if ('frequently' not in row or not isinstance(key[0], str) or type(key[1]) not in (bool, type(None)) or
                key not in CASES or key in seen):
            fail(case, 'duplicate-or-invalid-case'); continue
        seen.add(key)
        case = f'{key[0]}/willReadFrequently={key[1]}'
        attrs = row.get('attributes')
        if not isinstance(attrs, dict) or attrs.get('alpha') is not False or attrs.get('colorSpace') != 'srgb':
            fail(case, 'context-attributes')
        for field, expected in (('first', OPAQUE), ('again', OPAQUE), ('decoded', OPAQUE), ('source', SOURCE)):
            value = row.get(field)
            if not pixels(value, expected):
                fail(case, field + '-invalid-pixels'); continue
            if field == 'source':
                if value != expected: fail(case, 'script-owned-source-mutated')
            else:
                if value[3::4] != expected[3::4]: fail(case, field + '-opaque-alpha')
                if any(v != expected[i] for i, v in enumerate(value) if i % 4 != 3):
                    fail(case, field + '-rgb')
        if row.get('first') != row.get('again'):
            fail(case, 'read-history-changed-pixels')
        edges, edge_seen = row.get('edges'), set()
        if not isinstance(edges, list) or len(edges) != len(EDGES):
            fail(case, 'missing-or-invalid-edge-matrix'); continue
        for edge in edges:
            if not isinstance(edge, dict):
                fail(case, 'invalid-edge'); continue
            edge_key = edge.get('history'), edge.get('explicit')
            if (not isinstance(edge_key[0], str) or type(edge_key[1]) is not bool or
                    edge_key not in EDGES or edge_key in edge_seen):
                fail(case, 'duplicate-or-invalid-edge'); continue
            edge_seen.add(edge_key)
            if not pixels(edge.get('pixels'), PADDED) or edge['pixels'] != PADDED:
                fail(case, f'oob-{edge_key[0]}-explicit={edge_key[1]}')
    return failures


def collect(browser_path, compare_software=False):
    from playwright.sync_api import sync_playwright

    with browser_path.open('rb') as stream:
        browser_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'schema_version': 1, 'qualification': 'RGBA8 mechanism checks, not full Canvas or measured-device acceptance',
              'browser': str(browser_path), 'browser_sha256': browser_sha256,
              'probe_sha256': hashlib.sha256(PROBE.encode()).hexdigest(), 'launches': []}
    with sync_playwright() as playwright:
        for mode in (['default', 'software-requested'] if compare_software else ['default']):
            args = ['--disable-background-networking', '--no-first-run']
            if mode == 'software-requested': args.append('--disable-gpu')
            launch = {'mode': mode, 'args': args, 'status': 'failed'}
            browser = None
            try:
                browser = playwright.chromium.launch(executable_path=str(browser_path), headless=True,
                                                     args=args, timeout=30000)
                launch['version'] = browser.version
                context = browser.new_context()
                page = context.new_page()
                page.goto('about:blank', timeout=10000)
                launch['rows'] = page.evaluate(PROBE)
                launch['failures'] = analyze_rows(launch['rows'])
                launch['failure_groups'] = dict(Counter(item['check'] for item in launch['failures']))
                launch['status'] = 'failed' if launch['failures'] else 'passed'
            except Exception as error:
                launch['error'] = f'{type(error).__name__}: {error}'
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception as error:
                        launch['status'] = 'failed'
                        launch['close_error'] = f'{type(error).__name__}: {error}'
            report['launches'].append(launch)
    report['status'] = 'passed' if all(item['status'] == 'passed' for item in report['launches']) else 'failed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compare-software', action='store_true',
                        help='also request --disable-gpu for diagnosis; never an acceptance workaround')
    args = parser.parse_args(argv)
    if not args.browser.is_file(): parser.error('--browser must name an existing executable')
    if args.output.exists(): parser.error('--output must be new; preserve earlier evidence')
    report = collect(args.browser.resolve(), args.compare_software)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'output': str(args.output), 'launches': [
        {key: launch.get(key) for key in ('mode', 'version', 'status', 'failure_groups', 'error')}
        for launch in report['launches']]}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())

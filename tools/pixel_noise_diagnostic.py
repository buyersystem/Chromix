#!/usr/bin/env python3
"""Local-only explicit Canvas pixel-noise checks against an installed executable.

Uses Playwright and Pillow. PNGs are decoded outside the tested browser so a
second getImageData call cannot apply noise again to the exported pixels.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path

PROBE = r"""async () => {
  const rows = [];
  for (const kind of ['html', 'offscreen']) {
    const canvas = kind === 'html' ? document.createElement('canvas') : new OffscreenCanvas(16, 12);
    canvas.width = 16; canvas.height = 12;
    const ctx = canvas.getContext('2d', {willReadFrequently: true});
    const image = ctx.createImageData(16, 12);
    for (let y=0;y<12;y++) for (let x=0;x<16;x++) {
      const i=(y*16+x)*4;
      image.data.set([40+x*7, 30+y*9, 160, x===0 && y===0 ? 0 : 255], i);
    }
    ctx.putImageData(image, 0, 0);
    const first = Array.from(ctx.getImageData(0,0,16,12).data);
    const repeat = Array.from(ctx.getImageData(0,0,16,12).data);
    const crop = Array.from(ctx.getImageData(3,2,5,4).data);
    const blob = kind === 'html' ? await new Promise(r=>canvas.toBlob(r,'image/png')) :
      await canvas.convertToBlob({type:'image/png'});
    if (!blob) throw new Error('PNG export failed');
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
    rows.push({kind, first, repeat, crop, blob: btoa(binary),
      dataURL: kind === 'html' ? canvas.toDataURL('image/png').split(',')[1] : null});
  }
  return rows;
}"""


def analyze(samples: dict) -> dict:
    from PIL import Image

    checks = []

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})

    for kind in ('html', 'offscreen'):
        rows = {name: next(row for row in data if row['kind'] == kind)
                for name, data in samples.items()}
        native = rows['native']['first']
        for name, row in rows.items():
            prefix = f'{kind}/{name}'
            check(prefix + '/repeat', row['first'] == row['repeat'])
            crop = []
            for y in range(2, 6):
                crop += row['first'][(y*16+3)*4:(y*16+8)*4]
            check(prefix + '/crop', crop == row['crop'])
            for channel in ('blob', 'dataURL'):
                if row[channel] is None:
                    continue
                png = Image.open(io.BytesIO(base64.b64decode(row[channel])))
                check(prefix + '/' + channel, list(png.convert('RGBA').tobytes()) == row['first'])
            check(prefix + '/alpha', row['first'][3::4] == native[3::4])
            check(prefix + '/transparent', row['first'][:4] == native[:4])
            check(prefix + '/bounded', len(native) == len(row['first']) and
                  all(abs(a-b) <= 1 for a, b in zip(native, row['first'])))
        check(kind + '/seed42_changes_pixels', rows['seed42']['first'] != native)
        check(kind + '/different_seeds', rows['seed42']['first'] != rows['seed99']['first'])
        check(kind + '/same_seed_restart', rows['seed42']['first'] == rows['seed42_repeat']['first'])
        check(kind + '/global_disable', rows['disabled']['first'] == native)
        check(kind + '/off', rows['off']['first'] == native)
    return {'status': 'passed' if all(c['passed'] for c in checks) else 'failed',
            'checks': checks, 'scope': 'opaque/transparent RGBA8 Canvas2D and OffscreenCanvas; not full GPU acceptance'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    browser_path = args.browser.resolve(strict=True)
    from playwright.sync_api import sync_playwright
    modes = {
        'native': ['--fingerprint=42', '--fingerprint-pixel-noise=native'],
        'seed42': ['--fingerprint=42', '--fingerprint-pixel-noise=seeded'],
        'seed99': ['--fingerprint=99', '--fingerprint-pixel-noise=seeded'],
        'seed42_repeat': ['--fingerprint=42', '--fingerprint-pixel-noise=seeded'],
        'disabled': ['--fingerprint=42', '--fingerprint-pixel-noise=seeded', '--fingerprint-noise=false'],
        'off': ['--fingerprint=off', '--fingerprint-pixel-noise=seeded'],
    }
    samples = {}
    with sync_playwright() as pw:
        for name, switches in modes.items():
            browser = pw.chromium.launch(executable_path=str(browser_path),
                                         headless=not args.headed, args=switches)
            try:
                page = browser.new_page()
                page.goto('about:blank')
                samples[name] = page.evaluate(PROBE)
            finally:
                browser.close()
    report = analyze(samples)
    report.update(browser=str(browser_path), samples=samples, modes=modes)
    with browser_path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    report['executable_sha256'] = digest.hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'output': str(args.output),
                      'failed': [c['name'] for c in report['checks'] if not c['passed']]}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

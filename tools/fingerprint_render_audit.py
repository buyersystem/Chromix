#!/usr/bin/env python3
"""Extended bitmap/export/ownership/GPU integration audit on an explicit browser."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import math
from fingerprint_runtime_audit import launch, server

PROBE = Path(__file__).resolve().parents[1] / 'sdk/python/chromix/render_integration_probe.js'


from chromix._render_integration import assess, _assess


def run(browser, headed=False):
    report = {'schema_version': 1, 'browser_sha256': launch.pool.file_hash(browser),
              'probe_sha256': launch.pool.file_hash(PROBE), 'runs': [], 'errors': [], 'unavailable': [],
              'qualification': 'native API integration; not physical GPU or profile-distinct rendering attestation'}
    try:
        from playwright.sync_api import sync_playwright
        with server() as origin, sync_playwright() as pw:
            instance = pw.chromium.launch(executable_path=str(browser.resolve()), headless=not headed,
                args=launch.NATIVE_ARGS, chromium_sandbox=True)
            try:
                report['browser_version'] = instance.version
                context = instance.new_context(no_viewport=True)
                context.add_init_script(path=str(PROBE))
                for _ in range(2):
                    page = context.new_page()
                    try:
                        page.goto(origin)
                        observation = page.evaluate('''async () => {
                          let timer;
                          try { return await Promise.race([chromixRenderProbe(), new Promise((_, reject) => {
                            timer = setTimeout(() => reject(Error('render suite timed out')), 120000);
                          })]); } finally { clearTimeout(timer); }
                        }''')
                        errors, unavailable = assess(observation)
                        report['runs'].append(observation)
                        report['errors'].extend(errors); report['unavailable'].extend(unavailable)
                    finally:
                        page.close()
                context.close()
            finally:
                instance.close()
    except Exception as error:
        report['errors'].append(str(error))
    if launch.pool.file_hash(browser) != report['browser_sha256']:
        report['errors'].append('browser executable changed')
    if len(report['runs']) != 2:
        report['errors'].append('not all render launches completed')
    report['status'] = 'failed' if report['errors'] else 'incomplete' if report['unavailable'] else 'passed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args(argv)
    if not args.browser.is_file() or args.output.exists():
        parser.error('use an existing executable and a new report path')
    report = run(args.browser, args.headed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
    print(json.dumps({'status': report['status'], 'errors': report['errors'], 'unavailable': report['unavailable']}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())

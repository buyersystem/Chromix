#!/usr/bin/env python3
"""Run focused feature diagnostics against one explicitly selected executable.

This is a local preflight for pixel noise and GPU identity, not complete browser
fingerprint acceptance or a comparison against a third-party binary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

TOOLS = Path(__file__).resolve().parent
SUITES = (
    ('canvas', 'pixel_noise_diagnostic.py'),
    ('webgl', 'webgl_seeded_noise_diagnostic.py'),
    ('gpu_identity', 'gpu_identity_diagnostic.py'),
)


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def summarize(rows):
    failed = [row['suite'] for row in rows if row['status'] == 'failed']
    incomplete = [row['suite'] for row in rows if row['status'] == 'incomplete']
    return {'status': 'failed' if failed else 'incomplete' if incomplete else 'passed',
            'failed_suites': failed, 'incomplete_suites': incomplete}


def validate_result(path, suite, digest, returncode):
    if not path.is_file():
        return {'status': 'failed', 'reason': 'diagnostic did not produce its report'}
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        return {'status': 'failed', 'reason': f'invalid report: {error}'}
    if not isinstance(report, dict):
        return {'status': 'failed', 'reason': 'report must be an object'}
    actual = report.get('executable_sha256') if suite == 'canvas' else report.get('browser_sha256')
    if actual != digest:
        return {'status': 'failed', 'reason': 'executable digest mismatch'}
    status = report.get('status')
    if status not in ('passed', 'failed', 'incomplete'):
        return {'status': 'failed', 'reason': 'unknown diagnostic status'}
    if status == 'passed' and returncode != 0:
        return {'status': 'failed', 'reason': 'report and exit status disagree'}
    if status != 'passed' and returncode == 0:
        return {'status': 'failed', 'reason': 'non-passing report with successful exit'}
    return {'status': status, 'reason': 'see individual report for checks and coverage'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path, help='new evidence directory')
    parser.add_argument('--timeout', type=int, default=300, help='seconds per diagnostic')
    parser.add_argument('--vendor', default='Chromix Diagnostic Vendor')
    parser.add_argument('--renderer', default='Chromix Diagnostic Renderer')
    args = parser.parse_args(argv)
    if not args.browser.is_file():
        parser.error('--browser must name an existing executable')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if not args.vendor.strip() or not args.renderer.strip():
        parser.error('diagnostic identity strings must be non-empty')
    browser = args.browser.resolve()
    if args.output_dir.exists():
        parser.error('--output-dir must be new')
    args.output_dir.mkdir(parents=True)
    output = args.output_dir.resolve()
    digest = file_digest(browser)
    rows = []
    for suite, script in SUITES:
        target = output / f'{suite}.json'
        command = [sys.executable, str(TOOLS / script), '--browser', str(browser),
                   '--output', str(target)]
        if suite == 'gpu_identity':
            command += ['--vendor', args.vendor, '--renderer', args.renderer]
        start = time.monotonic()
        with (output / f'{suite}.log').open('w', encoding='utf-8') as log:
            try:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=args.timeout, check=False)
                verdict = validate_result(target, suite, digest, result.returncode)
                code = result.returncode
            except subprocess.TimeoutExpired:
                verdict = {'status': 'failed', 'reason': 'diagnostic timeout; not evidence of success'}
                code = None
            except OSError as error:
                verdict = {'status': 'failed', 'reason': f'diagnostic could not start: {error}'}
                code = None
        rows.append({'suite': suite, **verdict, 'returncode': code,
                     'seconds': round(time.monotonic() - start, 2), 'report': target.name,
                     'command': command})
        print(f'{suite}: {verdict["status"]}', flush=True)
    summary = {'schema_version': 1, 'browser': str(browser), 'browser_sha256': digest,
               **summarize(rows), 'suites': rows,
               'qualification': 'Focused local feature preflight; not complete fingerprint acceptance, '
                                'release provenance, site-detection score or physical-device equivalence.'}
    if file_digest(browser) != digest:
        summary.update(status='failed', error='browser executable changed during diagnostics')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': summary['status'], 'summary': str(output / 'summary.json')}))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

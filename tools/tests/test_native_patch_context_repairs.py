"""Strict application against independently prepared Chromium source excerpts."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).with_name('fixtures') / 'native_patch_context_repairs.json').read_text())


def run_patch(directory, patch, reverse=False):
    program = shutil.which('gpatch') or shutil.which('patch')
    if not program:
        pytest.skip('GNU patch is required')
    return subprocess.run([program, '-p1', '--fuzz=0', '--batch', '--binary', '--get=0',
        '--no-backup-if-mismatch', '--reject-file=-', '--reverse' if reverse else '--forward',
        '-i', str(patch)], cwd=directory, capture_output=True, text=True, timeout=10,
        env={**os.environ, 'LC_ALL': 'C', 'PATCH_GET': '0'})


def make_source(tmp_path, number):
    item = FIXTURE['sources'][number]
    lines = []
    for first, content in item['sections']:
        assert len(lines) < first
        lines.extend('// unrelated pinned Chromium source\n' for _ in range(first - 1 - len(lines)))
        lines.extend(content.splitlines(keepends=True))
    target = tmp_path / item['target']
    target.parent.mkdir(parents=True)
    target.write_bytes((''.join(lines) + '// not EOF\n').encode())
    return target


@pytest.mark.parametrize('number', ['0005', '0146'])
def test_repaired_patch_strict_forward_reverse(tmp_path, number):
    target = make_source(tmp_path, number)
    original = target.read_bytes()
    patch = ROOT / FIXTURE['sources'][number]['patch']
    for reverse in (False, True):
        result = run_patch(tmp_path, patch, reverse)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert not re.search(r'fuzz|offset|FAILED', output, re.I), output
    assert target.read_bytes() == original


def test_old_asymmetric_header_hunk_is_not_a_valid_interior_gnu_hunk(tmp_path):
    target = make_source(tmp_path, '0005')
    patch = ROOT / FIXTURE['sources']['0005']['patch']
    text = patch.read_text()
    # The old source text matched exactly, but 4 leading / 2 trailing context
    # lines make GNU patch infer an end-of-file anchor for an interior hunk.
    broken = text.replace('@@ -23,6 +23,7 @@\n',
        '@@ -22,6 +22,7 @@\n #include "base/clang_profiling_buildflags.h"\n', 1)
    broken = broken.replace(' #include "base/debug/crash_logging.h"\n', '', 1)
    legacy = tmp_path / 'legacy.patch'
    legacy.write_bytes(broken.encode())
    assert '#include "base/clang_profiling_buildflags.h"' in target.read_text()
    result = run_patch(tmp_path, legacy)
    assert result.returncode != 0 and 'Hunk #1 FAILED' in result.stdout


@pytest.mark.parametrize('number', ['0005', '0146'])
def test_optional_prepared_source_provenance(number):
    root = os.environ.get('CHROMIX_NATIVE_PATCH_PREIMAGES')
    if not root:
        pytest.skip('independently prepared per-patch preimages are optional')
    item = FIXTURE['sources'][number]
    original = (Path(root) / item['preimage_file']).read_bytes()
    assert hashlib.sha256(original).hexdigest() == item['preimage_sha256']
    lines = original.decode().splitlines(keepends=True)
    for first, text in item['sections']:
        assert ''.join(lines[first - 1:first - 1 + len(text.splitlines())]) == text

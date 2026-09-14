"""Pinned Canvas upload/readback contracts, not native browser acceptance.

The complete patched putImageData/PutByteArray/readPixels methods execute with
bounded Skia/GPU shims under ASan/UBSan. The shims model byte storage, geometry,
allocation failures and calls, not real Skia color management or GPU drivers.
Set CHROMIX_CANVAS_UPSTREAM_ROOT to a clean Chromium 152 source tree to also
verify independently acquired upstream hashes and the file-specific patch chain.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_fingerprint_canvas import CXX, sanitizer_env, sanitizer_flags
from test_fingerprint_features import block

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name('fixtures')
EVIDENCE = json.loads((FIXTURES / 'canvas_native_paths.json').read_text(encoding='utf-8'))


def patch_path(number):
    matches = list((ROOT / 'patches').glob(number + '-*.patch'))
    assert len(matches) == 1
    return matches[0]


def source_fixture(number):
    lines = []
    for section in EVIDENCE['sources'][number]['sections']:
        first, text = section['line'], section['text']
        assert len(lines) < first
        lines.extend('// unrelated pinned source line\n' for _ in range(first - 1 - len(lines)))
        lines.extend(text.splitlines(keepends=True))
    return ''.join(lines) + '// not EOF\n'


def apply(directory, patch, *, reverse=False, allow_offsets=False):
    command = shutil.which('gpatch') or shutil.which('patch')
    if not command:
        pytest.skip('GNU patch is required')
    result = subprocess.run([command, '-p1', '--fuzz=0', '--batch', '--binary', '--get=0',
        '--no-backup-if-mismatch', '--reject-file=-', '--reverse' if reverse else '--forward',
        '-i', str(patch)], cwd=directory, capture_output=True, text=True, timeout=15,
        env={**os.environ, 'LC_ALL': 'C', 'PATCH_GET': '0'})
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert not re.search(r'fuzz|FAILED|Reversed patch', output, re.I), output
    if not allow_offsets:
        assert 'offset' not in output, output


@pytest.fixture(scope='module')
def patched_sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp('canvas-native-sources')
    result = {}
    for number in ('0149', '0150'):
        target = directory / EVIDENCE['sources'][number]['target']
        target.parent.mkdir(parents=True, exist_ok=True)
        original = source_fixture(number).encode()
        target.write_bytes(original)
        apply(directory, patch_path(number))
        result[number] = target.read_text(encoding='utf-8')
        apply(directory, patch_path(number), reverse=True)
        assert target.read_bytes() == original
    return result


@pytest.mark.parametrize('number', ['0149', '0150'])
def test_strict_patch_roundtrip(patched_sources, number):
    assert patched_sources[number] != source_fixture(number)


def test_predecessors_and_scope(patched_sources):
    assert EVIDENCE['chromium_version'] == (ROOT / 'CHROMIUM_VERSION').read_text().strip()
    assert EVIDENCE['core_commit'] == 'e71b91c6e336d0f25cfc6b9ef09298a9d2506e24'
    assert len(EVIDENCE['core_inputs']) == 2
    for item in EVIDENCE['sources']['0149']['predecessors']:
        assert hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest() == item['sha256']
    for number in ('0149', '0150'):
        additions = '\n'.join(line[1:] for line in patch_path(number).read_text().splitlines()
                              if line.startswith('+') and not line.startswith('+++'))
        assert not any(token in additions for token in (
            'UNSAFE_BUFFERS', '#pragma', 'UxrConfig', 'CommandLine', 'willReadFrequently'))
    upload = block(patched_sources['0149'], 'bool UxrCopyOpaqueImageData(')
    assert 'readPixels' not in upload and 'writePixels' not in upload
    assert 'SkPixmapToSpan(clipped)' in upload and 'row.copy_from(' in upload
    assert patched_sources['0149'].count('#include "base/containers/span.h"') == 1
    assert 'static_cast<GLuint>(dst_row_bytes)' in patched_sources['0150']


@pytest.mark.parametrize('number', ['0149', '0150'])
def test_optional_independent_preimage_and_full_method_roundtrip(tmp_path, number):
    root = os.environ.get('CHROMIX_CANVAS_UPSTREAM_ROOT')
    if not root:
        pytest.skip('clean pinned source root is required for independent provenance')
    item = EVIDENCE['sources'][number]
    original_path = Path(root) / item['target']
    original, timestamp = original_path.read_bytes(), original_path.stat().st_mtime_ns
    assert hashlib.sha256(original).hexdigest() == item['upstream_sha256']
    target = tmp_path / item['target']
    target.parent.mkdir(parents=True)
    target.write_bytes(original)
    if number == '0149':
        for index, core in enumerate(EVIDENCE['core_inputs']):
            fragment = tmp_path / f'core-{index}.patch'
            fragment.write_bytes(core['target_fragment'].encode())
            apply(tmp_path, fragment)
        for predecessor in item['predecessors']:
            path = ROOT / predecessor['path']
            apply(tmp_path, path, allow_offsets=path.name.startswith('0076-'))
    before = target.read_bytes()
    assert hashlib.sha256(before).hexdigest() == item['preimage_sha256']
    lines = before.decode().splitlines(keepends=True)
    for section in item['sections']:
        first, text = section['line'], section['text']
        assert ''.join(lines[first - 1:first - 1 + len(text.splitlines())]) == text
    apply(tmp_path, patch_path(number))
    apply(tmp_path, patch_path(number), reverse=True)
    assert target.read_bytes() == before
    assert (original_path.read_bytes(), original_path.stat().st_mtime_ns) == (original, timestamp)


@pytest.fixture(scope='module')
def contract_cache():
    return {}


@pytest.fixture(scope='module')
def contract_binary(request, tmp_path_factory, patched_sources, contract_cache):
    if request.param in contract_cache:
        return contract_cache[request.param]
    if not CXX:
        pytest.skip('C++20 compiler is required')
    patched = request.param == 'patched'
    source = patched_sources if patched else {n: source_fixture(n) for n in ('0149', '0150')}
    definitions = ''
    if patched:
        definitions += block(source['0149'], 'bool UxrCopyOpaqueImageData(') + '\n'
    for match in re.finditer(r'void BaseRenderingContext2D::putImageData\(', source['0149']):
        definitions += block(source['0149'][match.start():], 'void BaseRenderingContext2D::putImageData(') + '\n'
    definitions += block(source['0149'], 'void BaseRenderingContext2D::PutByteArray(') + '\n'
    definitions += block(source['0150'], 'bool MailboxTextureBacking::readPixels(') + '\n'
    directory = tmp_path_factory.mktemp('canvas-native-' + request.param)
    unit = directory / 'canvas.cc'
    unit.write_text(f'#define CHROMIX_PATCHED {int(patched)}\n' +
        (FIXTURES / 'canvas_native_shim.h').read_text(encoding='utf-8') + '\n' +
        definitions + '\n' + (FIXTURES / 'canvas_native_cases.cc').read_text(encoding='utf-8'),
        encoding='utf-8')
    binary = directory / ('canvas.exe' if os.name == 'nt' else 'canvas')
    # Upstream deliberately ignores dst_row_bytes, the very regression under
    # test. Chromium permits unused parameters; keep the stricter patched build.
    legacy_flags = [] if patched else ['-Wno-unused-parameter']
    result = subprocess.run([CXX, '-std=c++20', '-O1', '-g', '-Wall', '-Wextra', '-Werror', *legacy_flags,
        *sanitizer_flags(), str(unit), '-o', str(binary)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    contract_cache[request.param] = request.param, binary
    return contract_cache[request.param]


COMMON_CASES = ['alpha-true', 'empty-dirty', 'detached', 'open-layer', 'lost-context',
                'no-provider', 'no-paint', 'conversion-alpha-true', 'mailbox-inbounds', 'mailbox-error']
PATCHED_CASES = ['rgba8', 'bgra8', 'f16', 'f32', 'source-bits', 'padding', 'allocation-failure',
                 'subset-failure', 'unsupported-format', 'dirty-rectangle', 'negative-destination',
                 'negative-dirty-size', 'conversion-opaque', 'conversion-allocation-failure',
                 'opaque-random', 'mailbox-padding', 'mailbox-oob', 'mailbox-random',
                 'mailbox-invalid', 'mailbox-extreme', 'mailbox-formats']


@pytest.mark.parametrize('contract_binary,case',
    [('native', case) for case in COMMON_CASES + ['native-regression']] +
    [('patched', case) for case in COMMON_CASES + PATCHED_CASES], indirect=['contract_binary'])
def test_extracted_native_methods(contract_binary, case):
    _, binary = contract_binary
    result = subprocess.run([str(binary), case], capture_output=True, text=True, timeout=15,
                            env=sanitizer_env())
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f'PASS {case}\n'

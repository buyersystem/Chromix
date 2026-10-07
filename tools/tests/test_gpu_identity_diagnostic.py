"""Diagnostic helper fixtures are not browser or physical GPU acceptance."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('gpu_identity_diagnostic', ROOT / 'tools/gpu_identity_diagnostic.py')
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)
VENDOR, RENDERER = 'Diagnostic Vendor', 'Diagnostic Renderer'


def launches():
    snapshot = {'identity': {'vendor': 'WebKit', 'renderer': 'WebKit WebGL', 'version': 'WebGL',
        'shadingLanguageVersion': 'GLSL', 'debugExtension': True, 'unmaskedVendor': 'Native Vendor',
        'unmaskedRenderer': 'Native GPU'}, 'limits': {'MAX_TEXTURE_SIZE': 16384},
        'extensions': ['WEBGL_debug_renderer_info', 'WEBGL_lose_context'],
        'precision': {'FRAGMENT_SHADER/HIGH_FLOAT': [127, 127, 23]}, 'attributes': {'alpha': True}, 'error': 0}
    rows = []
    for mode, args in diagnostic.launch_cases(VENDOR, RENDERER):
        ready = deepcopy(snapshot)
        if mode == 'compatibility-identity':
            ready['identity'].update(unmaskedVendor=VENDOR, unmaskedRenderer=RENDERER)
        item = {'status': 'observed', 'ready': ready, 'again': deepcopy(ready), 'restored': deepcopy(ready),
                'lost': {'isContextLost': True, 'vendor': None, 'renderer': None,
                         'unmaskedVendor': None, 'unmaskedRenderer': None},
                'lifecycle': {'status': 'observed', 'isContextLost': False}}
        rows.append({'mode': mode, 'args': args, 'status': 'observed', 'probe': {'secureContext': True,
            'webgl': {api: deepcopy(item) for api in ('webgl', 'webgl2')},
            'webgpu': {'status': 'observed', 'identity': {'vendor': 'native', 'architecture': 'native-arch',
                'device': '', 'description': ''}, 'limits': {'maxTextureDimension2D': 8192},
                'features': [], 'isFallbackAdapter': False}}})
    return rows


def analyze(rows):
    return diagnostic.compare_launches(rows, VENDOR, RENDERER)


def update_snapshots(rows, mode, api, callback):
    for field in ('ready', 'again', 'restored'):
        callback(rows[mode]['probe']['webgl'][api][field])


def test_complete_fixture_passes_without_becoming_hardware_evidence():
    report = analyze(launches())
    assert report['status'] == 'passed'
    assert all(check['status'] == 'passed' for check in report['checks'])
    assert 'physical_attestation' not in report


def test_launch_matrix_requires_explicit_compatibility_and_no_seed_or_bypass():
    cases = dict(diagnostic.launch_cases(VENDOR, RENDERER))
    assert tuple(cases) == diagnostic.MODES
    assert not any('fingerprint' in arg for arg in cases['default'])
    assert not any('gpu-backend' in arg for arg in cases['default-identity'])
    assert '--fingerprint-gpu-backend=native' in cases['native-identity']
    assert '--fingerprint-gpu-backend=compatibility' in cases['compatibility-identity']
    assert '--fingerprint-gpu-vendor=' + VENDOR in cases['compatibility-identity']
    assert '--fingerprint-gpu-renderer=' + RENDERER in cases['compatibility-identity']
    for args in cases.values():
        assert not any(word in arg for arg in args
                       for word in ('synthetic', 'ignore-gpu-blocklist', 'use-angle', 'disable-gpu', 'noise'))
        assert not any(arg.startswith('--fingerprint=') for arg in args)


@pytest.mark.parametrize('value', ['', ' ', '\0bad', 'bad\nvalue', 'bad\tvalue', 'bad\x7fvalue', None, 1])
def test_identity_input_rejects_empty_and_control_characters(value):
    for vendor, renderer in ((value, RENDERER), (VENDOR, value)):
        with pytest.raises(ValueError):
            diagnostic.launch_cases(vendor, renderer)


def test_identity_values_remain_exact_single_arguments():
    args = dict(diagnostic.launch_cases('测试 Vendor', 'ANGLE (GPU, API) = x'))['compatibility-identity']
    assert args[-2:] == ['--fingerprint-gpu-vendor=测试 Vendor', '--fingerprint-gpu-renderer=ANGLE (GPU, API) = x']


@pytest.mark.parametrize('mode', [1, 2, 3])
def test_native_and_unconfigured_compatibility_identity_must_stay_native(mode):
    rows = launches()
    update_snapshots(rows, mode, 'webgl', lambda s: s['identity'].update(unmaskedVendor=VENDOR))
    assert analyze(rows)['status'] == 'failed'


@pytest.mark.parametrize('field,value', [('limits', {'MAX_TEXTURE_SIZE': 32768}),
    ('extensions', ['fabricated']), ('precision', {'FRAGMENT_SHADER/HIGH_FLOAT': [127, 127, 24]}),
    ('attributes', {'alpha': False})])
@pytest.mark.parametrize('api', ['webgl', 'webgl2'])
def test_identity_cannot_change_capabilities(api, field, value):
    rows = launches()
    update_snapshots(rows, 4, api, lambda s: s.update({field: value}))
    assert analyze(rows)['status'] == 'failed'


@pytest.mark.parametrize('field,value', [('identity', {'vendor': 'custom'}),
    ('limits', {'maxTextureDimension2D': 1}), ('features', ['fabricated']), ('isFallbackAdapter', True)])
def test_webgpu_must_remain_native(field, value):
    rows = launches()
    rows[-1]['probe']['webgpu'][field] = value
    assert analyze(rows)['status'] == 'failed'


@pytest.mark.parametrize('mutation', [
    lambda r: r.pop(), lambda r: r.append(deepcopy(r[0])),
    lambda r: r[0].update(mode='unknown'), lambda r: r[0].update(status='failed', error='crashed'),
    lambda r: r[0]['probe'].update(secureContext=False),
    lambda r: r[0]['probe']['webgl']['webgl'].pop('ready'),
    lambda r: r[0]['probe']['webgl']['webgl']['ready'].update(error=1280),
    lambda r: r[0]['probe']['webgl']['webgl'].pop('again'),
    lambda r: r[0]['probe']['webgl']['webgl'].pop('restored'),
    lambda r: r[0]['probe']['webgl']['webgl']['lost'].update(unmaskedVendor='leaked'),
    lambda r: r[0]['probe']['webgl']['webgl']['lifecycle'].update(isContextLost=True),
    lambda r: r[0]['probe']['webgl']['webgl']['lifecycle'].update(status='failed', error='timeout'),
    lambda r: r[0]['probe']['webgpu'].update(status='failed', error='timeout'),
    lambda r: r[-1]['probe']['webgpu'].update(identity={}),
])
def test_missing_or_failed_observations_cannot_pass(mutation):
    rows = launches()
    mutation(rows)
    assert analyze(rows)['status'] == 'failed'


@pytest.mark.parametrize('api', ['webgl', 'webgl2'])
def test_software_guard_is_preserved_and_not_counted_as_custom_success(api):
    rows = launches()
    for mode in range(len(rows)):
        update_snapshots(rows, mode, api, lambda s: s['identity'].update(
            unmaskedVendor='Google', unmaskedRenderer='ANGLE SwiftShader'))
    report = analyze(rows)
    assert report['status'] == 'incomplete'
    assert any(c['check'].endswith('/software-guard') and c['status'] == 'passed' for c in report['checks'])
    update_snapshots(rows, 4, api, lambda s: s['identity'].update(unmaskedVendor=VENDOR, unmaskedRenderer=RENDERER))
    assert analyze(rows)['status'] == 'failed'


def test_ignored_flags_are_incomplete_not_a_false_pass_or_hardware_claim():
    rows = launches()
    rows[-1]['probe'] = deepcopy(rows[0]['probe'])
    report = analyze(rows)
    assert report['status'] == 'incomplete'
    checks = [c for c in report['checks'] if c['check'].endswith('/custom-identity')]
    assert len(checks) == 2
    assert all('override not observed' in c['reason'] for c in checks)


def test_partial_wrong_identity_fails():
    rows = launches()
    update_snapshots(rows, 4, 'webgl', lambda s: s['identity'].update(unmaskedVendor='Wrong'))
    assert analyze(rows)['status'] == 'failed'


def test_identical_requested_and_baseline_identity_is_not_opt_in_evidence():
    rows = launches()
    for mode in range(len(rows)):
        for api in ('webgl', 'webgl2'):
            update_snapshots(rows, mode, api, lambda s: s['identity'].update(unmaskedVendor=VENDOR, unmaskedRenderer=RENDERER))
    assert analyze(rows)['status'] == 'incomplete'


@pytest.mark.parametrize('surface', ['webgpu', 'webgl', 'debug', 'restore'])
def test_unavailable_surfaces_remain_gaps(surface):
    rows = launches()
    for mode in range(len(rows)):
        probe = rows[mode]['probe']
        if surface == 'webgpu':
            probe['webgpu'] = {'status': 'unavailable', 'reason': 'no adapter'}
        elif surface == 'webgl':
            probe['webgl']['webgl'] = {'status': 'unavailable', 'reason': 'no context'}
        elif surface == 'restore':
            probe['webgl']['webgl']['lifecycle'] = {'status': 'unavailable', 'reason': 'no loss extension'}
        else:
            update_snapshots(rows, mode, 'webgl', lambda s: s['identity'].update(
                debugExtension=False, unmaskedVendor=None, unmaskedRenderer=None))
    assert analyze(rows)['status'] == 'incomplete'


def test_local_page_is_loopback_http():
    with diagnostic.local_page() as url:
        assert url.startswith('http://127.0.0.1:')
        with urlopen(url, timeout=5) as response:
            assert response.status == 200
            assert b'Local GPU identity diagnostic' in response.read()


def test_cli_preserves_existing_evidence(tmp_path, monkeypatch):
    browser, output = tmp_path / 'browser', tmp_path / 'report.json'
    browser.touch()
    output.write_text('old evidence')
    monkeypatch.setattr(diagnostic, 'collect', lambda *_: pytest.fail('must not launch'))
    with pytest.raises(SystemExit):
        diagnostic.main(['--browser', str(browser), '--vendor', VENDOR, '--renderer', RENDERER, '--output', str(output)])
    assert output.read_text() == 'old evidence'


@pytest.mark.parametrize('status,code', [('passed', 0), ('failed', 1), ('incomplete', 2)])
def test_cli_writes_report_and_distinct_exit_codes(tmp_path, monkeypatch, status, code):
    browser, output = tmp_path / 'browser', tmp_path / 'new/report.json'
    browser.touch()
    def collect(path, vendor, renderer, headed):
        assert path == browser.resolve()
        assert (vendor, renderer, headed) == (VENDOR, RENDERER, True)
        return {'status': status, 'checks': []}
    monkeypatch.setattr(diagnostic, 'collect', collect)
    assert diagnostic.main(['--browser', str(browser), '--vendor', VENDOR, '--renderer', RENDERER,
                            '--output', str(output), '--headed']) == code
    assert json.loads(output.read_text())['status'] == status


def test_probe_javascript_parses_without_launching_browser():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for JavaScript syntax verification')
    result = subprocess.run([node, '--check'], input='(' + diagnostic.PROBE + ')',
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_python_sdk_keeps_identity_explicit_without_injecting_backend():
    spec = importlib.util.spec_from_file_location('gpu_sdk_fingerprint', ROOT / 'sdk/python/chromix/_fingerprint.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = ['--fingerprint-gpu-vendor=' + VENDOR, '--fingerprint-gpu-renderer=' + RENDERER]
    for backend in ([], ['--fingerprint-gpu-backend=native'], ['--fingerprint-gpu-backend=compatibility']):
        args = backend + names
        assert module.normalize_fingerprint_args(args, final=True) == args
    with pytest.raises(ValueError):
        module.normalize_fingerprint_args(['--fingerprint-gpu-backend=custom'])


def test_node_sdk_keeps_identity_explicit_without_injecting_backend():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for SDK normalization verification')
    script = '''import assert from 'node:assert/strict';
import {normalizeFingerprintArgs} from './sdk/node/_fingerprint.js';
const names = ['--fingerprint-gpu-vendor=Diagnostic Vendor', '--fingerprint-gpu-renderer=Diagnostic Renderer'];
for (const backend of [[], ['--fingerprint-gpu-backend=native'], ['--fingerprint-gpu-backend=compatibility']]) {
  const args = [...backend, ...names];
  assert.deepEqual(normalizeFingerprintArgs(args), args);
}
assert.throws(() => normalizeFingerprintArgs(['--fingerprint-gpu-backend=custom']));
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=ROOT,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr

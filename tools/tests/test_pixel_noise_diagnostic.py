"""The diagnostic must distinguish active seeded output from native-only output."""
import base64
import copy
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pixel_noise_diagnostic import analyze


def row(kind, delta):
    Image = pytest.importorskip('PIL.Image')
    pixels = []
    for y in range(12):
        for x in range(16):
            pixels.extend([0, 0, 0, 0] if x == y == 0 else
                          [40+x*7+delta, 30+y*9+delta, 160+delta, 255])
    stream = io.BytesIO()
    Image.frombytes('RGBA', (16, 12), bytes(pixels)).save(stream, format='PNG')
    encoded = base64.b64encode(stream.getvalue()).decode('ascii')
    crop = []
    for y in range(2, 6):
        crop += pixels[(y*16+3)*4:(y*16+8)*4]
    return {'kind': kind, 'first': pixels, 'repeat': pixels[:], 'crop': crop,
            'blob': encoded, 'dataURL': encoded if kind == 'html' else None}


def samples():
    return {name: [row(kind, delta) for kind in ('html', 'offscreen')]
            for name, delta in [('native', 0), ('seed42', 1), ('seed99', -1),
                                ('seed42_repeat', 1), ('disabled', 0), ('off', 0)]}


def test_coherent_explicit_noise_is_accepted():
    assert analyze(samples())['status'] == 'passed'


def test_native_only_binary_cannot_pass():
    data = samples()
    for name in data:
        data[name] = copy.deepcopy(data['native'])
    report = analyze(data)
    assert report['status'] == 'failed'
    failed = [c['name'] for c in report['checks'] if not c['passed']]
    assert 'html/seed42_changes_pixels' in failed
    assert 'offscreen/different_seeds' in failed


@pytest.mark.parametrize('key', ['repeat', 'crop', 'blob', 'alpha', 'transparent'])
def test_inconsistent_paths_are_rejected(key):
    data = samples()
    value = data['seed42'][0]
    if key in ('repeat', 'crop'):
        value[key][4] ^= 4
    elif key == 'blob':
        value['blob'] = data['native'][0]['blob']
    elif key == 'alpha':
        value['first'][7] = 254
    else:
        value['first'][0] = 1
    assert analyze(data)['status'] == 'failed'

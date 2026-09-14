"""Synthetic fixtures check rejection/oracle logic, never hardware acceptance."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('canvas_native_diagnostic', ROOT / 'tools/canvas_native_diagnostic.py')
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def good_rows():
    return [{'kind': kind, 'frequently': frequent, 'attributes': {'alpha': False, 'colorSpace': 'srgb'},
        'first': diagnostic.OPAQUE.copy(), 'again': diagnostic.OPAQUE.copy(),
        'decoded': diagnostic.OPAQUE.copy(), 'source': diagnostic.SOURCE.copy(),
        'edges': [{'history': history, 'explicit': explicit, 'pixels': diagnostic.PADDED.copy()}
                  for history in ('fresh', 'full', 'crop') for explicit in (False, True)]}
        for kind in ('html', 'offscreen') for frequent in (None, False, True)]


def test_complete_fixture_satisfies_mechanism_oracle():
    assert diagnostic.analyze_rows(good_rows()) == []


@pytest.mark.parametrize('mutation', [
    lambda r: r.pop(), lambda r: r.append(deepcopy(r[0])),
    lambda r: r.__setitem__(1, deepcopy(r[0])), lambda r: r.__setitem__(0, None),
    lambda r: r[0].__setitem__('frequently', 0), lambda r: r[0].__setitem__('kind', []),
    lambda r: r[0].pop('frequently'),
    lambda r: r[0]['attributes'].__setitem__('alpha', True),
    lambda r: r[0]['attributes'].__setitem__('colorSpace', 'display-p3'),
    lambda r: r[0]['first'].__setitem__(3, 128), lambda r: r[0]['again'].__setitem__(0, 49),
    lambda r: r[0]['decoded'].__setitem__(7, 0), lambda r: r[0]['source'].__setitem__(7, 255),
    lambda r: r[0]['first'].__setitem__(0, True), lambda r: r[0]['first'].pop(),
    lambda r: r[0]['edges'].pop(), lambda r: r[0]['edges'].__setitem__(1, deepcopy(r[0]['edges'][0])),
    lambda r: r[0]['edges'][0].__setitem__('history', []),
    lambda r: r[0]['edges'][0].__setitem__('explicit', None),
    lambda r: r[0]['edges'][0]['pixels'].__setitem__(0, 48),
    lambda r: r[0]['edges'][1]['pixels'].__setitem__(31, 0),
])
def test_missing_duplicate_and_wrong_observations_fail(mutation):
    rows = good_rows()
    mutation(rows)
    assert diagnostic.analyze_rows(rows)


def test_all_zero_in_bounds_pixels_do_not_pass_as_padding():
    rows = good_rows()
    for row in rows:
        for edge in row['edges']: edge['pixels'] = [0] * len(diagnostic.PADDED)
    failures = diagnostic.analyze_rows(rows)
    assert len(failures) == 36
    assert all(item['check'].startswith('oob-') for item in failures)


def test_cli_preserves_existing_evidence(tmp_path, monkeypatch):
    browser, output = tmp_path / 'browser', tmp_path / 'report.json'
    browser.touch(); output.write_bytes(b'old evidence')
    monkeypatch.setattr(diagnostic, 'collect', lambda *_: pytest.fail('must not launch'))
    with pytest.raises(SystemExit):
        diagnostic.main(['--browser', str(browser), '--output', str(output)])
    assert output.read_bytes() == b'old evidence'

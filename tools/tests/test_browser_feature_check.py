"""Keep focused feature checks fail-closed without conflating coverage and success."""
import json
from pathlib import Path
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_feature_check as check


@pytest.mark.parametrize('suite,key', [('canvas', 'executable_sha256'), ('webgl', 'browser_sha256'), ('gpu_identity', 'browser_sha256')])
def test_matching_report(tmp_path, suite, key):
    output = tmp_path / 'report.json'
    output.write_text(json.dumps({'status': 'passed', key: 'a'*64}))
    assert check.validate_result(output, suite, 'a'*64, 0)['status'] == 'passed'
    assert check.validate_result(output, suite, 'b'*64, 0)['status'] == 'failed'
    assert check.validate_result(output, suite, 'a'*64, 1)['status'] == 'failed'


def test_incomplete_stays_incomplete(tmp_path):
    output = tmp_path / 'report.json'
    output.write_text(json.dumps({'status': 'incomplete', 'browser_sha256': 'a'*64}))
    assert check.validate_result(output, 'gpu_identity', 'a'*64, 2)['status'] == 'incomplete'
    assert check.summarize([{'suite': 'gpu_identity', 'status': 'incomplete'}])['status'] == 'incomplete'
    assert check.validate_result(output, 'gpu_identity', 'a'*64, 0)['status'] == 'failed'


@pytest.mark.parametrize('text', ['null', '[]', 'not json', '{}'])
def test_malformed_report_fails(tmp_path, text):
    output = tmp_path / 'report.json'; output.write_text(text)
    assert check.validate_result(output, 'webgl', 'a'*64, 0)['status'] == 'failed'


def test_missing_report_fails(tmp_path):
    assert check.validate_result(tmp_path/'missing', 'canvas', 'a'*64, 0)['status'] == 'failed'


def test_main_keeps_all_reports_and_continues_after_timeout(tmp_path, monkeypatch):
    binary = tmp_path/'browser'; binary.write_bytes(b'local test browser')
    output = tmp_path/'evidence'
    digest = check.file_digest(binary)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        target = Path(command[command.index('--output')+1])
        if target.stem == 'webgl':
            raise subprocess.TimeoutExpired(command, 1)
        key = 'executable_sha256' if target.stem == 'canvas' else 'browser_sha256'
        target.write_text(json.dumps({'status': 'passed', key: digest}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(check.subprocess, 'run', run)
    assert check.main(['--browser',str(binary),'--output-dir',str(output),'--timeout','1']) == 1
    report=json.loads((output/'summary.json').read_text())
    assert len(calls)==3
    assert report['failed_suites']==['webgl']
    assert report['browser_sha256']==digest
    assert len(list(output.glob('*.log')))==3
    with pytest.raises(SystemExit):
        check.main(['--browser',str(binary),'--output-dir',str(output)])

"""Keep native cleanup checks ahead of the expensive Windows build."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build-win-x64-github.yml"


def test_native_process_cleanup_regressions_gate_first_compile():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML's YAML 1.1 loader interprets the workflow's "on" key as True.
    triggers = workflow.get("on", workflow.get(True))
    assert "tools/tests/test_ci_stage.py" in triggers["push"]["paths"]
    assert "tools/tests/test_windows_cleanup_gate.py" in triggers["push"]["paths"]
    job = workflow["jobs"]["build-1"]
    steps = job["steps"]
    check = next(step for step in steps if step.get("name") == "Verify native process-tree cleanup")
    compile_step = next(step for step in steps if step.get("id") == "stage")
    assert job["runs-on"] == "windows-2022"
    assert steps.index(check) < steps.index(compile_step)
    assert check["shell"] == "powershell"
    assert check["timeout-minutes"] <= 5
    assert "if" not in check
    assert not check.get("continue-on-error", False)
    assert "PyYAML==6.0.2" in check["run"]
    assert "tools.tests.test_ci_stage.InvokeTrackedRegressionTest" in check["run"]
    assert "tools.tests.test_ci_stage.InvokeTrackedPowerShellTest" in check["run"]
    assert check["run"].count("if ($LASTEXITCODE -ne 0) { throw") == 2

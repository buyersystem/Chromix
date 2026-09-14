"""Execute the publisher's real asset gate without publishing or downloading."""
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap

import pytest


REPO = Path(__file__).resolve().parents[2]
EXPECTED = {
    "linux-x64": "chromix-linux-x64.zip",
    "linux-arm64": "chromix-linux-arm64.zip",
    "win-x64": "chromix-win-x64.zip",
    "win-arm64": "chromix-win-arm64.zip",
    "mac-arm64": "chromix-mac-arm64.zip",
    "mac-x64": "chromix-mac-x64.zip",
}


def publisher_asset_script():
    source = (REPO / ".github/workflows/publish-sdks.yml").read_text(encoding="utf-8")
    section = source.split("      - name: Verify Python browser asset mapping\n", 1)[1]
    section = section.split("\n      - ", 1)[0]
    match = re.search(r"(?ms)^          PYTHONPATH=sdk/python python - <<'PY'\n(.*?)^          PY$", section)
    assert match, "publisher asset check must remain an executable Python heredoc"
    return textwrap.dedent(match.group(1))


def expected_assignment(tree):
    matches = [node for node in tree.body if isinstance(node, ast.Assign)
               and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
               and node.targets[0].id == "expected"]
    assert len(matches) == 1
    return matches[0]


def run_gate(source):
    return subprocess.run([sys.executable, "-c", source], cwd=REPO,
                          env={**os.environ, "PYTHONPATH": str(REPO / "sdk/python"),
                               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
                          capture_output=True, text=True, timeout=15)


def test_publisher_asset_gate_has_all_six_platforms_and_executes_against_checkout():
    source = publisher_asset_script()
    assert ast.literal_eval(expected_assignment(ast.parse(source)).value) == EXPECTED
    result = run_gate(source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "assets match all build outputs" in result.stdout


@pytest.mark.parametrize("problem", ["missing-arm64", "wrong-arm64-archive", "extra-platform"])
def test_publisher_asset_gate_rejects_incomplete_or_incorrect_matrices(problem):
    tree = ast.parse(publisher_asset_script())
    expected = dict(EXPECTED)
    if problem == "missing-arm64":
        expected.pop("win-arm64")
    elif problem == "wrong-arm64-archive":
        expected["win-arm64"] = "chromix-win-x64.zip"
    else:
        expected["unsupported"] = "chromix-unsupported.zip"
    expected_assignment(tree).value = ast.parse(repr(expected), mode="eval").body
    result = run_gate(ast.unparse(tree))
    assert result.returncode != 0
    assert "Python browser assets mismatch" in result.stderr

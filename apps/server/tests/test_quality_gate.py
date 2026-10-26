# Copyright (c) 2026 Hans Valerie
"""Prove the actual aggregate workflow fails on incomplete prerequisite results."""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def test_gate_depends_on_every_other_quality_job() -> None:
    workflow = Path(__file__).resolve().parents[3] / ".github/workflows/quality.yml"
    jobs = workflow.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    job_names = set(re.findall(r"(?m)^  ([\w-]+):$", jobs))
    gate = jobs.split("  quality-gate:", 1)[1]
    needs = gate.split("    needs:\n", 1)[1].split("    runs-on:", 1)[0]
    assert set(re.findall(r"(?m)^      - ([\w-]+)$", needs)) == job_names - {
        "quality-gate"
    }


@pytest.mark.parametrize(
    ("results", "status"),
    [
        ({"lint": {"result": "success"}, "tests": {"result": "success"}}, 0),
        ({"lint": {"result": "success"}, "tests": {"result": "failure"}}, 1),
        ({"tests": {"result": "cancelled"}}, 1),
        ({"tests": {"result": "skipped"}}, 1),
        ({"tests": {}}, 1),
        ({}, 1),
    ],
)
def test_aggregate_job_enforces_success(
    results: dict[str, dict[str, str]], status: int
) -> None:
    workflow = Path(__file__).resolve().parents[3] / ".github/workflows/quality.yml"
    gate = workflow.read_text(encoding="utf-8").split("  quality-gate:", 1)[1]
    match = re.search(r"(?ms)^          python3 - <<'PY'\n(.*?)^          PY$", gate)
    assert match is not None
    script = textwrap.dedent(match.group(1))
    result = subprocess.run(  # noqa: S603 -- Trusted checked-in workflow, no shell.
        [sys.executable, "-c", script],
        env={**os.environ, "REQUIRED_JOB_RESULTS": json.dumps(results)},
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == status

# Copyright (c) 2026 Hans Valerie
"""Operator-local fixed-N execution, separate from scientific benchmarking."""

from tachyron.execution.models import LOCAL_LIMITS, ExecutionResult
from tachyron.execution.runner import run_local
from tachyron.execution.storage import inspect_execution

__all__ = ["LOCAL_LIMITS", "ExecutionResult", "inspect_execution", "run_local"]

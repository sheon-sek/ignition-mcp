"""D29 confines the Jython 2.7.4 process to repository-controlled fixtures.

The handler is already fixed to the repository. The fixture is the other input
that crosses into the vulnerable interpreter, so it must come from the committed
fixture directory, must stay within a bound, and must be rejected before Java
starts.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pytest

from tooling.native.jython_runner import runner
from tooling.native.jython_runner.runner import JythonRunnerError, run_recorded_tool

FIXTURES = Path(runner.__file__).resolve().parent / "fixtures"
COMMITTED = FIXTURES / "tag_query-full-path-continuation.json"


def _refuse_java() -> str:
    raise AssertionError("Java must not start for a rejected fixture")


class FixtureBoundaryTest(unittest.TestCase):
    def test_fixture_outside_the_fixture_directory_is_rejected_before_java(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            untrusted = Path(temporary) / "untrusted.json"
            untrusted.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
            with mock.patch.object(runner, "_java_executable", _refuse_java):
                with pytest.raises(JythonRunnerError) as caught:
                    run_recorded_tool("tag_query", untrusted)
        assert str(FIXTURES) in str(caught.value)
        assert "untrusted.json" in str(caught.value)

    def test_symlink_out_of_the_fixture_directory_is_rejected(self) -> None:
        escaping = FIXTURES / ".escape-boundary-test.json"
        with tempfile.TemporaryDirectory() as temporary:
            untrusted = Path(temporary) / "untrusted.json"
            untrusted.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
            escaping.symlink_to(untrusted)
            try:
                with mock.patch.object(runner, "_java_executable", _refuse_java):
                    with pytest.raises(JythonRunnerError) as caught:
                        run_recorded_tool("tag_query", escaping)
            finally:
                escaping.unlink()
        assert str(FIXTURES) in str(caught.value)

    def test_fixture_over_the_size_limit_is_rejected_before_java(self) -> None:
        oversized = FIXTURES / ".oversize-boundary-test.json"
        oversized.write_bytes(b"x" * (runner.MAX_FIXTURE_BYTES + 1))
        try:
            with mock.patch.object(runner, "_java_executable", _refuse_java):
                with pytest.raises(JythonRunnerError) as caught:
                    run_recorded_tool("tag_query", oversized)
        finally:
            oversized.unlink()
        assert str(runner.MAX_FIXTURE_BYTES) in str(caught.value)

    def test_missing_fixture_inside_the_directory_is_rejected(self) -> None:
        with mock.patch.object(runner, "_java_executable", _refuse_java):
            with pytest.raises(JythonRunnerError) as caught:
                run_recorded_tool("tag_query", FIXTURES / "absent.json")
        assert "absent.json" in str(caught.value)

    def test_committed_fixture_is_inside_the_boundary(self) -> None:
        # The positive control: the real fixture passes the same checks the
        # rejected paths fail, so the boundary is not rejecting everything.
        size = COMMITTED.stat().st_size
        assert size <= runner.MAX_FIXTURE_BYTES
        assert COMMITTED.resolve().is_relative_to(FIXTURES)

"""Run blocks are the unit of shell checking, so extraction must be exact.

Two historical breakages are reconstructed as fixtures:

* ``reconstructed-missing-fi.workflow.yml`` — ``db8c702`` shipped the Refresh
  diagnostics step with the outer ``fi`` clobbered (failed runs 35592969557,
  35593736110, 35594408385).
* ``reconstructed-runner-temp-env.workflow.yml`` — ``a1ada74`` referenced
  ``runner.temp`` in a job-level ``env`` (failed run 35586649945).
* ``reconstructed-quoted-run-key.workflow.yml`` — the same missing ``fi`` behind
  the YAML quoted (``"run":``, ``'run':``) and explicit (``? run``) key forms,
  which a plain key extractor silently skips.
* ``reconstructed-escaped-run-key.workflow.yml`` — the same missing ``fi`` behind
  ``"r\\u0075n":``, a quoted key whose YAML escape spells ``run``; matching the
  raw key text rather than the decoded key skips that block too.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tooling.ci import actionlint
from tooling.ci.check_workflows import (
    WorkflowCheckError,
    extract_run_blocks,
    main,
    shell_syntax_findings,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parents[3]

MIXED_FORMS = """\
name: Mixed run forms
on: workflow_dispatch
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Inline step
        run: uv lock --check
      - name: Block step
        shell: bash
        run: |
          set -euo pipefail
          if [[ -n "$x" ]]; then
            echo "$x"

          fi
      - name: Folded step
        run: >
          echo one
          && echo two
      - name: Quoted step
        shell: pwsh
        run: "Write-Host 'hello'"
"""


def _write(text: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "workflow.yml"
    path.write_text(text, encoding="utf-8")
    return path


def _block_line_span(workflow: Path, index: int = 0) -> range:
    """The workflow lines run block ``index`` occupies, end-exclusive.

    Fixtures break the block they exist for, which is block 0 for every one of
    them. Assertions are phrased against this span rather than a literal line
    because the reported line legitimately moves with the bash release: 5.3
    names the construct whose terminator is missing, 5.2 only the line where the
    parse ran out of input.
    """
    block = extract_run_blocks(workflow)[index]
    return range(block.first_line, block.first_line + len(block.script.splitlines()))


def _reported_line(output: str, workflow: str) -> int:
    """The workflow line the CLI report carries for ``workflow``.

    The check prints the path it was handed, which for the CLI tests is a
    temporary directory, so the file name is matched at a path boundary.
    """
    match = re.search(rf"(?:^|/){re.escape(workflow)}:(?P<line>\d+): ", output)
    if match is None:
        raise AssertionError(f"no finding reported for {workflow} in {output!r}")
    return int(match.group("line"))


class ExtractRunBlocksTest(unittest.TestCase):
    def test_finds_every_form_with_step_name_shell_and_line(self) -> None:
        blocks = extract_run_blocks(_write(MIXED_FORMS))
        self.assertEqual([b.step for b in blocks], ["Inline step", "Block step", "Folded step", "Quoted step"])
        self.assertEqual([b.shell for b in blocks], ["", "bash", "", "pwsh"])
        self.assertEqual(blocks[0].script, "uv lock --check")
        self.assertEqual(blocks[1].script, 'set -euo pipefail\nif [[ -n "$x" ]]; then\n  echo "$x"\n\nfi')
        self.assertEqual(blocks[2].script, "echo one && echo two")
        self.assertEqual(blocks[3].script, "Write-Host 'hello'")

    def test_reports_first_script_line_of_each_block(self) -> None:
        blocks = extract_run_blocks(_write(MIXED_FORMS))
        # 8 is the inline `run:` line itself; 12 and 19 are the first body lines
        # of the literal and folded blocks; 23 is the quoted inline value.
        self.assertEqual([b.first_line for b in blocks], [8, 12, 19, 23])

    def test_missing_fi_reconstruction_is_one_block_ending_at_the_next_step(self) -> None:
        blocks = extract_run_blocks(FIXTURES / "reconstructed-missing-fi.workflow.yml")
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block.step, "Refresh diagnostics")
        self.assertEqual(block.shell, "bash")
        self.assertEqual(block.first_line, 15)
        self.assertEqual(block.script.splitlines()[0], 'mkdir -p "$EVIDENCE_DIR"')
        self.assertTrue(block.script.rstrip().endswith("the file was always empty)"))
        self.assertNotIn("Upload G3 evidence", block.script)


class ShellSyntaxTest(unittest.TestCase):
    def test_missing_fi_reconstruction_is_reported_with_step_and_workflow_line(self) -> None:
        fixture = FIXTURES / "reconstructed-missing-fi.workflow.yml"
        findings = shell_syntax_findings(fixture)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.path, fixture)
        self.assertIn(finding.line, _block_line_span(fixture))
        self.assertIn("unexpected end of file", finding.message)
        self.assertIn("Refresh diagnostics", finding.message)

    def test_corrected_block_passes(self) -> None:
        corrected = FIXTURES / "reconstructed-missing-fi.workflow.yml"
        text = corrected.read_text(encoding="utf-8").replace(
            "            fi\n          # metrics scraped",
            "            fi\n          fi\n          # metrics scraped",
        )
        self.assertNotEqual(text, corrected.read_text(encoding="utf-8"))
        self.assertEqual(shell_syntax_findings(_write(text)), [])

    def test_non_bash_steps_are_not_shell_checked(self) -> None:
        script = 'if (-not $path) { throw "missing" }\n'
        pwsh = _write(
            "name: pwsh step\non: workflow_dispatch\njobs:\n  b:\n    runs-on: windows-latest\n    steps:\n"
            "      - name: Windows only\n        shell: pwsh\n        run: |\n          " + script
        )
        bash = _write(
            "name: bash step\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - name: Same body\n        shell: bash\n        run: |\n          " + script
        )
        self.assertEqual(extract_run_blocks(pwsh)[0].shell, "pwsh")
        self.assertEqual(shell_syntax_findings(pwsh), [])
        self.assertEqual(len(shell_syntax_findings(bash)), 1)

    def test_expressions_are_not_shell_syntax_errors(self) -> None:
        path = _write(
            "name: expressions\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - name: Expressions\n        shell: bash\n        run: |\n"
            "          set -euo pipefail\n"
            "          if [[ \"${{ matrix.gateway_version }}\" == \"8.3.8\" ]]; then\n"
            "            echo \"run ${{ github.run_id }} of ${{ needs.gate.x.outputs.y || 'fallback' }}\"\n"
            "          fi\n"
            "          echo ${{ format('{0}:{1}', github.ref, matrix.gateway_version) }}\n"
            "          exit ${{ github.ref == 'refs/heads/main' && '0' || '1' }}\n"
        )
        self.assertEqual(shell_syntax_findings(path), [])

    def test_repository_workflows_have_no_shell_syntax_errors(self) -> None:
        workflows = sorted((REPO / ".github/workflows").glob("*.yml"))
        self.assertEqual(len(workflows), 5)
        for workflow in workflows:
            self.assertEqual(shell_syntax_findings(workflow), [], workflow.name)


#: ``bash -n -c`` stderr recorded verbatim for the block the missing-``fi``
#: fixture carries (workflow lines 15-32, script lines 1-18). bash 5.3 also names
#: the construct whose terminator is missing (``on line 5``); bash 5.2 does not.
#: These two strings are the whole input to the message-to-workflow-line mapping,
#: so both shapes are exercised on every machine rather than only the one the
#: installed bash happens to print.
BASH_5_3_STDERR = "bash: -c: line 19: syntax error: unexpected end of file from `if' command on line 5\n"
BASH_5_2_STDERR = "bash: -c: line 19: syntax error: unexpected end of file\n"


class ShellErrorMessageMappingTest(unittest.TestCase):
    """Both recorded ``bash -n`` message shapes map onto the broken ``run:`` block.

    bash 5.3 (this workstation) reports where the unterminated construct starts;
    bash 5.2 (the GitHub ``ubuntu-24.04`` runner) reports only the line where the
    parse ran out of input. The finding must name a line inside the block either
    way, so the bash release changes how precise the report is, never whether the
    broken block is reported or which block it is attributed to.
    """

    def _finding_line(self, recorded: str) -> int:
        fixture = FIXTURES / "reconstructed-missing-fi.workflow.yml"
        with tempfile.TemporaryDirectory() as temporary:
            stub = Path(temporary) / "bash"
            stub.write_text(f"#!/bin/sh\ncat >&2 <<'STDERR'\n{recorded}STDERR\nexit 2\n", encoding="utf-8")
            stub.chmod(0o755)
            findings = shell_syntax_findings(fixture, bash=str(stub))
        self.assertEqual(len(findings), 1)
        return findings[0].line

    def test_construct_line_is_used_when_bash_names_it(self) -> None:
        # Line 19 is the `if [[ -n "$cid" ]]` that lost its `fi`, not the end of
        # the block where the parse gave up.
        self.assertEqual(self._finding_line(BASH_5_3_STDERR), 19)

    def test_end_of_file_line_is_used_when_bash_names_no_construct(self) -> None:
        # Line 19 of the input is one past the block's last script line, 32,
        # because bash counts the script's missing final newline. The finding has
        # to name a line the block occupies, not the step that follows it.
        self.assertEqual(self._finding_line(BASH_5_2_STDERR), 32)


class QuotedKeyTest(unittest.TestCase):
    """The key forms a YAML parser accepts but a bare-key regex does not."""

    def test_quoted_and_explicit_run_keys_are_extracted(self) -> None:
        blocks = extract_run_blocks(FIXTURES / "reconstructed-quoted-run-key.workflow.yml")
        self.assertEqual([b.script.splitlines()[0] for b in blocks], [
            'mkdir -p "$EVIDENCE_DIR"',
            "set -euo pipefail",
            'echo "explicit key"',
        ])
        self.assertEqual([b.step for b in blocks], ["", "Single quoted key", "Explicit key form"])
        self.assertEqual([b.shell for b in blocks], ["", "bash", "bash"])
        self.assertEqual([b.first_line for b in blocks], [8, 15, 21])

    def test_unclosed_construct_under_a_quoted_key_is_reported(self) -> None:
        fixture = FIXTURES / "reconstructed-quoted-run-key.workflow.yml"
        findings = shell_syntax_findings(fixture)
        self.assertEqual(len(findings), 1)
        self.assertIn(findings[0].line, _block_line_span(fixture))
        self.assertIn("unexpected end of file", findings[0].message)

    def test_flow_mapping_run_key_fails_loudly_instead_of_being_skipped(self) -> None:
        path = _write(
            "name: flow\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - {name: Flow step, run: "echo hi"}\n'
        )
        with self.assertRaises(WorkflowCheckError) as caught:
            extract_run_blocks(path)
        self.assertIn("run", str(caught.exception))
        self.assertIn(path.name, str(caught.exception))

    def test_flow_value_containing_a_run_colon_is_not_a_run_key(self) -> None:
        # Only keys count: a `run:` inside a quoted flow value must not turn a
        # valid workflow into a loud failure.
        path = _write(
            "name: flow value\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - {name: "run: something", shell: bash}\n'
        )
        self.assertEqual(extract_run_blocks(path), [])


class EscapedKeyTest(unittest.TestCase):
    """A quoted key is the scalar it decodes to, escapes included."""

    def test_escaped_double_quoted_run_key_is_extracted(self) -> None:
        # YAML `"r\u0075n"` is the key `run`; the raw key text is not.
        path = _write(
            "name: escaped\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - name: Escaped key step\n"
            "        shell: bash\n"
            '        "r\\u0075n": |\n'
            "          set -euo pipefail\n"
            "          echo hi\n"
        )
        blocks = extract_run_blocks(path)
        self.assertEqual([b.step for b in blocks], ["Escaped key step"])
        self.assertEqual([b.shell for b in blocks], ["bash"])
        self.assertEqual([b.first_line for b in blocks], [10])
        self.assertEqual(blocks[0].script, "set -euo pipefail\necho hi")

    def test_wide_sequence_gap_under_an_escaped_key_is_extracted(self) -> None:
        path = _write(
            "name: wide\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      -   "r\\u0075n": |\n'
            "            set -euo pipefail\n"
            "            echo hi\n"
        )
        self.assertEqual([b.script for b in extract_run_blocks(path)], ["set -euo pipefail\necho hi"])

    def test_unclosed_construct_under_an_escaped_key_is_reported(self) -> None:
        fixture = FIXTURES / "reconstructed-escaped-run-key.workflow.yml"
        findings = shell_syntax_findings(fixture)
        self.assertEqual(len(findings), 1)
        self.assertIn(findings[0].line, _block_line_span(fixture))
        self.assertIn("unexpected end of file", findings[0].message)
        self.assertIn("Escaped key step", findings[0].message)

    def test_quoted_key_with_an_undecodable_escape_fails_loudly(self) -> None:
        # `\q` is not a YAML escape, so the key cannot be decoded: the block it
        # covers must not be silently skipped instead.
        path = _write(
            "name: bad escape\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - "run\\q": |\n'
            "          echo hi\n"
        )
        with self.assertRaises(WorkflowCheckError) as caught:
            extract_run_blocks(path)
        self.assertIn("\\q", str(caught.exception))
        self.assertIn(path.name, str(caught.exception))

    def test_multiline_quoted_key_fails_loudly_instead_of_being_skipped(self) -> None:
        # An escaped line break inside a quoted scalar is the one escape this
        # extractor cannot decode from a single line.
        path = _write(
            "name: multiline key\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - "r\\\n'
            'un": |\n'
            "          echo hi\n"
        )
        with self.assertRaises(WorkflowCheckError) as caught:
            extract_run_blocks(path)
        self.assertIn(f"{path.name}:7", str(caught.exception))

    def test_escaped_flow_mapping_run_key_fails_loudly(self) -> None:
        path = _write(
            "name: escaped flow\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - {"r\\u0075n": "echo hi"}\n'
        )
        with self.assertRaises(WorkflowCheckError) as caught:
            extract_run_blocks(path)
        self.assertIn("unsupported run: key form", str(caught.exception))
        self.assertIn(path.name, str(caught.exception))

    def test_single_quoted_scalar_doubled_quote_is_decoded(self) -> None:
        path = _write(
            "name: single quoted\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - name: 'Don''t skip'\n"
            "        shell: 'bash'\n"
            "        run: |\n"
            "          echo hi\n"
        )
        blocks = extract_run_blocks(path)
        self.assertEqual([b.step for b in blocks], ["Don't skip"])
        self.assertEqual([b.shell for b in blocks], ["bash"])


class CommandLineTest(unittest.TestCase):
    """The seam a developer and CI both run: exit code plus the printed report."""

    def _stub(self, directory: Path, body: str = "exit 0") -> Path:
        path = directory / "actionlint"
        path.write_text(
            f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo {actionlint.VERSION}; exit 0; fi\n{body}\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def _run(self, workflow_dir: Path, binary: Path) -> tuple[int, str]:
        stream = io.StringIO()
        with (
            mock.patch.dict(os.environ, {actionlint.OVERRIDE_ENV: "1"}),
            contextlib.redirect_stdout(stream),
            contextlib.redirect_stderr(stream),
        ):
            code = main(["--workflow-dir", str(workflow_dir), "--actionlint", str(binary)])
        return code, stream.getvalue()

    def test_clean_workflow_directory_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            (workflow_dir / "clean.yml").write_text(
                "name: clean\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: echo hi\n",
                encoding="utf-8",
            )
            code, output = self._run(workflow_dir, stub)
        self.assertEqual(code, 0)
        self.assertIn("OK: 1 workflow file(s)", output)

    def test_missing_fi_reconstruction_fails_with_step_and_line(self) -> None:
        fixture = FIXTURES / "reconstructed-missing-fi.workflow.yml"
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            shutil.copy(fixture, workflow_dir / "g3.yml")
            code, output = self._run(workflow_dir, stub)
        self.assertEqual(code, 1)
        self.assertIn('step "Refresh diagnostics": syntax error: unexpected end of file', output)
        self.assertIn(_reported_line(output, "g3.yml"), _block_line_span(fixture))

    def test_actionlint_diagnostic_is_reported_verbatim(self) -> None:
        recorded = (FIXTURES / "actionlint-runner-temp-output.txt").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary), f"cat <<'RECORDED'\n{recorded}RECORDED\nexit 1")
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            shutil.copy(FIXTURES / "reconstructed-runner-temp-env.workflow.yml", workflow_dir / "g3.yml")
            code, output = self._run(workflow_dir, stub)
        self.assertEqual(code, 1)
        self.assertIn("context \"runner\" is not allowed here", output)
        self.assertIn("[expression]", output)

    def test_empty_workflow_directory_is_a_loud_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            code, output = self._run(workflow_dir, stub)
        self.assertEqual(code, 2)
        self.assertIn("no workflow files", output)

    def test_no_op_actionlint_override_cannot_pass(self) -> None:
        # The reviewed bypass: `--actionlint /bin/true` used to exit 0 and print OK.
        with tempfile.TemporaryDirectory() as temporary:
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            shutil.copy(FIXTURES / "reconstructed-missing-fi.workflow.yml", workflow_dir / "g3.yml")
            code, output = self._run(workflow_dir, Path("/bin/true"))
        self.assertEqual(code, 2)
        self.assertIn("does not identify as actionlint", output)

    def test_override_without_the_test_opt_in_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            (workflow_dir / "clean.yml").write_text(
                "name: clean\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: echo hi\n",
                encoding="utf-8",
            )
            stream = io.StringIO()
            with (
                mock.patch.dict(os.environ),
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(stream),
            ):
                os.environ.pop(actionlint.OVERRIDE_ENV, None)
                code = main(["--workflow-dir", str(workflow_dir), "--actionlint", str(stub)])
            output = stream.getvalue()
        self.assertEqual(code, 2)
        self.assertIn(actionlint.OVERRIDE_ENV, output)

    def test_quoted_run_key_unclosed_construct_fails_the_check(self) -> None:
        fixture = FIXTURES / "reconstructed-quoted-run-key.workflow.yml"
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            shutil.copy(fixture, workflow_dir / "quoted.yml")
            code, output = self._run(workflow_dir, stub)
        self.assertEqual(code, 1)
        self.assertIn("unexpected end of file", output)
        self.assertIn(_reported_line(output, "quoted.yml"), _block_line_span(fixture))

    def test_escaped_run_key_unclosed_construct_fails_the_check(self) -> None:
        # The reviewed bypass through the same seam the developer runs: 0 blocks
        # were extracted under `"r\u0075n": |`, so the unclosed `if` printed "OK".
        fixture = FIXTURES / "reconstructed-escaped-run-key.workflow.yml"
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            shutil.copy(fixture, workflow_dir / "escaped.yml")
            code, output = self._run(workflow_dir, stub)
        self.assertEqual(code, 1)
        self.assertIn("unexpected end of file", output)
        self.assertIn(_reported_line(output, "escaped.yml"), _block_line_span(fixture))

    def test_unparseable_run_key_form_fails_the_check_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            (workflow_dir / "flow.yml").write_text(
                "name: flow\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
                '      - {name: Flow step, run: "echo hi"}\n',
                encoding="utf-8",
            )
            code, output = self._run(workflow_dir, stub)
        self.assertEqual(code, 2)
        self.assertIn("unsupported run: key form", output)

    def test_missing_actionlint_binary_is_a_loud_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            (workflow_dir / "clean.yml").write_text(
                "name: clean\non: workflow_dispatch\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: echo hi\n",
                encoding="utf-8",
            )
            code, output = self._run(workflow_dir, Path(temporary) / "absent-actionlint")
        self.assertEqual(code, 2)
        self.assertIn("absent-actionlint", output)


    def test_module_entry_point_runs_the_check(self) -> None:
        # `python -m tooling.ci.check_workflows` is the documented command; a
        # module without an entry point would exit 0 having checked nothing.
        fixture = FIXTURES / "reconstructed-missing-fi.workflow.yml"
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary))
            workflow_dir = Path(temporary) / "workflows"
            workflow_dir.mkdir()
            shutil.copy(fixture, workflow_dir / "g3.yml")
            completed = subprocess.run(
                [sys.executable, "-m", "tooling.ci.check_workflows", "--workflow-dir", str(workflow_dir),
                 "--actionlint", str(stub)],
                capture_output=True,
                text=True,
                cwd=REPO,
                env={**os.environ, "PYTHONPATH": str(REPO), actionlint.OVERRIDE_ENV: "1"},
                check=False,
            )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("Refresh diagnostics", completed.stdout)
        self.assertIn("unexpected end of file", completed.stdout)
        self.assertIn(_reported_line(completed.stdout, "g3.yml"), _block_line_span(fixture))


#: A stand-in for a shellcheck a runner has on ``PATH``: actionlint parses this
#: JSON and reports each entry as a finding in the run block it was run against.
SHELLCHECK_STUB = (
    "#!/bin/sh\n"
    'echo \'[{"file":"-","line":3,"endLine":3,"column":11,"endColumn":14,"level":"error",'
    '"code":2046,"message":"Double quote to prevent globbing and word splitting.","fix":null}]\'\n'
    "exit 1\n"
)


class PinnedActionlintMachineIndependenceTest(unittest.TestCase):
    """The pinned check must report the same result on every machine.

    actionlint runs shellcheck and pyflakes itself when it finds them on
    ``PATH``, so the same command failed in GitHub CI (run 35626729044,
    ``SC2046`` in ``phase3-live-g3.yml``) that passed on a workstation without
    shellcheck. This runs the real pinned binary over the repository's own
    workflows, with a shellcheck on ``PATH`` that reports an error for every
    script it is handed.
    """

    def _run(self, path: str) -> tuple[int, str]:
        stream = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"PATH": path}),
            contextlib.redirect_stdout(stream),
            contextlib.redirect_stderr(stream),
        ):
            code = main(["--workflow-dir", str(REPO / ".github" / "workflows")])
        return code, stream.getvalue()

    def test_shellcheck_on_path_cannot_change_the_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            stub = directory / "shellcheck"
            stub.write_text(SHELLCHECK_STUB, encoding="utf-8")
            stub.chmod(0o755)
            with_stub = f"{directory}{os.pathsep}{os.environ['PATH']}"
            # The stub must be the shellcheck PATH resolves, or this test would
            # pass without exercising the integration at all.
            self.assertEqual(shutil.which("shellcheck", path=with_stub), str(stub))
            found, with_shellcheck = self._run(with_stub)
            _, without_shellcheck = self._run(os.environ["PATH"])
        self.assertEqual(found, 0, with_shellcheck)
        self.assertEqual(with_shellcheck, without_shellcheck)
        self.assertIn("OK:", with_shellcheck)


if __name__ == "__main__":
    unittest.main()

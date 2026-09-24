"""Pre-push check for the GitHub Actions workflows: shell syntax and actionlint.

Two independent checks over ``.github/workflows``:

* ``bash -n`` over every ``run:`` block, so a shell syntax error cannot burn a
  live-Gateway run again (failed runs 35592969557, 35593736110, 35594408385 —
  a missing ``fi``). bash 5.3 names the construct whose terminator is missing as
  well as where the parse stopped; bash 5.2 names only the latter, one line past
  the script. A finding uses the construct line when bash reports one and the end
  of the block otherwise, so the reported line is a line the block occupies on
  either release — the GitHub ``ubuntu-24.04`` runner ships 5.2.
* actionlint over the workflow files, so an invalid context, expression or
  schema reference is caught before the push (failed run 35586649945 —
  ``${{ runner.temp }}`` in a job-level ``env``).

actionlint is invoked with its shellcheck and pyflakes integrations off
(:data:`tooling.ci.actionlint.DISABLED_INTEGRATIONS`), so neither check depends
on which external linters the calling machine happens to have installed: the
same commit reported ``SC2046`` in CI (run 35626729044) that passed on a
workstation without shellcheck.

Both run locally and in CI from this same module, so the pre-push loop and the
pipeline cannot disagree.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys

from tooling.ci import actionlint

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW_GLOBS = ("*.yml", "*.yaml")

#: A mapping-key token: a YAML quoted scalar, or a plain scalar. Quoted keys are
#: valid YAML and actionlint parses them, so a bare-word key regex silently
#: missing ``- "run": |`` would leave that block shell-unchecked.
KEY_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\'|[A-Za-z_][A-Za-z0-9_.-]*')
#: A ``run`` key this extractor did not parse, in block position. It is a loud
#: failure rather than a skip: the block would never reach ``bash -n``.
UNPARSED_RUN_KEY = re.compile(r'^[ \t]*(?:-[ \t]+)?(?:\?[ \t]*)?(?P<quote>["\']?)run(?P=quote)[ \t]*:')
#: A line whose entry opens a one-line flow mapping, e.g. ``- {run: echo hi}``.
FLOW_START = re.compile(r"^[ \t]*(?:-[ \t]+)?(?:\?[ \t]*)?\{")
#: A sequence dash and the whitespace after it, so ``-   run:`` indents like ``- run:``.
SEQUENCE_PREFIX = re.compile(r"^-[ \t]+")
#: A block scalar header, e.g. ``|``, ``|-``, ``>2``.
BLOCK_HEADER = re.compile(r"^[|>][+-]?[0-9]?$")
STEP_NAME = re.compile(r"^[ ]*-[ ]+name:[ ]*(?P<name>.*?)[ ]*$")
SHELL_KEY = re.compile(r"^(?P<indent>[ ]*)shell:[ ]*(?P<shell>.*?)[ ]*$")

#: The escape table of a YAML double-quoted scalar. ``\N``, ``\_``, ``\L`` and
#: ``\P`` are the Unicode break and space characters, not ASCII escapes.
ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}
#: ``\x41``, ``\u0041`` and ``\U00000041``, and how many hex digits each takes.
CODE_ESCAPES = {"x": 2, "u": 4, "U": 8}
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class Entry:
    """One YAML mapping entry: its key, inline value, and where those live.

    ``indent`` is the column of the key itself (for a ``- key: value`` sequence
    entry that is the dash column); ``value_line`` is the 0-based line that
    carries the value, which differs from the key line only for the explicit
    ``? key`` form.
    """

    indent: int
    name: str
    value: str
    value_line: int



@dataclass(frozen=True)
class RunBlock:
    """One ``run:`` block, ready to hand to a shell.

    ``script`` is the dedented script text; ``first_line`` is the 1-based line of
    script line 1 in the workflow file, so shell errors map back to the workflow.
    """

    path: Path
    step: str
    shell: str
    script: str
    first_line: int


@dataclass(frozen=True)
class Finding:
    """One problem, addressed at the workflow file and line that carries it."""

    path: Path
    line: int
    message: str


#: ``bash`` reports ``line N: ...`` for where the parse stopped and, since 5.3,
#: ``on line N`` for where an unterminated construct starts. The second is the
#: actionable one: it points at the ``if`` whose ``fi`` went missing. 5.2 prints
#: only the first, which for an unterminated construct is one past the script's
#: last line; :func:`_error_line` maps either onto the block.
CONSTRUCT_LINE = re.compile(r"on line (?P<line>\d+)")
ERROR_LINE = re.compile(r"line (?P<line>\d+)")
BASH_C_PREFIX = re.compile(r"^\S+: -c: ")
ERROR_LINE_PREFIX = re.compile(r"^line \d+: ")
CONSTRUCT_LINE_TAIL = re.compile(r" on line \d+")


def shell_syntax_findings(workflow: Path, *, bash: str = "bash") -> list[Finding]:
    """``bash -n`` over every shell ``run:`` block in one workflow file.

    Steps that declare a non-bash shell are not shell scripts and are left to
    the shell they declare.
    """
    findings: list[Finding] = []
    for block in extract_run_blocks(workflow):
        if block.shell and not block.shell.startswith("bash"):
            continue
        finding = _bash_syntax_finding(block, bash)
        if finding is not None:
            findings.append(finding)
    return findings


def _bash_syntax_finding(block: RunBlock, bash: str) -> Finding | None:
    result = subprocess.run([bash, "-n", "-c", block.script], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return None
    raw = BASH_C_PREFIX.sub("", " ".join(result.stderr.split()))
    line = _error_line(raw, block)
    # Both numbers bash prints are script lines. The Finding carries the workflow
    # line, so the message keeps only the description of the broken construct.
    detail = CONSTRUCT_LINE_TAIL.sub("", ERROR_LINE_PREFIX.sub("", raw))
    if not detail:
        detail = f"{bash} exited {result.returncode} without a diagnostic"
    step = f'step "{block.step}": ' if block.step else ""
    return Finding(block.path, line, f"{step}{detail}")


def _error_line(detail: str, block: RunBlock) -> int:
    """The workflow line a ``bash -n`` diagnostic belongs to.

    Both numbers bash prints are script lines, and which one it prints depends
    on its release. bash 5.3 reports the construct whose terminator is missing
    (``unexpected end of file from `if' command on line 5``); that line is the
    actionable one, so it wins. bash 5.2 reports only where the parse ran out of
    input, one past the block's last script line because the script it is handed
    has no final newline; that line is clamped onto the block, so the finding
    names a line the block occupies instead of the step after it. A message with
    neither number is reported at the block's first line.
    """
    match = CONSTRUCT_LINE.search(detail) or ERROR_LINE.search(detail)
    if match is None:
        return block.first_line
    line = block.first_line + int(match.group("line")) - 1
    last_line = block.first_line + max(len(block.script.splitlines()), 1) - 1
    return min(max(line, block.first_line), last_line)


class WorkflowCheckError(RuntimeError):
    """The check could not be run at all."""


class _UndecodableScalar(WorkflowCheckError):
    """A quoted scalar whose YAML escapes this extractor cannot decode.

    It is a :class:`WorkflowCheckError` so it is loud wherever it surfaces, and
    it is caught where the workflow line is known to name that line too.
    """


def workflow_files(directory: Path) -> list[Path]:
    """Every workflow file in ``directory``; an empty directory is an error."""
    if not directory.is_dir():
        raise WorkflowCheckError(f"{directory} is not a directory")
    files = sorted(path for pattern in WORKFLOW_GLOBS for path in directory.glob(pattern))
    if not files:
        raise WorkflowCheckError(f"{directory} contains no workflow files")
    return files


def actionlint_findings(files: Sequence[Path], *, binary: Path) -> list[Finding]:
    """actionlint's diagnostics for ``files``, as :class:`Finding` rows."""
    return [
        Finding(Path(diagnostic.path), diagnostic.line, diagnostic.message)
        for diagnostic in actionlint.run_actionlint(binary, files)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 when clean, 1 on findings, 2 when the check could not run."""
    parser = argparse.ArgumentParser(description="Lint GitHub workflows and their run: blocks.")
    parser.add_argument("--workflow-dir", type=Path, default=DEFAULT_WORKFLOW_DIR)
    parser.add_argument(
        "--actionlint",
        type=Path,
        default=None,
        help=f"test-only actionlint binary; requires {actionlint.OVERRIDE_ENV}=1",
    )
    args = parser.parse_args(argv)
    try:
        bash = shutil.which("bash")
        if bash is None:
            raise WorkflowCheckError(
                "bash is not on PATH; this check runs `bash -n` over every run: block. "
                "On Windows run it under WSL or Git Bash, or leave it to CI"
            )
        files = workflow_files(args.workflow_dir)
        binary = actionlint.resolve_binary(args.actionlint)
        findings = actionlint_findings(files, binary=binary)
        blocks = [block for path in files for block in extract_run_blocks(path)]
        findings.extend(finding for path in files for finding in shell_syntax_findings(path, bash=bash))
    except (WorkflowCheckError, actionlint.ActionlintError, OSError) as error:
        print(f"workflow check failed: {error}", file=sys.stderr)
        return 2
    for finding in sorted(findings, key=lambda item: (str(item.path), item.line, item.message)):
        print(f"{finding.path}:{finding.line}: {finding.message}")
    skipped = [block for block in blocks if block.shell and not block.shell.startswith("bash")]
    if skipped:
        print(f"note: {len(skipped)} run block(s) declared a non-bash shell and were not shell-checked")
    if findings:
        print(f"{len(findings)} problem(s) in {len(files)} workflow file(s)", file=sys.stderr)
        return 1
    print(f"OK: {len(files)} workflow file(s), {len(blocks)} run block(s)")
    return 0


def extract_run_blocks(path: Path) -> list[RunBlock]:
    """Every ``run:`` script in one workflow file, in file order.

    Keys are parsed as YAML mapping keys, so the quoted (``"run":``, ``'run':``)
    and explicit (``? run``) forms reach ``bash -n`` too. A ``run`` key in a form
    the extractor cannot parse raises instead of being skipped, because a skipped
    block is a green check that proves nothing.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[RunBlock] = []
    index = 0
    try:
        while index < len(lines):
            entry = _entry(lines, index)
            if entry is None:
                _reject_unparsed_run_key(path, index, lines[index])
                index += 1
                continue
            if not BLOCK_HEADER.match(entry.value):
                if entry.name == "run":
                    step, shell = _step_context(lines, index, entry.indent, entry.value_line + 1)
                    blocks.append(RunBlock(path, step, shell, _unquote(entry.value), entry.value_line + 1))
                index = entry.value_line + 1
                continue
            body, cursor = _block_body(lines, entry.value_line + 1, entry.indent)
            if entry.name == "run":
                step, shell = _step_context(lines, index, entry.indent, cursor)
                script, first_line = _block_script(body, entry.value_line + 2, folded=entry.value.startswith(">"))
                blocks.append(RunBlock(path, step, shell, script, first_line))
            index = cursor
    except _UndecodableScalar as error:
        # ``index`` is the line being read, which is where the scalar starts.
        raise WorkflowCheckError(f"{path}:{index + 1}: {error}") from error
    return blocks


def _reject_unparsed_run_key(path: Path, index: int, line: str) -> None:
    """A ``run`` key this extractor could not parse is louder than a silent skip."""
    _, text = _entry_prefix(line)
    reason: str | None = None
    if UNPARSED_RUN_KEY.match(line):
        reason = "unsupported run: key form"
    elif FLOW_START.match(line):
        keys = _flow_keys(line)
        if keys is None:
            reason = "a flow-mapping key cannot be decoded"
        elif "run" in keys:
            reason = "unsupported run: key form (one-line flow mapping)"
    else:
        reason = _unparsed_key_reason(text)
    if reason is not None:
        raise WorkflowCheckError(
            f"{path}:{index + 1}: {reason}, so its block would never be shell-checked: {line.strip()!r}"
        )


def _unparsed_key_reason(text: str) -> str | None:
    """Why the quoted key on this line is unusable, or ``None`` when it is no key.

    A quoted scalar is the value it decodes to, so ``"r\\u0075n"`` is the key
    ``run``. A scalar whose escapes cannot be decoded could be that key too, and
    treating its raw text as the key would skip the block underneath.
    """
    head = text.lstrip(" \t")
    if head.startswith("?"):
        head = head[1:].lstrip(" \t")
    token = KEY_TOKEN.match(head)
    if token is None:
        if head[:1] in ("'", '"'):
            return "the quoted key spans lines, so its escapes cannot be decoded from one line"
        return None
    try:
        _unquote(token.group(0))
    except _UndecodableScalar as error:
        return str(error)
    return None


def _flow_keys(line: str) -> list[str] | None:
    """The keys of the one-line flow mapping on ``line``, quoted forms decoded.

    ``None`` means a key could not be decoded or recognised, which is loud at
    the call site: such a key can spell ``run``. Commas and colons inside quoted
    scalars, and inside nested flow collections, are not key separators.
    """
    text = line[line.find("{") + 1 :]
    keys: list[str] = []
    segment = ""
    quote = ""
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if quote:
            segment += char
            if char == "\\" and quote == '"' and index < len(text):
                segment += text[index]
                index += 1
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif char == "}" and depth:
            depth -= 1
        elif char == "}" or (char == "," and depth == 0):
            key = _flow_key(segment)
            if key is None:
                return None
            if key:
                keys.append(key)
            segment = ""
            if char == "}":
                return keys
            continue
        segment += char
    return keys


def _flow_key(segment: str) -> str | None:
    """The decoded key of one flow-mapping entry; ``''`` when there is no key."""
    text = segment.strip(" \t")
    if not text:
        return ""
    token = KEY_TOKEN.match(text)
    if token is None:
        return None
    if not text[token.end() :].lstrip(" \t").startswith(":"):
        return ""
    try:
        return _unquote(token.group(0))
    except _UndecodableScalar:
        return None


def _entry(lines: list[str], index: int) -> Entry | None:
    """The YAML mapping entry that starts at ``index``, or ``None``."""
    indent, text = _entry_prefix(lines[index])
    if text.startswith("?"):
        return _explicit_entry(lines, index, text[1:].strip())
    token = _key_token(text)
    if token is None:
        return None
    return Entry(indent, token[0], token[1], index)


def _entry_prefix(line: str) -> tuple[int, str]:
    """``(key indent, text after any sequence dash)`` for one line."""
    text = line.lstrip(" ")
    indent = len(line) - len(text)
    dash = SEQUENCE_PREFIX.match(text)
    if dash is not None:
        text = text[dash.end() :]
    return indent, text


def _key_token(text: str) -> tuple[str, str] | None:
    """``(key, value)`` for one ``key: value`` entry, in any key form."""
    match = KEY_TOKEN.match(text)
    if match is None:
        return None
    rest = text[match.end() :].lstrip(" \t")
    if not rest.startswith(":"):
        return None
    value = rest[1:]
    if value and value[0] not in " \t":
        return None
    try:
        key = _unquote(match.group(0))
    except _UndecodableScalar:
        return None  # the line guard reports it: an undecodable key can spell ``run``
    return key, value.strip(" \t")


def _explicit_entry(lines: list[str], index: int, key_text: str) -> Entry | None:
    """``? key`` on ``index``, whose value is the ``: value`` line that follows."""
    match = KEY_TOKEN.match(key_text)
    if match is None or key_text[match.end() :].strip():
        return None
    try:
        name = _unquote(match.group(0))
    except _UndecodableScalar:
        return None
    for cursor in range(index + 1, len(lines)):
        indent, text = _entry_prefix(lines[cursor])
        if text.strip() == "":
            continue
        if not text.startswith(":"):
            return None
        return Entry(indent, name, text[1:].strip(" \t"), cursor)
    return None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _unquote(value: str) -> str:
    """``value`` without its quoting, with YAML escapes decoded.

    ``"r\\u0075n"`` and ``'run'`` are both the key ``run``; only the second is
    still ``run`` when the delimiters are merely stripped. Undecodable escapes
    raise, because the key they spell cannot be known.
    """
    if len(value) < 2 or value[0] != value[-1]:
        return value
    if value[0] == '"':
        return _decode_double_quoted(value)
    if value[0] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _decode_double_quoted(token: str) -> str:
    """The value of one double-quoted YAML scalar, its escapes decoded."""
    text: list[str] = []
    index = 1
    end = len(token) - 1
    while index < end:
        char = token[index]
        index += 1
        if char != "\\":
            text.append(char)
            continue
        if index >= end:
            raise _UndecodableScalar(f"the quoted scalar {token!r} ends in a lone escape")
        escape = token[index]
        index += 1
        digits = CODE_ESCAPES.get(escape)
        if digits is not None:
            code = token[index : index + digits]
            if len(code) != digits or not set(code) <= HEX_DIGITS:
                raise _UndecodableScalar(f"the \\{escape} escape in the quoted scalar {token!r} is incomplete")
            if int(code, 16) > 0x10FFFF:
                raise _UndecodableScalar(f"the \\{escape} escape in the quoted scalar {token!r} is out of range")
            text.append(chr(int(code, 16)))
            index += digits
            continue
        decoded = ESCAPES.get(escape)
        if decoded is None:
            raise _UndecodableScalar(f"the YAML escape \\{escape} in the quoted scalar {token!r} is unknown")
        text.append(decoded)
    return "".join(text)


def _block_body(lines: list[str], start: int, indent: int) -> tuple[list[str], int]:
    """Lines of a block scalar, and the index of the first line after it."""
    body: list[str] = []
    cursor = start
    while cursor < len(lines):
        line = lines[cursor]
        if line.strip() == "":
            body.append("")
        elif _indent(line) <= indent:
            break
        else:
            body.append(line)
        cursor += 1
    while body and body[-1] == "":
        body.pop()
    return body, cursor


def _block_script(body: list[str], fallback_line: int, *, folded: bool) -> tuple[str, int]:
    if not body:
        return "", fallback_line
    base = min(_indent(line) for line in body if line.strip())
    text = [line[base:] if line.strip() else "" for line in body]
    if folded:
        return " ".join(part.strip() for part in text if part.strip()), fallback_line
    return "\n".join(text), fallback_line


def _step_context(lines: list[str], key_index: int, indent: int, after_index: int) -> tuple[str, str]:
    """Step ``name`` and declared ``shell`` for the ``run:`` key at ``key_index``."""
    start = key_index
    for index in range(key_index - 1, -1, -1):
        if lines[index].strip() == "":
            continue
        if _indent(lines[index]) < indent:
            start = index if lines[index].strip().startswith("- ") else index + 1
            break
    end = len(lines)
    for index in range(after_index, len(lines)):
        if lines[index].strip() == "":
            continue
        if _indent(lines[index]) <= indent:
            end = index
            break
    name = ""
    name_match = STEP_NAME.match(lines[start])
    if name_match is not None:
        name = _unquote(name_match.group("name"))
    shell = ""
    for index in range(start, end):
        shell_match = SHELL_KEY.match(lines[index])
        if shell_match is not None and len(shell_match.group("indent")) == indent:
            shell = _unquote(shell_match.group("shell"))
    return name, shell


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())

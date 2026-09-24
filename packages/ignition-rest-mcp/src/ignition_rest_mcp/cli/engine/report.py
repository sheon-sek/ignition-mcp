"""The step reporter (D32 section 4).

A command wraps each step in :meth:`Reporter.step`, which prints the start line and
always records an end: the status the body set, ``OK`` when it set none, or
``FAILED`` with the reason and a next action when an exception escapes.
:meth:`Reporter.fail` records the error that ends the run and closes a step that
:meth:`Reporter.start` left open. :meth:`Reporter.finish` returns the exit code.

:class:`RichReporter` prints one line when a step starts and one when it ends.
:class:`JsonReporter` prints nothing until :meth:`finish`, then one JSON document
with the same steps, the accepted items, the error code and the equivalent command.

Both reporters replace every registered secret with ``<secret>`` in every string
they print, as a last guard behind the rule that no caller passes one.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TextIO

from rich.console import Console
from rich.markup import escape

from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.resolve import Accepted

#: Version of the ``--json`` document. Bump it when a field changes meaning.
JSON_VERSION = 1


class Status(StrEnum):
    OK = "OK"
    CHANGED = "CHANGED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    status: Status
    reason: str
    #: A command the operator can run. Required for ``FAILED``, else ``""``.
    next_action: str = ""
    #: An :class:`~ignition_rest_mcp.cli.engine.errors.ErrorCode` value for ``FAILED``.
    code: str = ""


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One change ``setup`` will make, shown before anything is written."""

    stage: str
    change: str


class StepEnd:
    """What the body of :meth:`Reporter.step` sets as the step's end."""

    def __init__(self, next_action: str) -> None:
        self.status = Status.OK
        self.reason = "done"
        self.next_action = next_action
        self.code = ""

    def set(self, status: Status, reason: str, *, next_action: str = "", code: str = "") -> None:
        self.status = status
        self.reason = reason
        self.next_action = next_action or self.next_action
        self.code = code


class Reporter:
    """The shared bookkeeping. Subclasses decide how lines look."""

    def __init__(self, command: str) -> None:
        self.command = command
        #: The next action for a failure that names none. ``run`` sets it.
        self.default_next_action = f"ignition-mcp {command} --help"
        self.steps: list[Step] = []
        self.plan_entries: list[PlannedChange] = []
        self.accepted_items: list[Accepted] = []
        self.error: CliError | None = None
        self.equivalent: str = ""
        self._secrets: set[str] = set()
        self._open: str | None = None

    def hide(self, *secrets: str) -> None:
        """Register secret values that must never be printed."""

        self._secrets.update(secret for secret in secrets if secret)

    def redact(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, "<secret>")
        return text

    def start(self, name: str, what: str = "") -> None:
        self._open = name
        self._started(name, self.redact(what))

    def end(
        self, name: str, status: Status, reason: str, *, next_action: str = "", code: str = ""
    ) -> Step:
        if status is Status.FAILED and not next_action:
            raise ValueError(f"step {name!r} failed without a next action")
        step = Step(name, status, self.redact(reason), self.redact(next_action), code)
        self.steps.append(step)
        self._open = None
        self._ended(step)
        return step

    @contextmanager
    def step(self, name: str, what: str = "", *, next_action: str = "") -> Iterator[StepEnd]:
        """One step with a guaranteed end line. ``next_action`` is used if it fails."""

        end = StepEnd(next_action)
        self.start(name, what)
        try:
            yield end
        except CliError as error:
            self._close_failed(error.message, error.next_action or end.next_action, error.code.value)
            raise
        except KeyboardInterrupt:
            self._close_failed("interrupted", end.next_action, ErrorCode.INTERRUPTED.value)
            raise
        except Exception as error:
            reason = f"unexpected {type(error).__name__}"
            self._close_failed(reason, end.next_action, ErrorCode.UNEXPECTED_ERROR.value)
            raise
        if end.status is Status.FAILED and not end.next_action:
            end.next_action = self.default_next_action
        self.end(name, end.status, end.reason, next_action=end.next_action, code=end.code)

    def _close_failed(self, reason: str, next_action: str, code: str) -> None:
        if self._open is not None:
            self.end(self._open, Status.FAILED, reason, next_action=next_action or self.default_next_action, code=code)

    def plan(self, entries: list[PlannedChange]) -> None:
        self.plan_entries.extend(entries)
        self._planned([PlannedChange(entry.stage, self.redact(entry.change)) for entry in entries])

    def accepted(self, items: list[Accepted]) -> None:
        self.accepted_items.extend(items)
        for item in items:
            self._accepted(item)

    def equivalent_command(self, command: str) -> None:
        self.equivalent = self.redact(command)
        self._equivalent(self.equivalent)

    def fail(self, error: CliError) -> None:
        self._close_failed(error.message, error.next_action, error.code.value)
        self.error = error
        self._failed(error)

    def finish(self) -> int:
        code = self.error.exit_code if self.error is not None else 0
        if code == 0 and any(step.status is Status.FAILED for step in self.steps):
            code = 1
        self._finished(code)
        return code

    # Presentation hooks.
    def _started(self, name: str, what: str) -> None: ...
    def _ended(self, step: Step) -> None: ...
    def _accepted(self, item: Accepted) -> None: ...
    def _planned(self, entries: list[PlannedChange]) -> None: ...
    def _equivalent(self, command: str) -> None: ...
    def _failed(self, error: CliError) -> None: ...
    def _finished(self, code: int) -> None: ...


_STYLE = {
    Status.OK: "green",
    Status.CHANGED: "cyan",
    Status.SKIPPED: "yellow",
    Status.FAILED: "bold red",
}


class RichReporter(Reporter):
    def __init__(self, command: str, console: Console | None = None) -> None:
        super().__init__(command)
        self.console = console or Console(highlight=False)

    def _line(self, markup: str) -> None:
        self.console.print(markup, soft_wrap=True)

    def _started(self, name: str, what: str) -> None:
        self._line(f"[dim]START[/dim]   {escape(name)}" + (f"  {escape(what)}" if what else ""))

    def _ended(self, step: Step) -> None:
        style = _STYLE[step.status]
        label = f"[{style}]{step.status.value:<7}[/{style}]"
        self._line(f"{label} {escape(step.name)}  {escape(step.reason)}")
        if step.next_action:
            self._line(f"        next: {escape(step.next_action)}")

    def _planned(self, entries: list[PlannedChange]) -> None:
        if not entries:
            self._line("PLAN    no changes")
        for entry in entries:
            self._line(f"PLAN    {escape(entry.stage)}  {escape(entry.change)}")

    def _accepted(self, item: Accepted) -> None:
        detail = f" ({item.detail})" if item.detail else ""
        self._line(f"[dim]ACCEPTED[/dim] {escape(item.risk.value)}{escape(self.redact(detail))} by {item.how}")

    def _equivalent(self, command: str) -> None:
        self._line("The same run as one command:")
        self._line(f"  {escape(command)}")

    def _failed(self, error: CliError) -> None:
        self._line(f"[bold red]ERROR[/bold red]   {escape(error.code.value)}: {escape(self.redact(error.message))}")
        if error.next_action:
            self._line(f"        next: {escape(self.redact(error.next_action))}")


class JsonReporter(Reporter):
    def __init__(self, command: str, stream: TextIO | None = None) -> None:
        super().__init__(command)
        self.stream = stream or sys.stdout

    def document(self, code: int) -> dict[str, Any]:
        error: dict[str, str] | None = None
        if self.error is not None:
            error = {
                "code": self.error.code.value,
                "message": self.redact(self.error.message),
                "next_action": self.redact(self.error.next_action),
            }
        return {
            "version": JSON_VERSION,
            "command": self.command,
            "exit_code": code,
            "steps": [
                {
                    "step": step.name,
                    "status": step.status.value,
                    "reason": step.reason,
                    "next_action": step.next_action,
                    "code": step.code,
                }
                for step in self.steps
            ],
            "plan": [
                {"stage": entry.stage, "change": self.redact(entry.change)} for entry in self.plan_entries
            ],
            "accepted": [
                {"item": item.risk.value, "detail": self.redact(item.detail), "how": item.how}
                for item in self.accepted_items
            ],
            "error": error,
            "equivalent_command": self.equivalent,
        }

    def _finished(self, code: int) -> None:
        json.dump(self.document(code), self.stream, indent=2)
        self.stream.write("\n")
        self.stream.flush()

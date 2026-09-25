"""The prompter the wizard asks through (D32 section 3).

:class:`Prompter` is the whole interface. :class:`QuestionaryPrompter` is the
normal one. :class:`PlainPrompter` reads plain lines and is used under mintty
without a pseudo console, where ``questionary`` cannot drive the terminal. Tests
pass a scripted fake, so no test needs a terminal.

A prompter only asks. It never validates: the resolver checks each answer and asks
again with the reason.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable, Sequence
from typing import Protocol, TextIO


class Prompter(Protocol):
    def say(self, message: str) -> None:
        """Show one line, such as the reason an answer was refused."""

    def text(self, question: str, default: str | None = None) -> str:
        """One line of text. An empty answer returns ``default`` when there is one."""

    def secret(self, question: str) -> str:
        """One line of text that is not echoed where the terminal allows it."""

    def select(self, question: str, choices: Sequence[str], default: str | None = None) -> str:
        """One of ``choices``."""

    def confirm(self, question: str) -> bool:
        """A yes or no answer. There is no default yes: acceptance is never implied."""


class QuestionaryPrompter:
    """Arrow-key prompts through ``questionary``. Ctrl+C raises ``KeyboardInterrupt``."""

    def __init__(self, output: TextIO | None = None) -> None:
        self._output = output or sys.stderr

    def say(self, message: str) -> None:
        print(message, file=self._output)

    def text(self, question: str, default: str | None = None) -> str:
        import questionary

        return _answered(questionary.text(question, default=default or "").ask())

    def secret(self, question: str) -> str:
        import questionary

        return _answered(questionary.password(question).ask())

    def select(self, question: str, choices: Sequence[str], default: str | None = None) -> str:
        import questionary

        return _answered(questionary.select(question, choices=list(choices), default=default).ask())

    def confirm(self, question: str) -> bool:
        import questionary

        answer = questionary.confirm(question, default=False).ask()
        if answer is None:
            raise KeyboardInterrupt
        return bool(answer)


class PlainPrompter:
    """Numbered choices and ``y``/``n`` answers read one line at a time.

    Questions go to ``output`` (stderr by default) so stdout stays free for the
    report. ``read_secret`` defaults to ``getpass``; under mintty without a pseudo
    console ``getpass`` cannot turn echo off, so there the secret is read as a line
    and the question says it will be visible.
    """

    def __init__(
        self,
        read_line: Callable[[], str] | None = None,
        output: TextIO | None = None,
        read_secret: Callable[[str], str] | None = None,
    ) -> None:
        self._read_line = read_line or sys.stdin.readline
        self._output = output or sys.stderr
        self._read_secret = read_secret

    def say(self, message: str) -> None:
        print(message, file=self._output)

    def _ask(self, prompt: str) -> str:
        self._output.write(prompt)
        self._output.flush()
        line = self._read_line()
        if not line:  # end of input
            raise KeyboardInterrupt
        return line.strip()

    def text(self, question: str, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        answer = self._ask(f"{question}{suffix}: ")
        return answer or (default or "")

    def secret(self, question: str) -> str:
        if self._read_secret is not None:
            return self._read_secret(f"{question}: ").strip()
        if is_mintty_pipe():
            return self._ask(f"{question} (the terminal shows what you type): ")
        return getpass.getpass(f"{question}: ", stream=self._output).strip()

    def select(self, question: str, choices: Sequence[str], default: str | None = None) -> str:
        self.say(question)
        for index, choice in enumerate(choices, start=1):
            self.say(f"  {index}) {choice}")
        while True:
            answer = self._ask(f"Choose 1-{len(choices)}" + (f" [{default}]" if default else "") + ": ")
            if not answer and default is not None:
                return default
            if answer in choices:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(choices):
                return choices[int(answer) - 1]
            self.say(f"Enter a number from 1 to {len(choices)}.")

    def confirm(self, question: str) -> bool:
        while True:
            answer = self._ask(f"{question} [y/n]: ").lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("Answer y or n.")


def _answered(answer: object) -> str:
    # questionary returns None when the operator presses Ctrl+C.
    if answer is None:
        raise KeyboardInterrupt
    return str(answer)


def is_terminal(stream: TextIO | None = None) -> bool:
    """Whether a person can answer on ``stream`` (stdin by default).

    Under mintty without a pseudo console stdin is a pipe, so ``isatty`` is false,
    but a person is still typing. That case counts as a terminal too.
    """

    stream = stream or sys.stdin
    try:
        if stream.isatty():
            return True
    except (AttributeError, ValueError):
        return False
    return is_mintty_pipe(stream)


def default_prompter() -> Prompter:
    """Plain prompts under mintty without a pseudo console, ``questionary`` otherwise."""

    if is_mintty_pipe():
        return PlainPrompter()
    return QuestionaryPrompter()


def is_mintty_pipe(stream: TextIO | None = None) -> bool:
    """Whether ``stream`` is the pipe mintty gives a native Windows program.

    MSYS2 and Cygwin name that pipe ``\\msys-<id>-ptyN-from-master`` or
    ``\\cygwin-<id>-ptyN-from-master``. Only the name tells it apart from a pipe
    another program feeds, so this reads the name through Win32. It is always
    ``False`` off Windows.
    """

    if sys.platform != "win32":
        return False
    stream = stream or sys.stdin
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return False
    name = _pipe_name(descriptor)
    return ("msys-" in name or "cygwin-" in name) and "-pty" in name


def _pipe_name(descriptor: int) -> str:
    if sys.platform != "win32":
        return ""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    file_name_info = 2  # FILE_INFO_BY_HANDLE_CLASS.FileNameInfo
    file_type_pipe = 3
    handle = msvcrt.get_osfhandle(descriptor)
    kernel32 = ctypes.windll.kernel32
    if kernel32.GetFileType(wintypes.HANDLE(handle)) != file_type_pipe:
        return ""
    size = 4 + 2 * 1024
    buffer = ctypes.create_string_buffer(size)
    if not kernel32.GetFileInformationByHandleEx(wintypes.HANDLE(handle), file_name_info, buffer, size):
        return ""
    length = int.from_bytes(buffer.raw[:4], "little")
    return buffer.raw[4 : 4 + length].decode("utf-16-le", errors="replace")



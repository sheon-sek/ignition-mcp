"""Stable error codes for the ``ignition-mcp`` commands (D32 section 4).

The codes belong to the CLI, not to ``contracts/``, which describes Tools. A code
never changes meaning once released: ``--json`` consumers match on it. Add new
codes at the end and document them in the CLI guide.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    #: A value has no flag, no saved value and no terminal to ask on.
    MISSING_INPUT = "missing_input"
    #: A flag or saved value failed validation and there is no terminal to ask again on.
    INVALID_INPUT = "invalid_input"
    #: A named risk or legal term was not accepted (D32 section 6).
    ACCEPTANCE_REQUIRED = "acceptance_required"
    #: The Gateway answered and refused the setup token.
    GATEWAY_TOKEN_REJECTED = "gateway_token_rejected"
    #: ``deployment.toml`` exists but cannot be read or parsed.
    DEPLOYMENT_UNREADABLE = "deployment_unreadable"
    #: A secret file is missing, unreadable, too open or has the wrong shape.
    SECRET_FILE_INVALID = "secret_file_invalid"
    #: A step could not finish; the step's reason says why.
    STEP_FAILED = "step_failed"
    #: The command is registered but its body has not been written yet.
    NOT_IMPLEMENTED = "not_implemented"
    #: The operator pressed Ctrl+C or closed the prompt.
    INTERRUPTED = "interrupted"
    #: A bug. Only the exception type is reported, so no secret can leak.
    UNEXPECTED_ERROR = "unexpected_error"
    #: DNS, TCP, TLS or a timeout: the Gateway never answered, so nothing was judged.
    GATEWAY_UNREACHABLE = "gateway_unreachable"
    #: The operator saw the plan and did not confirm it. Nothing was written.
    NOT_CONFIRMED = "not_confirmed"
    #: The Gateway lists the MCP Module but does not run it, so nothing it hosts exists.
    MODULE_NOT_ACTIVE = "module_not_active"
    #: The Gateway refused to mark the MCP Module for uninstall (issue #81).
    MODULE_UNINSTALL_REFUSED = "module_uninstall_refused"
    #: Another writer holds the project lock, or the project changed on the Gateway
    #: between the baseline export and the import (D16). Nothing was imported.
    CONFLICT = "conflict"


#: Exit code for each error code. ``0`` is success, ``1`` a failed step, ``2`` a
#: problem with the command line or the operator's answers.
EXIT_CODES: dict[ErrorCode, int] = {
    ErrorCode.MISSING_INPUT: 2,
    ErrorCode.INVALID_INPUT: 2,
    ErrorCode.ACCEPTANCE_REQUIRED: 2,
    ErrorCode.GATEWAY_TOKEN_REJECTED: 2,
    ErrorCode.DEPLOYMENT_UNREADABLE: 2,
    ErrorCode.SECRET_FILE_INVALID: 2,
    ErrorCode.STEP_FAILED: 1,
    ErrorCode.NOT_IMPLEMENTED: 1,
    ErrorCode.INTERRUPTED: 2,
    ErrorCode.UNEXPECTED_ERROR: 1,
    ErrorCode.GATEWAY_UNREACHABLE: 1,
    ErrorCode.NOT_CONFIRMED: 2,
    ErrorCode.MODULE_NOT_ACTIVE: 1,
    ErrorCode.MODULE_UNINSTALL_REFUSED: 1,
    ErrorCode.CONFLICT: 1,
}


class CliError(Exception):
    """A failure the reporter prints as its last line, with a code and a next action.

    ``message`` and ``next_action`` must never hold a secret. ``next_action`` is a
    command the operator can run, or ``""`` when none applies.
    """

    def __init__(self, code: ErrorCode, message: str, next_action: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]

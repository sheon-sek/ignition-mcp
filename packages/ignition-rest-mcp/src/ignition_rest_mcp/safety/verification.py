"""The one D30 §2 rule every REST Phase 4 Mutation's read-back follows.

A Tool turns what its bounded verification observed into a
``VerificationOutcome``, which the guarded executor maps onto a mutation state.
Every Tool answers the same question the same way: did *this* call produce the
observed state? Only a Gateway claim, plus a read-back that shows the intended
state, is a confirmation; an unchanged pre-state is a negative conclusion; and
anything else is indeterminate, because a read-back can show the intended values
without proving this call wrote them.
"""

from __future__ import annotations

from ignition_rest_mcp.safety.executor import VerificationOutcome


def verdict(
    *, claimed: bool, intended: bool, pre_state: bool, no_effect: bool = False,
) -> VerificationOutcome:
    """The D30 §2 attribution rule shared by every Phase 4 REST Mutation Tool.

    A claimed success is confirmed when the intended state is observed. Anything
    else has no claim to verify (an ambiguous dispatch: possibly sent, no response),
    and a read-back can show the intended values without proving *this* call wrote
    them — another writer may have made the same change inside the window — so only a
    negative conclusion is drawn: the pre-state intact, or a request with no
    observable effect of its own, is UNCHANGED ("nothing attributable to this call"),
    and anything else is INDETERMINATE, which the executor reports as
    ``outcome_unknown``. A success there would be a claim the evidence cannot support.
    """

    if claimed:
        if intended:
            return VerificationOutcome.CONFIRMED
        return VerificationOutcome.UNCHANGED if pre_state else VerificationOutcome.MISMATCH
    if pre_state or no_effect:
        return VerificationOutcome.UNCHANGED
    if intended:
        return VerificationOutcome.INDETERMINATE
    return VerificationOutcome.MISMATCH

# Phase 5 — Perspective typed authoring

Status: IN PROGRESS (scope frozen 2026-09-22).

Branch: `phase-5`, created from `main` at `9f414b0` on the owner's instruction.

Authority: D15, D16, D26 and its Phase 5 amendment, `CONTEXT.md`, then this runbook. If a decided rule cannot be met, the ticket stops and records the question here. Nobody reinterprets a decision silently.

## Goal

Ship the nine D15 Perspective Tools on the REST plane and close G5. The bar is working Tools with correct input and output, plus the safety rules: project Target allowlist, Precondition token, no silent inherited override, no generic path write, no secrets in output. Every other kind of rigor goes to a tracked issue.

## Tools

| Tool | Kind | Notes |
|---|---|---|
| `perspective_view_list` | read | Local Views only, paginated per D10 |
| `perspective_view_get` | read | returns the View document and the `pcf1` Project fingerprint |
| `perspective_view_validate` | read | offline: JSON, `root.type`, size and depth budgets |
| `perspective_page_config_get` | read | `not_found` when the project has none locally |
| `perspective_session_props_get` | read | `not_found` when the project has none locally |
| `perspective_view_upsert` | CONFIG write | whole-document replace, creates when absent |
| `perspective_view_delete` | CONFIG write, destructive | one View, never a folder |
| `perspective_page_config_update` | CONFIG write | whole-document replace, creates when absent |
| `perspective_session_props_update` | CONFIG write | whole-document replace, creates when absent |

Every write goes through `ProjectTransactionService` with a Perspective `CandidateBuilder`. The builder copies the baseline archive and patches only the target resource. Before building, the write checks the ancestor chain and refuses an Inherited resource with `invalid_argument` and reason `inherited_resource`.

## Tickets

1. P5-1 (#48): the typed Perspective adapter and the five reads.
2. P5-2 (#49): the four writes. It starts after P5-1 is merged into `phase-5`.
3. P5-3 (#50): the G5 live stage inside the existing `phase4-live-rest` harness and workflow. It runs in parallel with P5-2 and adds no new workflow.

## Delivery rules

The CLAUDE.md delivery speed rules apply. Agents never push, run only the tests for their own files plus `ruff`, `mypy` and `tooling.contracts.lint`, and stay in their own worktree. Codex reviews each ticket, with a cap of two rounds. Only functional or safety failures block. The coordinator pushes once per integration head and runs only the G5 workflow and the unit jobs.

## Open questions

None yet.

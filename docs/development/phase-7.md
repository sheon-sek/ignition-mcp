# Phase 7: setup CLI and G7

Status: G7 RECORDED on both tuples (run 36056634095). The owner ran the wizard on Windows and approved the merge. Merged to `main` on 2026-09-25. D32 approved on 2026-09-25.

Branch: `phase-7/setup-ux`, created from `fix/windows-portability` at `98d9456` on the owner's instruction, because that branch is about to merge into `main`. Rebase onto `main` after it merges. Each ticket runs on its own branch and worktree and merges into `phase-7/setup-ux`.

Authority: D32, then D20 as D32 amends it, D07, D25, D30, D31, `CONTEXT.md`, then this file. If a decided rule cannot be met, the ticket stops and records the question here. Nobody reinterprets a decision silently.

## Goal

Replace `ignition-mcp setup-native` and the bash wizard with the `ignition-mcp` commands D32 decides: `setup`, `status`, `start`, `connect` and `reset`. An operator or an AI agent must be able to take an empty playground Gateway to two connected Assistant roles with one command, and every step must say what happened and why.

## Tickets

1. **P7-1. D32, `CONTEXT.md` and `INDEX.md`.** Docs only. The coordinator writes this ticket.
2. **P7-2 (#73). CLI engine.** The deployment directory and `deployment.toml`, secret files, input resolution from flags then prompts then a non-TTY error that lists missing flags, the replaceable prompter with its `questionary` and plain-line implementations, asking again after invalid input, the step reporter for `rich` and `--json`, and the CLI error codes. Blocks every ticket below.
3. **P7-3 (#74). `setup` on the Runtime plane.** Module discovery and install, the bundle build from the checkout, a Security Level, a Runtime token and a Server Config with a generated permissions tree for each role, the generated Runtime Target Policy, and a closing check that initializes each endpoint with that role's token. Includes the three re-run rules in D32 section 10. Reuses the verified `setup_native` logic.
4. **P7-4 (#75). `setup` on the REST plane and `start`.** The `ignition-mcp-rest` Gateway token, both Named static tokens, the `IGNITION_MCP_*` values `start` derives from the deployment, and a foreground `start` that prints the health result and both roles' endpoints before serving.
5. **P7-5 (#76). `status`, `connect` and `reset`.** `connect` detects Claude Code and Codex, shows an agent that is not installed as an option that cannot be chosen, and offers None. `reset` is refused outside `dev`.
6. **P7-6 (#77). Remove the old entry points and rewrite the guides.** Delete `cli/setup_native`, its tests that the new CLI does not reuse, and `scripts/deploy-runtime-bundle.sh`. Merge `setup-rest` and `setup-runtime` into one quick-start guide in English and Chinese. Update the runbook. Blocked by P7-3 to P7-5.
7. **P7-7 (#78). Live G7 stage.** Extends an existing live harness: one one-line `setup` from an empty Gateway, `initialize` and `tools/list` with each role's token, then `reset` and a check that the Gateway is clean. Records the G7 evidence row. Blocked by P7-3 to P7-5.

P7-3 and P7-4 can run in parallel once P7-2 merges. Both edit the `setup` command, so their edits stay additive.

## Gate rules

- G7 never records `SUPPORTED`, as in every earlier gate.
- The live stage extends an existing harness and adds no workflow.
- Tests stay proportionate. The prompter is replaced by a scripted fake, so no test needs a terminal. One-line mode and wizard mode run the same scenarios and must produce the same result. The Gateway side uses `RecordedGateway`. Covered cases are the happy path, each safety refusal, asking again after invalid input, and the three re-run rules.

## G7 checklist

- [x] 1. `setup` takes an empty Gateway to both Assistant roles in one command, in `dev`, with no file written by hand. Evidence: `g7-8.3.8-mcp-2026021307` and `g7-8.3.9-mcp-2026021307`, run 36056634095, the `setup` step of `setup-g7.json`. The only manual action is the operator's setup key, as D32 section 9 decides.
- [x] 2. The wizard asks only for missing values, asks again after invalid input, and prints the equivalent one-line command. Evidence: the P7-2 unit tests `test_one_line_and_wizard_resolve_the_same_inputs`, `test_a_saved_deployment_answers_instead_of_asking`, `test_rejected_token_is_asked_again_and_the_run_continues`, `test_wrong_path_is_asked_again` and `test_equivalent_command_reruns_to_the_same_result`. The owner's Windows run in item 9 exercises the wizard by hand.
- [x] 3. In a non-TTY run with a missing value, the command exits before any write and lists the missing flags. Evidence: the P7-2 unit tests `test_non_tty_missing_values_fail_before_any_write` and `test_one_line_changes_need_yes`, and the P7-6 CLI smoke run of `ignition-mcp setup` with no flags and no terminal.
- [x] 4. Every step reports its status and reason, and every failure names a next action. `--json` carries the same steps with stable error codes. Evidence: the P7-2 unit tests `test_json_steps_carry_codes_and_never_a_secret`, `test_an_error_inside_a_step_ends_that_step_as_failed` and `test_an_unreachable_gateway_has_its_own_code`, and the live `setup-g7.json` documents of run 36056634095, which are the CLI's own `--json` output.
- [x] 5. Each role's token initializes only its own endpoint, and `tools/list` returns that role's exact Tool inventory. Evidence: `g7-8.3.8-mcp-2026021307` and `g7-8.3.9-mcp-2026021307`, run 36056634095, the `roles` step (and the `rest` step for the Named static tokens).
- [x] 6. A second `setup` run reports no change. The three re-run rules in D32 section 10 behave as decided. Evidence: `g7-8.3.8-mcp-2026021307` and `g7-8.3.9-mcp-2026021307`, run 36056634095, the `setupAgain` step. The three re-run rules are covered by the unit tests of P7-3 to P7-5, not by the live run.
- [x] 7. `reset` removes everything `setup` created and is refused in `prod`. Evidence: `g7-8.3.8-mcp-2026021307` and `g7-8.3.9-mcp-2026021307`, run 36056634095, the `reset` step, including the Module uninstall. The `prod` refusal is covered by the P7-5 tests, not by the live run.
- [x] 8. The old entry points are deleted and the guides describe only the new commands. Evidence: P7-6 (#77, merge 24f3839). A repository grep finds `setup-native` only in decision records, phase records, compatibility evidence and the `tooling/compat` code that reads the frozen G4 and G6 artifacts.
- [x] 9. The owner runs the wizard once on a Windows Gateway and records the result. Evidence: on 2026-09-25 the owner reported running it by hand on Windows and finding no significant problems.

## Owner rulings

- 2026-09-25: `setup` may use the Module copy in `tests/fixtures/modules/`. D32 section 9.
- 2026-09-25: tickets are tracked as GitHub issues, and each worker's Orca worktree is linked to its issue.

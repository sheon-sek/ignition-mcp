# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/decisions/INDEX.md`**, then the `D<NN>-*.md` decisions that touch the area you're about to work in. D01–D28 are binding (see `AGENTS.md`, "Governance").

If `CONTEXT.md` doesn't exist, **proceed silently**. Don't flag its absence; don't suggest creating it upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates it lazily when terms actually get resolved.

Documents under `docs/_archive/` are not authoritative. Don't cite them as decisions.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/decisions/
│   ├── INDEX.md
│   ├── D01-license-and-upstream-reuse.md
│   ├── D02-capability-ownership.md
│   └── ...
└── packages/
    ├── ignition-rest-mcp/
    └── ignition-runtime-bundle/
```

## Recording decisions

This repo's decision records live in `docs/decisions/`, not `docs/adr/`. When a skill says "write an ADR":

- Name the file `D<NN>-<kebab-slug>.md`, taking the next number after the highest existing `D<NN>`.
- Add a row for it to the decision index table in `docs/decisions/INDEX.md`.
- Never edit a decided rule in place. Changing one needs a new decision or an explicit amendment section on the existing one.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag decision conflicts

If your output contradicts an existing decision, surface it explicitly rather than silently overriding:

> _Contradicts D02 (capability ownership), but worth reopening because…_

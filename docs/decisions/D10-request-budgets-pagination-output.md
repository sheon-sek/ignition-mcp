# D10 — Request Budgets, Pagination and Output Limits

**Status:** DECIDED

## Core principle
MCP is an Agent operation interface, not a bulk ETL channel.

Every Tool must bound:
- input;
- execution;
- output;
- pagination/continuation.

No unbounded input, query, recursion, cache, or response.

## Budget layers
```text
Project safe default
→ deployment override
→ absolute hard ceiling
```
Deployment may lower limits and raise within hard ceilings, but may not exceed hard ceilings.

## Structured output size
- Default target ceiling: **256 KiB**
- Absolute hard ceiling: **1 MiB**

Measure serialized structured result bytes.

Large binary/artifact data must not be returned as inline Base64.

## No silent truncation
Allowed outcomes:
1. complete;
2. paginated with continuation;
3. `limit_exceeded`.

Never silently drop data.

## Text Resources and Prompts
The bounded Agent interface principle also covers MCP Text Resources and Prompts:

- a Text Resource that publishes a Tool's schema or documentation must stay small and bounded;
- no multi-MB Text Resource and no unbounded Prompt expansion;
- the no-silent-truncation rule applies unchanged: an over-budget Resource or Prompt payload must be rejected as `limit_exceeded`, or split into several bounded primitives, rather than truncated or partially emitted.

Per-primitive numeric budgets for Resources/Prompts are fixed when those primitives are actually implemented. The existing per-Tool numeric budgets above are unchanged.

## Pagination
Prefer opaque cursors that are bounded, tamper-resistant, and tied to the original query.

Avoid unbounded server-side pagination-session caches.
Prefer compact/stateless cursor state. If upstream continuation requires state, use bounded TTL/LRU storage with cleanup.

One Tool call returns one page; do not fetch hundreds of pages internally and concatenate them.

## REST collections
- Default page size: 100
- Hard max: 500

## Tag
### `tag_read`
- default max paths: 100
- hard max: 500
- internal batch ~100–200
- timeout default 10 s / hard 30 s

### `tag_write`
- default max writes: 20
- hard max: 100
- timeout default 10 s / hard 30 s

### Browse/config
- browse default 100 / hard 500
- recursive browse default disabled
- recursive hard max nodes 2,000
- config read targets default 50 / hard 200
- config mutation targets default 20 / hard 100

## Alarm
### Active alarms
- default page 100
- hard max 500

### Journal
- default range 24 h
- hard max 31 days
- default page 100
- hard max 500
- timeout default 20 s / hard 60 s

## Historian raw
- default paths 10 / hard 50
- default range 24 h / hard 7 days
- default total points 5,000 / hard 25,000
- timeout default 30 s / hard 120 s
- unlimited return size forbidden

Budget applies across all paths combined.

## Historian aggregate
- default paths 20 / hard 100
- default range 7 days / hard 366 days
- default total buckets 5,000 / hard 25,000
- timeout default 30 s / hard 120 s

Budget considers `paths × buckets`.

## DB readonly
- default rows 200 / hard 2,000
- timeout default 10 s / hard 30 s
- global output ceiling still applies

## Generic mutation
- default targets 20
- hard max 100

## Timeout classes
### FAST
- default 10 s
- hard 30 s

### QUERY
- default 30 s
- hard 120 s

### ARTIFACT
- default 120 s
- hard 300 s

Mutation timeout never implies safe retry.

## Cancellation
Propagate cancellation through async I/O where supported.
Runtime blocking calls may not be interruptible, making bounded requests essential.

## Errors
Over-budget uses `limit_exceeded` and must state:
- requested amount;
- applicable limit;
- how to split/reduce the request.

```yaml
decision: D10
status: DECIDED
structured_output_default_bytes: 262144
structured_output_hard_bytes: 1048576
silent_truncation: forbidden
pagination: cursor-preferred
binary_inline_base64: false
resource_prompt_payloads_bounded: required
```

## Pre-D26 consistency amendment
Extended the bounded Agent interface principle to MCP Text Resources and Prompts (bounded payloads, no silent truncation, no multi-MB Text Resource).

Removed the stale `hard max SQL text 32 KiB` DB-readonly budget: D14 exposes no caller-supplied SQL (approved Named Query registry only), so no SQL text crosses the MCP boundary to bound. Row limits, timeout, and the global output ceiling still apply under the Named Query model. All other Tool numeric budgets and timeout classes are unchanged.

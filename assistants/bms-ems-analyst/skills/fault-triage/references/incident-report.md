# Incident report

## Field checks

A field check is an instruction a technician on site can carry out safely. Each one states:

- **Where**: the equipment tag, the panel, and what to look at.
- **What**: the reading, indicator or observation.
- **Expected**: the result that would confirm or exclude each remaining candidate. For example: "Breaker Q3 flag: tripped → overload or short (candidate A); open with no flag → a shunt trip from EPO or fire (candidate B)."
- **Who and how**: whether it needs a qualified electrician, energised-work controls or LOTO. Anything behind a live-front panel needs a qualified person.

Order them by the information they give for the effort and risk they cost. A look at the HMI or front panel comes before opening anything.

## Report template

```
STATUS   <verdict or risk> | urgency: Immediate / Urgent / Planned
SCOPE    <equipment> at <site/area>, first deviation <time TZ>, alarm <time TZ>
REDUNDANCY  <before> → <now>; time to impact <estimate or n/a>

TIMELINE
  <time>  <event>   (source: Tag path / audit / user)

FINDINGS
  - <fact>  [<Tag path> = <value>, <quality>, <timestamp>]

CAUSE    <most likely>  confidence: High / Medium / Low
         runner-up: <next>; why it ranks lower: <evidence>
         ruled out: <candidate> by <evidence>

FIELD CHECKS
  1. ...

ACTIONS
  - <action> | owner <role> | uses <redundancy> | <MOP/EOP needed?>

DATA GAPS  <signals that were missing, stale or not historised; what instrumentation would have caught this sooner>
```

## Confidence

- **High**: direct evidence for the cause, and the other candidates ruled out by evidence.
- **Medium**: the evidence fits and nothing contradicts it, but at least one candidate is still open and waits on a field check.
- **Low**: an inference from the pattern only, or the key data is suspect or missing.

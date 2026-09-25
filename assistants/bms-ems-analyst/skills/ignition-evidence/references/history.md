# History

- `historian_browse` confirms the path is historised and gives the exact historical path.
- `historian_query_series` returns raw points: at most 50 paths, 7 days, and 25,000 points in total (paths × sampleCount). Use it for the minutes around an event, with high `sampleCount` on few paths.
- `historian_query_aggregate` computes over the **whole** range, not in buckets. To get a trend (daily PUE, weekly UPS load, drift over a month), call it once per consecutive window, for example one call per day.

Useful aggregates:

| Aggregate | Answers |
| --- | --- |
| `Minimum`, `Maximum`, `Range` | Excursions and peaks |
| `Average`, `LastValue` | Load and utilisation reports |
| `CountOn`, `DurationOn` on a status Tag | Starts, run hours, short-cycling, lead/lag rotation |
| `PctBad` | How much of the window the data itself was bad |
| `StdDev` | Hunting control loops, unstable readings |

An average hides spikes. For an event, use `Maximum`/`Minimum` or raw points around it.

# Cityflo take-home — as-of lateness verdicts

For each route, at a given cut-off (07:45 IST, 2026-06-17), the tool says one of four things:
- `PUSH_LATE n`: tell the route's riders the bus is late, and by how much;
- `HOLD`: do nothing;
- `CALL_DRIVER`;
- `NO_VERDICT`, with a reason.

It uses **only what the server had received by the cut-off**. What happened afterwards goes to a separate retrospective file.

The reasoning behind every rule is in [`DECISION_RECORD.md`](DECISION_RECORD.md). The decisions ledger is [`decisions.jsonl`](decisions.jsonl).

## Architecture

```
data/*.csv
   │
   ▼
load.py ──── schema check; parse tz-aware timestamps; bad rows -> Issue (never silent)
   │
   ▼
clean.py ─── (1) KNOWLEDGE CUT: received_at <= as_of      ──► held pings ──┐
   │         (2) row checks: service area, clock-ahead, speed field         │
   │         (3) exact-duplicate fixes   (4) position-jump filter           │
   ▼                                                                        │
features.py  assign pings→trips; snap to stop polyline (geometry.py);       │
   │         schedule lateness; silence & recency on received_at;           │
   │         Mon/Tue baseline (received by as_of, up to arrival)            │
   ▼                                                                        │
decide.py ── ordered rules → Verdict(verdict, reason, action, note)         │
   │                                                                        │
   ├──────────────► report.py → verdicts.csv / verdicts.jsonl               │
   │                            data_quality.csv / run_meta.json            │
   ▼                                                                        │
retro.py ─── hindsight re-run + held-ping analysis ◄────────────────────────┘
             → retrospective.csv   (never an input to decide.py)
```

| module | responsibility |
|---|---|
| `lateness/config.py` | CLI, as-of parsing (offset required), all thresholds |
| `lateness/load.py` | read and validate the CSVs. The timetable is strict (fail loudly); telemetry and bookings are lenient (record and skip bad rows) |
| `lateness/clean.py` | knowledge cut, then telemetry cleaning |
| `lateness/geometry.py` | local projection; snapping a fix onto the stop line → distance along the route, distance off the route |
| `lateness/features.py` | per-trip features at the cut-off |
| `lateness/decide.py` | pure decision function (no input/output, no clock) |
| `lateness/retro.py` | hindsight diagnostics, kept separate |
| `lateness/report.py` | deterministic CSV / JSONL / JSON output |
| `lateness/pipeline.py` | wires the modules together; command-line entry point |

## Prerequisites

- Python **3.10+** (developed and tested on 3.14.10).
- **No third-party packages.** The pipeline and tests use only the standard library.
- Optional: `exploration/*.py` (the first-pass data scans) and `legacy/slice_v1.py` use `duckdb`. `slice_v1.py` also uses `pandas`. Neither is needed to run or test the pipeline.

## Install

```powershell
git clone <repo> ; cd cityflo-takehome
# nothing to install
```

## Run

```powershell
python -m lateness --as-of "2026-06-17 07:45:00+05:30"
```

- `--as-of` is required and **must include a UTC offset**; a timestamp without one is rejected. The service date comes from the as-of time in IST, so any date works, e.g. `--as-of "2026-06-18 07:45:00+05:30"` for a fourth morning.
- `--data-dir DIR` (default `data`) and `--out-dir DIR` (default `outputs/<as-of>`).
- Every threshold can be overridden, e.g. `--silence-min 8 --push-min 4 --origin-radius-m 300`. Run `python -m lateness --help` for the full list.
- Exit codes: `0` success; `2` input or configuration error, with a readable message (e.g. a missing column, a timestamp without an offset, no trips on that date).

## Outputs (`outputs/<as-of>/`)

| file | content |
|---|---|
| `verdicts.csv` | one row per live trip, in worry-order. Verdict, reason, action, note, plus the features behind them (lateness, progress, silence, recent fixes, history, rider impact) |
| `verdicts.jsonl` | the same rows as JSON lines, each tagged with `as_of` |
| `retrospective.csv` | **hindsight only:** held pings, delivery lag, `hindsight_verdict`, finding |
| `data_quality.csv` | every rejected or flagged input row: file, row number, id, severity, code, detail |
| `run_meta.json` | thresholds used, counts, SHA-256 of every input file |
| `console.txt` | the console table (checked in for the 07:45 run only) |

Output is deterministic: rows are sorted by fixed keys, numbers are formatted the same way every run, and no wall-clock time is recorded.

## Example (07:45, 2026-06-17)

```
| 1 | 11 Borivali → BKC          | TRIP_014 | NO_VERDICT       | ORIGIN_STATIONARY | ops: confirm departure |
| 2 | 17 Vashi → Worli           | TRIP_017 | CALL_DRIVER      | TELEMETRY_SILENT  | driver + telemetry     |
| 3 | 14 Kandivali → Lower Parel | TRIP_016 | CALL_DRIVER      | TELEMETRY_SILENT  | driver + telemetry     |
| 4 | 12 Mulund → Andheri E      | TRIP_015 | PUSH_LATE 10 min | LATE_VS_SCHEDULE  | rider push             |
| 5 | 9 Thane → Powai            | TRIP_013 | HOLD             | ARRIVED           |                        |
| 6 | 21 Ghatkopar → BKC         | TRIP_018 | HOLD             | ON_SCHEDULE       |                        |
| 7 | 21 Ghatkopar → BKC         | TRIP_019 | HOLD             | ON_SCHEDULE       | (operator 7, kept)     |
Retrospective: TRIP_016 DELIVERY_DELAY (145 fixes arrived ~90 min late); TRIP_017 VEHICLE_SILENT_CONFIRMED
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

There are 47 tests:
- `tests/test_logic.py` builds synthetic scenarios on a hand-checkable route. It covers the cut-off, late arrivals, silence, origin-stationary, the push gates, HOLD, duplicates, bad timestamps, jumps, missing telemetry, multiple trips, and empty or malformed inputs.
- `tests/test_real_extract.py` runs on the supplied extract. It fixes the expected 07:45 verdicts and checks determinism.
- It includes the **Route 14 regression**: deleting or corrupting V-04's 145 held pings changes no live field. A control test confirms that delivering them on time *would* change the verdict.

## Repository layout

```
lateness/          pipeline package (stdlib only)
tests/             unittest suite
data/              supplied extract (unchanged)
outputs/           generated results (07:45 run checked in)
exploration/       first-pass DuckDB scans used to find the data issues (reference only)
legacy/            v1 slice + its 07:45 output, kept for the before/after comparison
HANDOFF.md, DATA_GUIDE.md   supplied brief
DECISION_RECORD.md, decisions.jsonl, README.md
```

## Known limitations

See `DECISION_RECORD.md` §7. In short:
1. At the cut-off, a stopped vehicle and a delivery fault look identical. Both get CALL_DRIVER, and on this morning that means one unnecessary call for Route 14.
2. The schedule assumes even pace along the route, so mid-route readings carry a few minutes of error.
3. The thresholds are initial policy.
4. Two days of history is a thin baseline.

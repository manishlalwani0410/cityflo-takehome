"""Output writers. Deterministic: fixed sort keys, fixed float formatting, no wall-clock timestamps."""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

VERDICT_COLS = ["worry_rank", "route_id", "route_name", "trip_id", "vehicle_id", "operator_id", "riders",
                "verdict", "reason", "action", "note", "scheduled_start", "minutes_since_start",
                "late_now_min", "late_spread_min", "frac_done", "max_along_m", "departed_at", "arrived_at",
                "arrival_late_min", "first_fix",
                "last_fix_recorded", "last_received", "silence_min", "n_recent", "n_fixes", "n_heartbeats",
                "hist_late_min", "hist_n", "riders_past_promised_eta"]


def _r(x, nd=1):
    return "" if x is None else round(x, nd)


def _t(x):
    return "" if x is None else x.isoformat()


def verdict_rows(feats, verdicts):
    rows = []
    for f, v in zip(feats, verdicts):
        t = f.trip
        rows.append({
            "worry_rank": v.rank, "route_id": t.route_id, "route_name": f.route_name, "trip_id": t.trip_id,
            "vehicle_id": t.vehicle_id, "operator_id": "" if f.operator_id is None else f.operator_id,
            "riders": f.riders, "verdict": v.verdict, "reason": v.reason, "action": v.action, "note": v.note,
            "scheduled_start": _t(t.start), "minutes_since_start": _r(f.minutes_since_start),
            "late_now_min": _r(f.late_now), "late_spread_min": _r(f.late_spread), "frac_done": _r(f.frac_done, 3),
            "max_along_m": _r(f.max_along_m, 0), "departed_at": _t(f.departed_at), "arrived_at": _t(f.arrived_at),
            "arrival_late_min": _r(f.arrival_late), "first_fix": _t(f.first_fix),
            "last_fix_recorded": _t(f.last_rec), "last_received": _t(f.last_recv), "silence_min": _r(f.silence_min),
            "n_recent": f.n_recent, "n_fixes": f.n_fixes, "n_heartbeats": f.n_heartbeats,
            "hist_late_min": _r(f.hist_late), "hist_n": f.hist_n,
            "riders_past_promised_eta": f.riders_promised_before_last_fix,
        })
    rows.sort(key=lambda r: (r["worry_rank"], -r["riders"], r["route_id"], r["trip_id"]))
    return rows


def _csv(path: Path, cols, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_all(cfg, rows, retro, issues, counts):
    out = cfg.out_dir
    out.mkdir(parents=True, exist_ok=True)
    _csv(out / "verdicts.csv", VERDICT_COLS, rows)
    with (out / "verdicts.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps({"as_of": cfg.as_of.isoformat(), **r}, ensure_ascii=False) + "\n")
    rcols = list(asdict(retro[0]).keys()) if retro else ["trip_id"]
    _csv(out / "retrospective.csv", rcols, sorted((asdict(r) for r in retro), key=lambda r: r["trip_id"]))
    icols = ["source", "row", "record_id", "severity", "code", "detail"]
    _csv(out / "data_quality.csv", icols,
         sorted((asdict(i) for i in issues), key=lambda i: (i["source"], i["row"], i["code"], i["record_id"])))
    meta = {"config": cfg.as_dict(), "counts": counts,
            "inputs_sha256": {p.name: _sha(p) for p in sorted(Path(cfg.data_dir).glob("*.csv"))}}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_table(rows) -> str:
    lines = ["| # | route | trip | verdict | reason | action | note |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['route_id']} {r['route_name']} | {r['trip_id']} ({r['vehicle_id']}, op {r['operator_id']}, "
                     f"{r['riders']} riders) | `{r['verdict']}` | {r['reason']} | {r['action']} | {r['note']} |")
    return "\n".join(lines)

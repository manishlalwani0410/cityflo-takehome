"""Pipeline orchestration: load -> knowledge cut + clean -> features -> decide -> retrospective -> write."""
from __future__ import annotations

import logging
import sys
from collections import Counter

from . import config as cfgmod
from .clean import clean
from .decide import decide
from .features import build
from .load import InputError, load
from .report import render_table, verdict_rows, write_all
from .retro import retrospective

log = logging.getLogger("lateness")


def run(cfg):
    ds = load(cfg.data_dir)
    c = clean(ds.pings, cfg.as_of, cfg.th)
    feats, _, f_issues = build(ds, c.accepted, c.heartbeat, cfg)
    if not feats:
        raise InputError(f"no trips scheduled on {cfg.service_date} (as-of {cfg.as_of.isoformat()})")
    verdicts = [decide(f, cfg.th) for f in feats]
    rows = verdict_rows(feats, verdicts)
    retro = retrospective(ds, c, feats, cfg)
    issues = ds.issues + c.issues + f_issues
    counts = {
        "pings_total": len(ds.pings) + sum(1 for i in ds.issues if i.source == "gps_pings.csv" and i.severity == "REJECT"),
        "pings_known_at_as_of": sum(1 for p in ds.pings if p.recv <= cfg.as_of),
        "pings_held_recorded_before_received_after": len(c.held),
        "pings_accepted_for_position": len(c.accepted),
        "live_trips": len(feats),
        "verdicts": dict(sorted(Counter(v.verdict.split(" ")[0] for v in verdicts).items())),
        "issues_by_code": dict(sorted(Counter(i.code for i in issues).items())),
    }
    write_all(cfg, rows, retro, issues, counts)
    return rows, retro, issues, counts


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        cfg = cfgmod.from_args(argv)
        rows, retro, issues, counts = run(cfg)
    except (cfgmod.ConfigError, InputError) as e:
        log.error("%s", e)
        return 2
    print(f"As of {cfg.as_of.isoformat()}  (service date {cfg.service_date}, knowledge clock = received_at)\n")
    print(render_table(rows))
    print("\nRetrospective (hindsight; NOT used for the verdicts above):")
    for r in retro:
        if r.finding or r.hindsight_verdict.split(" (")[0] != next(x["verdict"] for x in rows if x["trip_id"] == r.trip_id):
            print(f"  {r.trip_id} route {r.route_id}: hindsight={r.hindsight_verdict}  {r.finding}")
    print(f"\nCounts: {counts}")
    print(f"Outputs: {cfg.out_dir}")
    return 0

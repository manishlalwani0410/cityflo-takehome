"""Retrospective diagnostics: what the full extract reveals AFTER the cut-off.

Strictly separated from the live verdict. Nothing here is an input to decide(). It exists to answer
"was the 07:45 call right?" later, e.g. the Route 14 silence turning out to be a 90-minute delivery lag.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median

from .clean import clean
from .config import Config
from .decide import decide
from .features import build


@dataclass
class Retro:
    trip_id: str
    route_id: int
    held_pings: int = 0                 # recorded <= as_of, received after
    held_first_rec: str = ""
    held_last_rec: str = ""
    held_median_lag_s: float | None = None
    finding: str = ""
    hindsight_verdict: str = ""         # verdict if the server had received every fix instantly
    hindsight_late_min: float | None = None


def retrospective(ds, cleaned_live, live_feats, cfg: Config):
    # Hindsight world: every fix recorded by as_of, as if delivered instantly (recv := rec).
    oracle_pings = [replace(p, recv=p.rec) for p in ds.pings if p.rec <= cfg.as_of]
    oc = clean(oracle_pings, cfg.as_of, cfg.th)
    o_feats, _, _ = build(ds, oc.accepted, oc.heartbeat, cfg)
    o_by = {f.trip.trip_id: f for f in o_feats}

    from .features import assign
    held_by = assign(ds.trips, cleaned_live.held, cfg.th)
    out = []
    for f in live_feats:
        t = f.trip
        r = Retro(t.trip_id, t.route_id)
        held = sorted(held_by.get(t.trip_id, []), key=lambda p: p.rec)
        if held:
            r.held_pings = len(held)
            r.held_first_rec = held[0].rec.isoformat()
            r.held_last_rec = held[-1].rec.isoformat()
            r.held_median_lag_s = median((p.recv - p.rec).total_seconds() for p in held)
        of = o_by.get(t.trip_id)
        if of is not None:
            ov = decide(of, cfg.th)
            r.hindsight_verdict = f"{ov.verdict} ({ov.reason})"
            r.hindsight_late_min = round(of.late_now, 1) if of.late_now is not None else None
        live_silent = f.silence_min is not None and f.silence_min > cfg.th.silence_min
        if live_silent and held:
            r.finding = (f"DELIVERY_DELAY: device kept fixing until {held[-1].rec:%H:%M:%S}; "
                         f"those fixes arrived ~{r.held_median_lag_s/60:.0f} min late. Live silence was a "
                         f"delivery fault, not a stopped vehicle.")
        elif live_silent:
            later = [p for p in ds.pings if p.vehicle_id == t.vehicle_id and p.rec > cfg.as_of]
            r.finding = ("VEHICLE_SILENT_CONFIRMED: no fix recorded during the silence arrived later either"
                         + (f"; device resumed at {min(p.rec for p in later):%H:%M:%S}" if later else
                            "; device never reported again in the extract"))
        elif held:
            r.finding = f"minor: {len(held)} fix(es) in flight at cut-off"
        out.append(r)
    return out

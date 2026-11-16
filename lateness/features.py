"""Per-trip features as of the cut-off. Pure functions of (trips, cleaned telemetry, geometry, config).

Clock usage (the rule that matters most):
  - recv (received_at) decides WHAT WE KNOW and WHEN: silence, recency counts, the knowledge cut.
  - rec (recorded_at) is the time the bus was at that position: used for the lateness arithmetic,
    ordering fixes along the trace, and departure detection.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import geometry
from .config import Config
from .load import Dataset, Issue, Trip


@dataclass(frozen=True)
class Fix:
    ping_row: int
    ping_id: str
    rec: datetime
    recv: datetime
    along_m: float
    frac: float
    late_min: float


@dataclass
class TripFeatures:
    trip: Trip
    route_name: str
    operator_id: int | None
    riders: int
    minutes_since_start: float
    n_fixes: int = 0                  # accepted, on-route fixes known at as-of
    n_heartbeats: int = 0             # any ping received from the device for this trip
    last_recv: datetime | None = None
    silence_min: float | None = None  # as_of - last_recv  (knowledge clock)
    n_recent: int = 0                 # accepted fixes RECEIVED in (as_of - silence_min, as_of]
    last_rec: datetime | None = None
    frac_done: float | None = None
    max_along_m: float | None = None
    late_now: float | None = None     # median of the last k fixes, minutes vs schedule
    late_spread: float | None = None  # max-min of those k: a stability signal, not a verdict input
    departed_at: datetime | None = None   # first fix beyond origin_radius_m
    arrived_at: datetime | None = None    # first fix at/after completed_frac
    arrival_late: float | None = None
    first_fix: datetime | None = None
    hist_late: float | None = None    # median Mon/Tue lateness at the same progress fraction
    hist_n: int = 0
    riders_promised_before_last_fix: int = 0  # rider impact (ETA), reported only


def assign(trips, pings, th, rec_attr="rec"):
    """Map each ping to at most one trip of its vehicle: the trip whose scheduled window is nearest."""
    by_vehicle = {}
    for t in trips:
        by_vehicle.setdefault(t.vehicle_id, []).append(t)
    out = {t.trip_id: [] for t in trips}
    pre, post = timedelta(minutes=th.trip_pre_buffer_min), timedelta(minutes=th.trip_post_buffer_min)
    for p in pings:
        ts = getattr(p, rec_attr)
        cands = [t for t in by_vehicle.get(p.vehicle_id, []) if t.start - pre <= ts <= t.end + post]
        if not cands:
            continue
        dist = lambda t: 0 if t.start <= ts <= t.end else min(abs((ts - t.start).total_seconds()),
                                                                abs((ts - t.end).total_seconds()))
        best = min(cands, key=lambda t: (dist(t), t.start, t.trip_id))
        out[best.trip_id].append(p)
    return out


def _fixes(trip, route, line, pings, th, issues):
    fixes = []
    for p in pings:
        along, off = line.project(p.lat, p.lon)
        if off > th.off_route_max_m:
            issues.append(Issue("gps_pings.csv", p.row, p.ping_id, "REJECT", "OFF_ROUTE",
                                f"{off:.0f} m from route {route.route_id} polyline"))
            continue
        frac = along / line.length_m
        due = trip.start + timedelta(minutes=frac * route.runtime_min)
        fixes.append(Fix(p.row, p.ping_id, p.rec, p.recv, along, frac, (p.rec - due).total_seconds() / 60))
    fixes.sort(key=lambda f: (f.rec, f.ping_row))
    return fixes


def _until_arrival(fixes, th):
    """History only: drop the dwell at the destination, which otherwise reads as growing lateness."""
    out = []
    for f in fixes:
        out.append(f)
        if f.frac >= th.completed_frac:
            break
    return out


def build(ds: Dataset, accepted, heartbeat, cfg: Config):
    th, as_of = cfg.th, cfg.as_of
    lines = {rid: geometry.build(rid, st) for rid, st in ds.stops.items()}
    issues: list[Issue] = []
    fix_assign = assign(ds.trips, accepted, th)
    hb_assign = assign(ds.trips, heartbeat, th)

    fixes_by_trip = {}
    for t in ds.trips:
        fixes_by_trip[t.trip_id] = _fixes(t, ds.routes[t.route_id], lines[t.route_id], fix_assign[t.trip_id], th, issues)

    # history = earlier service dates only, and only what had been received by as_of (no leakage)
    hist = {}
    for t in ds.trips:
        if t.service_date < cfg.service_date:
            hist.setdefault(t.route_id, []).extend(_until_arrival(fixes_by_trip[t.trip_id], th))

    op_of = {}
    for p in heartbeat:
        op_of.setdefault(p.vehicle_id, p.operator_id)
    riders = {}
    for b in ds.bookings:
        riders.setdefault(b.trip_id, []).append(b)

    feats = []
    window = timedelta(minutes=th.silence_min)
    for t in sorted(ds.trips, key=lambda t: (t.route_id, t.start, t.trip_id)):
        if t.service_date != cfg.service_date:
            continue
        fx = fixes_by_trip[t.trip_id]
        hb = hb_assign[t.trip_id]
        f = TripFeatures(t, ds.routes[t.route_id].name, op_of.get(t.vehicle_id), len(riders.get(t.trip_id, [])),
                         (as_of - t.start).total_seconds() / 60)
        f.n_fixes, f.n_heartbeats = len(fx), len(hb)
        if hb:
            f.last_recv = max(p.recv for p in hb)
            f.silence_min = (as_of - f.last_recv).total_seconds() / 60
        if fx:
            last_k = fx[-th.smoothing_fixes:]
            f.last_rec = fx[-1].rec
            f.frac_done = fx[-1].frac
            f.max_along_m = max(x.along_m for x in fx)
            f.late_now = statistics.median(x.late_min for x in last_k)
            f.late_spread = max(x.late_min for x in last_k) - min(x.late_min for x in last_k)
            f.first_fix = fx[0].rec
            f.n_recent = sum(1 for x in fx if as_of - window < x.recv <= as_of)
            dep = next((x for x in fx if x.along_m > th.origin_radius_m), None)
            f.departed_at = dep.rec if dep else None
            arr = next((x for x in fx if x.frac >= th.completed_frac), None)
            if arr is not None:
                # lateness of an arrived bus = at the moment it arrived, not after dwelling at the terminus
                f.arrived_at, f.arrival_late = arr.rec, arr.late_min
            h = [x.late_min for x in hist.get(t.route_id, []) if abs(x.frac - f.frac_done) <= th.hist_frac_window]
            f.hist_n = len(h)
            f.hist_late = statistics.median(h) if h else None
            # rider impact only: bookings whose promised time at their stop has passed while the bus hasn't
            stop_along = {sid: lines[t.route_id].cum[i] for i, sid in enumerate(lines[t.route_id].stop_ids)}
            f.riders_promised_before_last_fix = sum(
                1 for b in riders.get(t.trip_id, [])
                if b.promised_eta <= as_of and stop_along.get(b.boarding_stop_id, 0) > f.max_along_m)
        feats.append(f)
    return feats, fixes_by_trip, issues

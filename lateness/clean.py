"""Telemetry cleaning. Runs ONLY on pings the server had received by the as-of instant.

Order matters and is deliberate:
  1. Knowledge cut: recv <= as_of. Everything else is "held" and goes to the retrospective report only.
     Applying this first means no later step (dedup, jump filter) can be influenced by future rows.
  2. Per-row checks (service area, device clock ahead of server clock, speed field sanity).
  3. Exact-duplicate fixes per vehicle (same recorded_at + position): keep the first received.
  4. Position-jump filter against the last ACCEPTED fix, in device-time order.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .config import Thresholds
from .geometry import distance_m
from .load import Issue, Ping

log = logging.getLogger(__name__)


@dataclass
class CleanResult:
    accepted: list                 # pings usable for position/progress
    heartbeat: list                # pings that prove the device->server path was alive (accepted + position-rejected)
    held: list                     # recorded <= as_of but received after: retrospective only
    issues: list = field(default_factory=list)


def clean(pings: list, as_of: datetime, th: Thresholds) -> CleanResult:
    known = [p for p in pings if p.recv <= as_of]
    held = [p for p in pings if p.recv > as_of and p.rec <= as_of]
    issues: list[Issue] = []
    log.info("knowledge cut at %s: %d known, %d held (recorded before, received after), %d future",
             as_of.isoformat(), len(known), len(held), len(pings) - len(known) - len(held))

    def reject(p, code, detail):
        issues.append(Issue("gps_pings.csv", p.row, p.ping_id, "REJECT", code, detail))

    # ping_id is not a unique key in the source; report collisions, key internally on file row
    by_id = defaultdict(list)
    for p in known:
        by_id[p.ping_id].append(p)
    for pid, ps in sorted(by_id.items()):
        if len(ps) > 1:
            for p in ps:
                issues.append(Issue("gps_pings.csv", p.row, pid, "WARN", "DUPLICATE_PING_ID",
                                    f"ping_id used by {len(ps)} rows ({', '.join(sorted({q.vehicle_id for q in ps}))})"))

    lat_lo, lat_hi, lon_lo, lon_hi = th.service_area
    heartbeat, stage1 = [], []
    for p in known:
        if (p.rec - p.recv).total_seconds() > th.max_clock_ahead_s:
            reject(p, "DEVICE_CLOCK_AHEAD", f"recorded_at {p.rec.isoformat()} after received_at {p.recv.isoformat()}")
            continue
        heartbeat.append(p)       # the server did receive something from this device at p.recv
        if not (lat_lo <= p.lat <= lat_hi and lon_lo <= p.lon <= lon_hi):
            reject(p, "OUTSIDE_SERVICE_AREA", f"({p.lat}, {p.lon})")
            continue
        if p.speed_kmph is not None and not (0 <= p.speed_kmph <= th.max_device_speed_kmph):
            # speed is not used for lateness; the position may still be good, so flag but keep
            issues.append(Issue("gps_pings.csv", p.row, p.ping_id, "WARN", "IMPLAUSIBLE_SPEED_FIELD",
                                f"speed_kmph={p.speed_kmph}"))
        stage1.append(p)

    seen = {}
    stage2 = []
    for p in sorted(stage1, key=lambda p: (p.recv, p.row)):
        k = (p.vehicle_id, p.rec, p.lat, p.lon)
        if k in seen:
            reject(p, "DUPLICATE_FIX", f"same fix as row {seen[k].row} ({seen[k].ping_id})")
            continue
        seen[k] = p
        stage2.append(p)

    accepted = []
    by_vehicle = defaultdict(list)
    for p in stage2:
        by_vehicle[p.vehicle_id].append(p)
    for vid in sorted(by_vehicle):
        last, streak = None, 0
        for p in sorted(by_vehicle[vid], key=lambda p: (p.rec, p.recv, p.row)):
            if last is not None:
                dt = (p.rec - last.rec).total_seconds()
                d = distance_m(last.lat, last.lon, p.lat, p.lon)
                if dt <= 0 and d > 1:
                    reject(p, "CONFLICTING_FIX", f"same device time as row {last.row}, {d:.0f} m apart")
                    continue
                kmph = 3.6 * d / dt if dt > 0 else 0.0
                if kmph > th.max_implied_kmph and streak < th.jump_reset_after:
                    streak += 1
                    reject(p, "POSITION_JUMP", f"{kmph:.0f} km/h implied from row {last.row}")
                    continue
                if streak >= th.jump_reset_after:
                    issues.append(Issue("gps_pings.csv", p.row, p.ping_id, "WARN", "JUMP_FILTER_REANCHORED",
                                        f"accepted after {streak} consecutive rejections"))
            accepted.append(p)
            last, streak = p, 0

    accepted.sort(key=lambda p: (p.vehicle_id, p.rec, p.row))
    heartbeat.sort(key=lambda p: (p.vehicle_id, p.recv, p.row))
    return CleanResult(accepted, heartbeat, held, issues)

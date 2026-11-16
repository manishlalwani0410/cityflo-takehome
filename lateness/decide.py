"""Decision rules. Pure function of TripFeatures + thresholds -> Verdict. No I/O, no clock reads.

Checks run in a fixed order; the first that fires wins. PUSH_LATE is reached only after every reason
NOT to push has been ruled out (Manish's rule: crossing the threshold alone is not enough).
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Thresholds
from .features import TripFeatures

# worry-order: problems a push can't fix first, then confirmed lateness, then uncertainty, then fine
RANK = {
    "ORIGIN_STATIONARY": 1,
    "TELEMETRY_SILENT": 2,
    "NO_TELEMETRY": 2,
    "LATE_VS_SCHEDULE": 3,
    "SPARSE_TELEMETRY": 4,
    "AT_ORIGIN_WITHIN_GRACE": 4,
    "AWAITING_FIRST_PING": 5,
    "ON_SCHEDULE": 9, "ARRIVED": 9, "NOT_DUE": 9,
}


@dataclass(frozen=True)
class Verdict:
    verdict: str        # HOLD | PUSH_LATE <n> min | CALL_DRIVER | NO_VERDICT
    reason: str
    action: str
    note: str

    @property
    def rank(self) -> int:
        return RANK.get(self.reason, 8)


def _fmt(m):
    return f"{m:+.1f}"


def decide(f: TripFeatures, th: Thresholds) -> Verdict:
    since = f.minutes_since_start

    # 1. not due yet
    if since < 0:
        return Verdict("HOLD", "NOT_DUE", "none", f"departs in {-since:.0f} min")

    # 2. nothing ever received for this trip
    if f.n_heartbeats == 0:
        if since > th.silence_min:
            return Verdict("CALL_DRIVER", "NO_TELEMETRY", "driver: confirm departure; telemetry: check device",
                           f"due {since:.0f} min ago, nothing received from {f.trip.vehicle_id}")
        return Verdict("NO_VERDICT", "AWAITING_FIRST_PING", "watch", f"due {since:.0f} min ago, no ping yet")

    # 3. arrived (by the first fix at the terminus) -> nothing left to act on
    if f.arrived_at is not None:
        return Verdict("HOLD", "ARRIVED", "none",
                       f"reached terminus at {f.arrived_at:%H:%M}, {_fmt(f.arrival_late)} min vs schedule")

    # 4. silence on the knowledge clock. At the cut-off we cannot tell a stopped vehicle from a delivery
    #    fault: both look like "nothing received". One call resolves both, so this is CALL_DRIVER, and the
    #    telemetry team is told in the same action. Whether it was delivery is answered retrospectively.
    if f.silence_min > th.silence_min:
        where = f"{f.frac_done:.0%} along" if f.frac_done is not None else "position unknown"
        return Verdict("CALL_DRIVER", "TELEMETRY_SILENT",
                       "driver: confirm vehicle status; telemetry: check device/ingestion",
                       f"nothing received for {f.silence_min:.0f} min (last {f.last_recv:%H:%M:%S}, {where}); "
                       f"vehicle stop vs delivery fault not distinguishable at cut-off")

    # 5. no usable position (heartbeats only, e.g. every fix off-route) -> cannot say anything
    if f.n_fixes == 0:
        return Verdict("NO_VERDICT", "SPARSE_TELEMETRY", "telemetry: check device",
                       f"{f.n_heartbeats} pings received but none usable for position")

    # 6. on the way? device near origin long after departure time
    if f.max_along_m < th.origin_radius_m:
        if since > th.silence_min:
            return Verdict("NO_VERDICT", "ORIGIN_STATIONARY", "ops: confirm departure",
                           f"device within {f.max_along_m:.0f} m of origin {since:.0f} min after departure time; "
                           f"device position may not be bus position")
        if f.late_now >= th.push_min:
            return Verdict("NO_VERDICT", "AT_ORIGIN_WITHIN_GRACE", "watch",
                           f"not yet departed, {since:.0f} min after departure time")

    # 7. reliability gate (applies to HOLD as well as PUSH: a confident 'fine' needs data too)
    if f.n_recent < th.min_recent_fixes:
        return Verdict("NO_VERDICT", "SPARSE_TELEMETRY", "watch",
                       f"only {f.n_recent} usable fixes received in last {th.silence_min:.0f} min")

    hist = f", Mon/Tue at this point {_fmt(f.hist_late)} (n={f.hist_n})" if f.hist_late is not None else ", no history"
    # 8. late
    if f.late_now >= th.push_min:
        n = int(f.late_now + 0.5)
        return Verdict(f"PUSH_LATE {n} min", "LATE_VS_SCHEDULE", "rider push",
                       f"{_fmt(f.late_now)} min at {f.frac_done:.0%} along{hist}")
    # 9. fine (an early bus is reported, not pushed: see DECISION_RECORD D2)
    early = " RUNNING EARLY" if f.late_now <= -th.push_min else ""
    return Verdict("HOLD", "ON_SCHEDULE", "none", f"{_fmt(f.late_now)} min at {f.frac_done:.0%} along{hist}{early}")

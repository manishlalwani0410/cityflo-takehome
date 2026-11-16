"""Run configuration: as-of timestamp, thresholds, and paths. Everything tunable lives here."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30), "IST")


class ConfigError(ValueError):
    pass


def parse_aware(value: str, what: str = "timestamp") -> datetime:
    """Parse an ISO-8601 timestamp that MUST carry a UTC offset. Naive times are rejected, never guessed."""
    try:
        ts = datetime.fromisoformat(value.strip())
    except ValueError as e:
        raise ConfigError(f"{what} {value!r} is not ISO-8601 (expected e.g. 2026-06-17 07:45:00+05:30)") from e
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ConfigError(f"{what} {value!r} has no UTC offset; refusing to assume a timezone")
    return ts


@dataclass(frozen=True)
class Thresholds:
    silence_min: float = 10.0          # no ping RECEIVED for longer than this during an active trip -> silent
    push_min: float = 5.0              # schedule lateness at/above this is push-worthy (subject to gates)
    origin_radius_m: float = 300.0     # "has departed" = furthest progress along route beyond this
    min_recent_fixes: int = 3          # reliability gate: fixes RECEIVED in the last `silence_min` minutes
    completed_frac: float = 0.97       # progress fraction treated as arrived
    smoothing_fixes: int = 3           # lateness = median over this many most recent fixes
    max_implied_kmph: float = 120.0    # implied speed from last accepted fix above this = position jump
    jump_reset_after: int = 5          # consecutive rejections before re-anchoring the jump filter
    max_device_speed_kmph: float = 150.0
    max_clock_ahead_s: float = 60.0    # recorded_at later than received_at by more than this = device clock bad
    off_route_max_m: float = 1000.0    # fix further than this from the route polyline is not trusted for progress
    hist_frac_window: float = 0.05     # history match window on progress fraction
    trip_pre_buffer_min: float = 20.0  # pings this long before scheduled_start can belong to the trip
    trip_post_buffer_min: float = 45.0
    service_area: tuple = (18.8, 19.4, 72.7, 73.2)  # lat_min, lat_max, lon_min, lon_max (Mumbai)


@dataclass(frozen=True)
class Config:
    as_of: datetime
    data_dir: Path
    out_dir: Path
    tz: timezone = IST
    th: Thresholds = field(default_factory=Thresholds)

    @property
    def service_date(self) -> date:
        """The live service date is the as-of date in the operating timezone, never a hardcoded date."""
        return self.as_of.astimezone(self.tz).date()

    def as_dict(self) -> dict:
        d = asdict(self.th)
        d["service_area"] = list(self.th.service_area)
        return {"as_of": self.as_of.isoformat(), "data_dir": str(self.data_dir), "out_dir": str(self.out_dir),
                "tz": str(self.tz), "thresholds": d}


def from_args(argv=None) -> Config:
    p = argparse.ArgumentParser(description="Cityflo as-of lateness verdicts")
    p.add_argument("--as-of", required=True, help="cut-off with offset, e.g. '2026-06-17 07:45:00+05:30'")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--out-dir", default=None, help="default: outputs/<as-of>")
    for f, v in asdict(Thresholds()).items():
        if f == "service_area":
            continue
        p.add_argument("--" + f.replace("_", "-"), type=type(v), default=v)
    a = p.parse_args(argv)
    as_of = parse_aware(a.as_of, "--as-of")
    th = Thresholds(**{f: getattr(a, f) for f in asdict(Thresholds()) if f != "service_area"})
    tag = as_of.astimezone(IST).strftime("%Y%m%dT%H%M%S%z")
    out = Path(a.out_dir) if a.out_dir else Path("outputs") / tag
    return Config(as_of=as_of, data_dir=Path(a.data_dir), out_dir=out, th=th)

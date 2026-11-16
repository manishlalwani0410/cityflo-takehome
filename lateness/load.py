"""Load + schema-validate the five CSVs. Row-level parse failures become DataQualityIssues, never silent drops.

Reference tables (routes, stops, trips) are strict: a malformed row there means the timetable itself is
untrustworthy, so we fail loudly. Telemetry and bookings are lenient: bad rows are recorded and excluded.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

from .config import parse_aware, ConfigError

log = logging.getLogger(__name__)

SCHEMAS = {
    "routes": ["route_id", "route_name", "origin_stop", "dest_stop", "scheduled_runtime_min", "stops_count"],
    "stops": ["stop_id", "stop_name", "lat", "lon", "route_id", "seq"],
    "trips": ["trip_id", "route_id", "vehicle_id", "service_date", "scheduled_start", "scheduled_end", "direction"],
    "bookings": ["booking_id", "trip_id", "rider_id", "boarding_stop_id", "booked_at", "promised_eta"],
    "gps_pings": ["ping_id", "vehicle_id", "operator_id", "lat", "lon", "speed_kmph", "recorded_at", "received_at"],
}
STRICT = {"routes", "stops", "trips"}


class InputError(ValueError):
    pass


@dataclass
class Issue:
    source: str        # file name
    row: int           # 1-based data row (0 = file level)
    record_id: str
    severity: str      # REJECT (excluded from analysis) | WARN (kept, flagged) | INFO
    code: str
    detail: str


@dataclass(frozen=True)
class Route:
    route_id: int
    name: str
    runtime_min: float
    stops_count: int


@dataclass(frozen=True)
class Stop:
    stop_id: str
    route_id: int
    seq: int
    lat: float
    lon: float


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: int
    vehicle_id: str
    service_date: date
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Booking:
    booking_id: str
    trip_id: str
    boarding_stop_id: str
    promised_eta: datetime


@dataclass(frozen=True)
class Ping:
    row: int           # file row: the internal key (ping_id is NOT unique in the source)
    ping_id: str
    vehicle_id: str
    operator_id: int
    lat: float
    lon: float
    speed_kmph: float | None
    rec: datetime      # device clock: when the fix was taken
    recv: datetime     # server clock: when we knew about it  <- the knowledge clock


@dataclass
class Dataset:
    routes: dict
    stops: dict          # route_id -> [Stop] ordered by seq
    trips: list
    bookings: list
    pings: list
    issues: list


def _read(path: Path, name: str):
    if not path.exists():
        raise InputError(f"missing input file: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        header = [h.strip() for h in (r.fieldnames or [])]
        missing = [c for c in SCHEMAS[name] if c not in header]
        if missing:
            raise InputError(f"{path.name}: missing required columns {missing} (found {header})")
        rows = [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in r]
    log.info("loaded %s: %d rows", path.name, len(rows))
    return rows


def load(data_dir: Path) -> Dataset:
    issues: list[Issue] = []
    raw = {n: _read(Path(data_dir) / f"{n}.csv", n) for n in SCHEMAS}
    for n in STRICT:
        if not raw[n]:
            raise InputError(f"{n}.csv has no rows; cannot evaluate without a timetable")

    def strict(name, i, fn):
        try:
            return fn()
        except (ValueError, ConfigError) as e:
            raise InputError(f"{name}.csv row {i}: {e}") from e

    routes = {}
    for i, r in enumerate(raw["routes"], 1):
        rt = strict("routes", i, lambda: Route(int(r["route_id"]), r["route_name"],
                                                float(r["scheduled_runtime_min"]), int(r["stops_count"])))
        if rt.runtime_min <= 0:
            raise InputError(f"routes.csv row {i}: non-positive scheduled_runtime_min")
        routes[rt.route_id] = rt

    stops: dict[int, list] = {}
    for i, r in enumerate(raw["stops"], 1):
        s = strict("stops", i, lambda: Stop(r["stop_id"], int(r["route_id"]), int(r["seq"]),
                                             float(r["lat"]), float(r["lon"])))
        stops.setdefault(s.route_id, []).append(s)
    for rid, lst in stops.items():
        lst.sort(key=lambda s: s.seq)
        if [s.seq for s in lst] != list(range(1, len(lst) + 1)):
            raise InputError(f"stops.csv: route {rid} stop seq is not 1..n contiguous")
        if rid in routes and routes[rid].stops_count != len(lst):
            issues.append(Issue("stops.csv", 0, str(rid), "WARN", "STOPS_COUNT_MISMATCH",
                                f"routes.csv says {routes[rid].stops_count}, stops.csv has {len(lst)}"))

    trips = []
    seen = set()
    for i, r in enumerate(raw["trips"], 1):
        t = strict("trips", i, lambda: Trip(r["trip_id"], int(r["route_id"]), r["vehicle_id"],
                                             date.fromisoformat(r["service_date"]),
                                             parse_aware(r["scheduled_start"], "scheduled_start"),
                                             parse_aware(r["scheduled_end"], "scheduled_end")))
        if t.trip_id in seen:
            raise InputError(f"trips.csv row {i}: duplicate trip_id {t.trip_id}")
        if t.route_id not in routes or len(stops.get(t.route_id, [])) < 2:
            raise InputError(f"trips.csv row {i}: route {t.route_id} missing from routes/stops")
        if t.end <= t.start:
            raise InputError(f"trips.csv row {i}: scheduled_end not after scheduled_start")
        seen.add(t.trip_id)
        trips.append(t)

    stop_ids = {s.stop_id: s for lst in stops.values() for s in lst}
    trip_ids = {t.trip_id: t for t in trips}
    bookings = []
    for i, r in enumerate(raw["bookings"], 1):
        try:
            b = Booking(r["booking_id"], r["trip_id"], r["boarding_stop_id"], parse_aware(r["promised_eta"]))
        except (ValueError, ConfigError) as e:
            issues.append(Issue("bookings.csv", i, r.get("booking_id", ""), "REJECT", "UNPARSEABLE_ROW", str(e)))
            continue
        t = trip_ids.get(b.trip_id)
        s = stop_ids.get(b.boarding_stop_id)
        if t is None:
            issues.append(Issue("bookings.csv", i, b.booking_id, "REJECT", "UNKNOWN_TRIP", b.trip_id))
            continue
        if s is None or s.route_id != t.route_id:
            issues.append(Issue("bookings.csv", i, b.booking_id, "REJECT", "STOP_NOT_ON_ROUTE",
                                f"{b.boarding_stop_id} not on route {t.route_id}"))
            continue
        bookings.append(b)

    pings = []
    for i, r in enumerate(raw["gps_pings"], 1):
        pid = r.get("ping_id", "")
        try:
            recv = parse_aware(r["received_at"], "received_at")
        except ConfigError as e:
            issues.append(Issue("gps_pings.csv", i, pid, "REJECT", "UNPARSEABLE_RECEIVED_AT", str(e)))
            continue
        try:
            rec = parse_aware(r["recorded_at"], "recorded_at")
        except ConfigError as e:
            issues.append(Issue("gps_pings.csv", i, pid, "REJECT", "UNPARSEABLE_RECORDED_AT", str(e)))
            continue
        try:
            lat, lon, op = float(r["lat"]), float(r["lon"]), int(r["operator_id"])
        except ValueError as e:
            issues.append(Issue("gps_pings.csv", i, pid, "REJECT", "UNPARSEABLE_POSITION", str(e)))
            continue
        if not r["vehicle_id"]:
            issues.append(Issue("gps_pings.csv", i, pid, "REJECT", "MISSING_VEHICLE", ""))
            continue
        try:
            spd = float(r["speed_kmph"]) if r["speed_kmph"] else None
        except ValueError:
            spd = None
        pings.append(Ping(i, pid, r["vehicle_id"], op, lat, lon, spd, rec, recv))

    if not pings:
        issues.append(Issue("gps_pings.csv", 0, "", "WARN", "NO_TELEMETRY", "no usable pings in file"))
    return Dataset(routes, stops, trips, bookings, pings, issues)

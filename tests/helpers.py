"""Synthetic-data builders for tests. One straight north-south route keeps the arithmetic checkable by hand."""
from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lateness.config import Config, Thresholds

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-06-17"
START = datetime(2026, 6, 17, 7, 0, tzinfo=IST)
# route 1: 3 stops along lon 72.85, lat 19.00 -> 19.10 (~11 km), 60 min runtime
STOPS = [("S-0101", 19.00), ("S-0102", 19.05), ("S-0103", 19.10)]
RUNTIME = 60


def ts(dt):
    return dt.isoformat(sep=" ")


def pos(frac):
    return 19.00 + 0.10 * frac, 72.85


class World:
    """Accumulates rows, then writes the 5 CSVs into a temp dir."""

    def __init__(self):
        self.trips, self.bookings, self.pings = [], [], []
        self._n = 0

    def trip(self, trip_id="T1", vehicle="V-1", start=START, day=DAY, route=1):
        self.trips.append([trip_id, route, vehicle, day, ts(start), ts(start + timedelta(minutes=RUNTIME)), "inbound"])
        return self

    def ping(self, vehicle, frac, rec, recv=None, op=5, lat=None, lon=None, speed=20.0, ping_id=None, rec_raw=None):
        self._n += 1
        la, lo = pos(frac)
        self.pings.append([ping_id or f"P-{self._n:07d}", vehicle, op, la if lat is None else lat,
                           lo if lon is None else lon, speed, rec_raw or ts(rec),
                           ts(recv or rec + timedelta(seconds=2))])
        return self

    def trace(self, vehicle, start, minutes, frac_at, every_s=20, lag_s=2, op=5):
        """Pings every `every_s` from start for `minutes`; frac_at(t_min) gives progress."""
        n = int(minutes * 60 / every_s)
        for i in range(n + 1):
            t = start + timedelta(seconds=i * every_s)
            self.ping(vehicle, frac_at(i * every_s / 60), t, t + timedelta(seconds=lag_s), op=op)
        return self

    def booking(self, trip_id, stop_id, eta):
        self.bookings.append([f"B{len(self.bookings)+1}", trip_id, "R1", stop_id, ts(START - timedelta(days=1)), ts(eta)])
        return self

    def write(self, root: Path | None = None, drop=()):
        root = Path(root or tempfile.mkdtemp())
        files = {
            "routes": (["route_id", "route_name", "origin_stop", "dest_stop", "scheduled_runtime_min", "stops_count"],
                       [[1, "A → B", "A", "B", RUNTIME, 3]]),
            "stops": (["stop_id", "stop_name", "lat", "lon", "route_id", "seq"],
                      [[sid, sid, la, 72.85, 1, i + 1] for i, (sid, la) in enumerate(STOPS)]),
            "trips": (["trip_id", "route_id", "vehicle_id", "service_date", "scheduled_start", "scheduled_end",
                       "direction"], self.trips),
            "bookings": (["booking_id", "trip_id", "rider_id", "boarding_stop_id", "booked_at", "promised_eta"],
                         self.bookings),
            "gps_pings": (["ping_id", "vehicle_id", "operator_id", "lat", "lon", "speed_kmph", "recorded_at",
                           "received_at"], self.pings),
        }
        for name, (hdr, rows) in files.items():
            if name in drop:
                continue
            with (root / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(hdr)
                w.writerows(rows)
        return root


def cfg(data_dir, as_of, **th):
    return Config(as_of=as_of, data_dir=Path(data_dir), out_dir=Path(data_dir) / "out", th=Thresholds(**th))


def on_time(t_min):
    return min(1.0, t_min / RUNTIME)

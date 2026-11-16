"""Regression tests against the supplied extract (data/). Pins the 07:45 verdicts and the as-of guarantee."""
from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lateness.config import Config, Thresholds, parse_aware  # noqa: E402
from lateness.pipeline import run  # noqa: E402

AS_OF = parse_aware("2026-06-17 07:45:00+05:30")
LIVE_KEYS = [k for k in ("verdict", "reason", "action", "note", "late_now_min", "frac_done", "silence_min",
                         "n_recent", "n_fixes", "n_heartbeats", "last_received", "last_fix_recorded",
                         "max_along_m", "hist_late_min", "worry_rank")]


def _run(data_dir, out=None):
    out = Path(out or tempfile.mkdtemp())
    return run(Config(as_of=AS_OF, data_dir=Path(data_dir), out_dir=out, th=Thresholds())), out


def _copy_data():
    d = Path(tempfile.mkdtemp())
    for f in (ROOT / "data").glob("*.csv"):
        shutil.copy(f, d / f.name)
    return d


class RealExtract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (cls.rows, cls.retro, cls.issues, cls.counts), cls.out = _run(ROOT / "data")
        cls.v = {r["trip_id"]: r for r in cls.rows}

    def test_every_live_trip_present(self):
        with (ROOT / "data" / "trips.csv").open(encoding="utf-8") as fh:
            expected = {r["trip_id"] for r in csv.DictReader(fh) if r["service_date"] == "2026-06-17"}
        self.assertEqual(set(self.v), expected)
        self.assertEqual(len(expected), 7)

    def test_operator_7_kept(self):
        self.assertEqual(self.v["TRIP_019"]["operator_id"], 7)
        self.assertEqual(self.v["TRIP_019"]["riders"], 6)

    def test_route_12_not_forced_on_time(self):
        self.assertEqual(self.v["TRIP_015"]["verdict"], "PUSH_LATE 10 min")

    def test_pinned_0745_verdicts(self):
        got = {t: (r["verdict"], r["reason"]) for t, r in self.v.items()}
        self.assertEqual(got, {
            "TRIP_013": ("HOLD", "ARRIVED"),
            "TRIP_014": ("NO_VERDICT", "ORIGIN_STATIONARY"),
            "TRIP_015": ("PUSH_LATE 10 min", "LATE_VS_SCHEDULE"),
            "TRIP_016": ("CALL_DRIVER", "TELEMETRY_SILENT"),
            "TRIP_017": ("CALL_DRIVER", "TELEMETRY_SILENT"),
            "TRIP_018": ("HOLD", "ON_SCHEDULE"),
            "TRIP_019": ("HOLD", "ON_SCHEDULE"),
        })

    def test_route14_and_route17_treated_by_same_rule(self):
        a, b = self.v["TRIP_016"], self.v["TRIP_017"]
        self.assertEqual((a["verdict"], a["reason"], a["action"]), (b["verdict"], b["reason"], b["action"]))

    def test_route14_has_145_held_pings_reported_only_retrospectively(self):
        r = next(x for x in self.retro if x.trip_id == "TRIP_016")
        self.assertEqual(r.held_pings, 145)
        self.assertTrue(r.finding.startswith("DELIVERY_DELAY"))
        self.assertNotIn("DELIVERY", self.v["TRIP_016"]["note"])
        self.assertNotIn("INGESTION_DELAY", self.v["TRIP_016"]["reason"])

    def test_no_live_input_received_after_cutoff(self):
        for r in self.rows:
            if r["last_received"]:
                self.assertLessEqual(datetime.fromisoformat(r["last_received"]), AS_OF, r["trip_id"])

    def test_outputs_valid(self):
        with (self.out / "verdicts.csv").open(encoding="utf-8") as fh:
            self.assertEqual(len(list(csv.DictReader(fh))), 7)
        lines = (self.out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 7)
        for ln in lines:
            self.assertEqual(json.loads(ln)["as_of"], AS_OF.isoformat())
        json.loads((self.out / "run_meta.json").read_text(encoding="utf-8"))

    def test_data_quality_issues_reported_not_silent(self):
        got = {(i.record_id, i.code) for i in self.issues}
        for expected in [("P-0001797", "UNPARSEABLE_RECORDED_AT"), ("P-0002821", "DUPLICATE_FIX"),
                         ("P-0003148", "OUTSIDE_SERVICE_AREA"), ("P-0003149", "OUTSIDE_SERVICE_AREA"),
                         ("P-0003150", "OUTSIDE_SERVICE_AREA"), ("P-0003995", "IMPLAUSIBLE_SPEED_FIELD"),
                         ("P-0002821", "DUPLICATE_PING_ID")]:
            self.assertIn(expected, got)

    def test_deterministic(self):
        (_, out2) = _run(ROOT / "data")
        for f in ("verdicts.csv", "verdicts.jsonl", "retrospective.csv", "data_quality.csv", "run_meta.json"):
            a = (self.out / f).read_bytes()
            b = (out2 / f).read_bytes()
            if f == "run_meta.json":
                a, b = (json.loads(x)["counts"] for x in (a, b))
            self.assertEqual(a, b, f)


class Route14Regression(unittest.TestCase):
    """The 145 V-04 pings received after 07:45 must not be able to change the 07:45 live verdict."""

    def _live(self, data_dir):
        (rows, *_), _ = _run(data_dir)
        return {r["trip_id"]: {k: r[k] for k in LIVE_KEYS} for r in rows}

    def _rewrite_held(self, fn):
        d = _copy_data()
        p = d / "gps_pings.csv"
        with p.open(newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            hdr, rows = rd.fieldnames, list(rd)
        held = [r for r in rows if r["vehicle_id"] == "V-04" and parse_aware(r["received_at"]) > AS_OF
                and parse_aware(r["recorded_at"]) <= AS_OF]
        self.assertEqual(len(held), 145)
        rows = fn(rows, held)
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=hdr, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        return d

    def test_deleting_held_pings_changes_nothing_live(self):
        base = self._live(ROOT / "data")
        d = self._rewrite_held(lambda rows, held: [r for r in rows if r not in held])
        self.assertEqual(self._live(d), base)

    def test_corrupting_held_pings_changes_nothing_live(self):
        base = self._live(ROOT / "data")

        def corrupt(rows, held):
            for r in held:                       # teleport them to the destination: would read as "arrived early"
                r["lat"], r["lon"] = "19.0", "72.83"
            return rows
        self.assertEqual(self._live(self._rewrite_held(corrupt)), base)

    def test_delivering_held_pings_on_time_would_change_the_verdict(self):
        """Control: proves the test above is sensitive. If they HAD arrived, Route 14 would be on time."""
        def deliver(rows, held):
            for r in held:
                r["received_at"] = r["recorded_at"]
            return rows
        live = self._live(self._rewrite_held(deliver))
        self.assertEqual(live["TRIP_016"]["verdict"], "HOLD")


if __name__ == "__main__":
    unittest.main()

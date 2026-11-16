"""Unit + scenario tests on synthetic data. Run: python -m unittest discover -s tests -v"""
from __future__ import annotations

import contextlib
import io
import logging
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

logging.disable(logging.INFO)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import START, World, cfg, on_time, RUNTIME  # noqa: E402
from lateness.config import ConfigError, parse_aware, from_args  # noqa: E402
from lateness.load import InputError, load  # noqa: E402
from lateness.pipeline import run  # noqa: E402

AS_OF = START + timedelta(minutes=30)


def verdicts(world, as_of=AS_OF, **th):
    rows, retro, issues, counts = run(cfg(world.write(), as_of, **th))
    return {r["trip_id"]: r for r in rows}, retro, issues, counts


def codes(issues):
    return sorted(i.code for i in issues)


class AsOfCutoff(unittest.TestCase):
    def test_ping_received_after_cutoff_is_ignored_even_if_recorded_before(self):
        # bus is on time per what we know; a late-arriving fix claims it is far behind -> must not matter
        w = World().trip().trace("V-1", START, 30, on_time)
        w.ping("V-1", 0.05, AS_OF - timedelta(seconds=5), recv=AS_OF + timedelta(minutes=1))
        v, *_ = verdicts(w)
        self.assertEqual(v["T1"]["verdict"], "HOLD")

    def test_ping_received_exactly_at_cutoff_counts(self):
        w = World().trip().trace("V-1", START, 15, on_time)       # last regular ping 07:15
        w.ping("V-1", 0.5, AS_OF - timedelta(seconds=30), recv=AS_OF)  # arrives exactly at cut-off
        v, *_ = verdicts(w)
        self.assertEqual(v["T1"]["silence_min"], 0.0)

    def test_late_arrivals_create_silence_not_progress(self):
        # device fixes all along but delivery stalls 20 min before cut-off: live view must be silence
        w = World().trip()
        for i in range(91):
            t = START + timedelta(seconds=20 * i)
            lag = 2 if t < AS_OF - timedelta(minutes=20) else 5400
            w.ping("V-1", on_time(20 * i / 60), t, t + timedelta(seconds=lag))
        v, retro, *_ = verdicts(w)
        self.assertEqual((v["T1"]["verdict"], v["T1"]["reason"]), ("CALL_DRIVER", "TELEMETRY_SILENT"))
        r = next(x for x in retro if x.trip_id == "T1")
        self.assertTrue(r.finding.startswith("DELIVERY_DELAY"))
        self.assertEqual(r.held_pings, 61)   # fixes 07:10:00..07:30:00 inclusive, every 20 s
        self.assertIn("ON_SCHEDULE", r.hindsight_verdict)    # hindsight differs; live does not use it

    def test_history_only_uses_pings_received_by_cutoff(self):
        w = World().trip("T0", start=START - timedelta(days=1), day="2026-06-16").trip()
        # yesterday's trace received only tomorrow -> unknown at as-of -> no history
        for i in range(0, 90):
            t = START - timedelta(days=1) + timedelta(seconds=20 * i)
            w.ping("V-1", on_time(20 * i / 60), t, AS_OF + timedelta(days=1))
        w.trace("V-1", START, 30, on_time)
        v, *_ = verdicts(w)
        self.assertEqual(v["T1"]["hist_n"], 0)

    def test_naive_as_of_rejected(self):
        with self.assertRaises(ConfigError):
            parse_aware("2026-06-17 07:45:00")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            from_args(["--as-of"])  # missing value -> argparse exits

    def test_service_date_follows_as_of(self):
        c = from_args(["--as-of", "2026-06-18 07:45:00+05:30"])
        self.assertEqual(str(c.service_date), "2026-06-18")
        c = from_args(["--as-of", "2026-06-17 20:00:00+00:00"])   # 01:30 IST on the 18th
        self.assertEqual(str(c.service_date), "2026-06-18")


class Decisions(unittest.TestCase):
    def test_silence_uses_received_clock(self):
        # device timestamps look fresh but nothing has been RECEIVED for 15 min
        w = World().trip()
        for i in range(46):
            t = START + timedelta(seconds=20 * i)              # until 07:15
            w.ping("V-1", on_time(20 * i / 60), t, t + timedelta(seconds=2))
        v, *_ = verdicts(w)
        self.assertEqual(v["T1"]["reason"], "TELEMETRY_SILENT")
        self.assertAlmostEqual(v["T1"]["silence_min"], 15.0, delta=0.05)   # last receipt 07:15:02

    def test_silence_below_threshold_is_not_silent(self):
        w = World().trip().trace("V-1", START, 22, on_time)     # last ping 07:22, 8 min silence
        v, *_ = verdicts(w)
        self.assertNotEqual(v["T1"]["reason"], "TELEMETRY_SILENT")

    def test_origin_stationary(self):
        w = World().trip().trace("V-1", START, 30, lambda t: 0.0005)   # ~5 m along
        v, *_ = verdicts(w)
        self.assertEqual((v["T1"]["verdict"], v["T1"]["reason"]), ("NO_VERDICT", "ORIGIN_STATIONARY"))

    def test_origin_within_grace_is_not_stationary(self):
        w = World().trip().trace("V-1", START, 6, lambda t: 0.0)
        v, *_ = verdicts(w, as_of=START + timedelta(minutes=6))
        self.assertNotEqual(v["T1"]["reason"], "ORIGIN_STATIONARY")
        self.assertFalse(v["T1"]["verdict"].startswith("PUSH_LATE"))

    def test_movement_threshold_boundary(self):
        # 300 m along = 300/11054 of the route; just past it counts as departed
        frac = 320 / 11054
        w = World().trip().trace("V-1", START, 30, lambda t: frac)
        v, *_ = verdicts(w)
        self.assertNotEqual(v["T1"]["reason"], "ORIGIN_STATIONARY")

    def test_push_late(self):
        w = World().trip().trace("V-1", START, 30, lambda t: max(0.0, (t - 10) / RUNTIME))   # 10 min behind
        v, *_ = verdicts(w)
        self.assertEqual(v["T1"]["verdict"], "PUSH_LATE 10 min")

    def test_push_blocked_by_sparse_telemetry(self):
        w = World().trip()
        late = lambda t: max(0.0, (t - 10) / RUNTIME)
        for m in (0, 5, 12, 21, 25):                            # only 2 fixes received in the last 10 min
            t = START + timedelta(minutes=m)
            w.ping("V-1", late(m), t)
        v, *_ = verdicts(w)
        self.assertEqual((v["T1"]["verdict"], v["T1"]["reason"]), ("NO_VERDICT", "SPARSE_TELEMETRY"))

    def test_hold_on_schedule(self):
        w = World().trip().trace("V-1", START, 30, on_time)
        v, *_ = verdicts(w)
        self.assertEqual((v["T1"]["verdict"], v["T1"]["reason"]), ("HOLD", "ON_SCHEDULE"))
        self.assertAlmostEqual(v["T1"]["late_now_min"], 0.0, delta=0.2)

    def test_just_below_push_threshold_holds(self):
        w = World().trip().trace("V-1", START, 30, lambda t: max(0.0, (t - 4.5) / RUNTIME))
        v, *_ = verdicts(w)
        self.assertEqual(v["T1"]["verdict"], "HOLD")

    def test_not_due(self):
        w = World().trip(start=AS_OF + timedelta(minutes=5))
        v, *_ = verdicts(w)
        self.assertEqual((v["T1"]["verdict"], v["T1"]["reason"]), ("HOLD", "NOT_DUE"))

    def test_arrived_lateness_is_at_arrival_not_after_dwell(self):
        # arrives 2 min late at 07:62 -> 08:02, then sits at the terminus until 08:30
        w = World().trip().trace("V-1", START, 90, lambda t: min(1.0, max(0.0, (t - 2) / RUNTIME)))
        v, *_ = verdicts(w, as_of=START + timedelta(minutes=90))
        self.assertEqual(v["T1"]["reason"], "ARRIVED")
        self.assertAlmostEqual(v["T1"]["arrival_late_min"], 2.0, delta=0.5)

    def test_missing_vehicle_telemetry(self):
        w = World().trip()                                      # trip exists, no pings at all
        v, *_ = verdicts(w)
        self.assertEqual((v["T1"]["verdict"], v["T1"]["reason"]), ("CALL_DRIVER", "NO_TELEMETRY"))

    def test_awaiting_first_ping_within_grace(self):
        w = World().trip()
        v, *_ = verdicts(w, as_of=START + timedelta(minutes=5))
        self.assertEqual(v["T1"]["reason"], "AWAITING_FIRST_PING")

    def test_promised_eta_is_not_lateness(self):
        # on-time bus; riders were promised an absurdly early ETA -> still HOLD, impact counted separately
        w = World().trip().trace("V-1", START, 30, on_time).booking("T1", "S-0103", START + timedelta(minutes=1))
        v, *_ = verdicts(w)
        self.assertEqual(v["T1"]["verdict"], "HOLD")
        self.assertEqual(v["T1"]["riders_past_promised_eta"], 1)


class DataQuality(unittest.TestCase):
    def test_duplicate_fix_rejected_and_reported(self):
        w = World().trip().trace("V-1", START, 30, on_time)
        dup = list(w.pings[10])
        dup[0] = "P-DUP"
        w.pings.append(dup)
        v, _, issues, _ = verdicts(w)
        self.assertIn("DUPLICATE_FIX", codes(issues))
        self.assertEqual(v["T1"]["verdict"], "HOLD")

    def test_duplicate_ping_id_across_vehicles_does_not_merge_rows(self):
        w = World().trip().trip("T2", vehicle="V-2").trace("V-1", START, 30, on_time).trace("V-2", START, 30, on_time)
        w.pings[5][0] = "P-SAME"
        w.pings[-5][0] = "P-SAME"
        v, _, issues, _ = verdicts(w)
        self.assertIn("DUPLICATE_PING_ID", codes(issues))
        self.assertEqual(v["T1"]["n_fixes"], v["T2"]["n_fixes"])   # neither row lost

    def test_invalid_timestamp_rejected_and_reported(self):
        w = World().trip().trace("V-1", START, 30, on_time)
        w.ping("V-1", 0.2, START, rec_raw="2026-06-17 07:60:23+05:30")
        w.ping("V-1", 0.2, START, rec_raw="2026-06-17 07:12:00")        # naive: no offset
        v, _, issues, _ = verdicts(w)
        self.assertEqual(codes(issues).count("UNPARSEABLE_RECORDED_AT"), 2)
        self.assertEqual(v["T1"]["verdict"], "HOLD")

    def test_impossible_jump_rejected(self):
        w = World().trip().trace("V-1", START, 30, on_time)
        w.pings[60][3] = 19.09        # one fix teleports ~7 km ahead within 20 s, inside the service area
        v, _, issues, _ = verdicts(w)
        self.assertIn("POSITION_JUMP", codes(issues))
        self.assertEqual(v["T1"]["verdict"], "HOLD")

    def test_outside_service_area_rejected_but_counts_as_heartbeat(self):
        w = World().trip().trace("V-1", START, 30, on_time)
        for p in w.pings[-40:]:      # last ~13 min: positions garbage, device still talking
            p[3] = 25.0
        v, _, issues, _ = verdicts(w)
        self.assertIn("OUTSIDE_SERVICE_AREA", codes(issues))
        self.assertNotEqual(v["T1"]["reason"], "TELEMETRY_SILENT")      # we did hear from it
        self.assertEqual(v["T1"]["reason"], "SPARSE_TELEMETRY")         # but cannot place it

    def test_device_clock_ahead_rejected(self):
        w = World().trip().trace("V-1", START, 30, on_time)
        w.ping("V-1", 0.9, AS_OF + timedelta(hours=2), recv=AS_OF - timedelta(minutes=1))
        v, _, issues, _ = verdicts(w)
        self.assertIn("DEVICE_CLOCK_AHEAD", codes(issues))
        self.assertEqual(v["T1"]["verdict"], "HOLD")

    def test_multiple_trips_same_operator_same_route(self):
        w = (World().trip("T1", "V-1").trip("T2", "V-2", start=START + timedelta(minutes=12))
             .trace("V-1", START, 30, on_time)
             .trace("V-2", START + timedelta(minutes=12), 18, lambda t: max(0.0, (t - 8) / RUNTIME)))
        v, *_ = verdicts(w)
        self.assertEqual(set(v), {"T1", "T2"})
        self.assertEqual(v["T1"]["verdict"], "HOLD")
        self.assertEqual(v["T2"]["verdict"], "PUSH_LATE 8 min")

    def test_same_vehicle_two_trips_pings_not_double_counted(self):
        w = (World().trip("T1", "V-1", start=START - timedelta(minutes=70))
             .trip("T2", "V-1", start=START + timedelta(minutes=5))
             .trace("V-1", START - timedelta(minutes=70), 60, on_time)
             .trace("V-1", START + timedelta(minutes=5), 25, on_time))
        rows, *_ = run(cfg(w.write(), AS_OF))
        n = sum(r["n_heartbeats"] for r in rows)
        known = [p for p in w.pings if datetime.fromisoformat(p[7]) <= AS_OF]   # the 07:30:00 fix lands 07:30:02
        self.assertEqual(n, len(known))
        self.assertEqual(len(w.pings) - len(known), 1)


class Inputs(unittest.TestCase):
    def test_missing_file(self):
        root = World().trip().write(drop=("stops",))
        with self.assertRaises(InputError):
            load(root)

    def test_missing_column(self):
        root = World().trip().write()
        (root / "routes.csv").write_text("route_id,route_name\n1,x\n", encoding="utf-8")
        with self.assertRaisesRegex(InputError, "missing required columns"):
            load(root)

    def test_empty_trips_fails_loudly(self):
        root = World().write()
        with self.assertRaisesRegex(InputError, "no rows"):
            load(root)

    def test_empty_pings_every_trip_still_reported(self):
        w = World().trip("T1").trip("T2", "V-2")
        v, _, issues, _ = verdicts(w)
        self.assertEqual({r["reason"] for r in v.values()}, {"NO_TELEMETRY"})
        self.assertIn("NO_TELEMETRY", codes(issues))

    def test_no_trips_on_service_date(self):
        w = World().trip(day="2026-06-16", start=START - timedelta(days=1))
        with self.assertRaisesRegex(InputError, "no trips scheduled"):
            run(cfg(w.write(), AS_OF))

    def test_malformed_timetable_fails_loudly(self):
        w = World().trip()
        w.trips[0][4] = "07:00"
        with self.assertRaisesRegex(InputError, "trips.csv row 1"):
            load(w.write())


if __name__ == "__main__":
    unittest.main()

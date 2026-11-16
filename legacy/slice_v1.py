"""Cityflo lateness slice.

Rules (set by Manish, see decisions.jsonl):
  1. Knowledge clock = received_at. Only pings with received_at <= AS_OF are used. recorded_at is
     used only as the time of the fix itself. Late-arriving pings are kept in a separate table.
  2. Lateness = actual progress along the route vs schedule progress (schedule is the yardstick).
     Mon/Tue runtime is a secondary "is this unusual" check. Promised ETA is reported separately as
     rider impact, never as lateness. Distance/speed gives a projected arrival, not lateness.
  3. Rev. C reconciliation rules are rejected: Route 12 is not forced to 0, operator 7 is kept.
  4. >10 min without a valid ping during an active trip -> CALL_DRIVER.
     NO_VERDICT when data is missing / unreliable / contradictory.

Usage: python slice.py [AS_OF]   e.g. python slice.py "2026-06-17 07:45:00+05:30"
"""
import sys
import duckdb

AS_OF = sys.argv[1] if len(sys.argv) > 1 else "2026-06-17 07:45:00+05:30"
SILENCE_MIN = 10        # rule 4
PUSH_MIN = 5            # lateness at/above this -> PUSH_LATE (assumption, not yet confirmed)
MAX_KMPH = 100          # implied speed above this between fixes = position jump, drop the fix
BOX = (18.8, 19.4, 72.7, 73.2)  # Mumbai bounding box

con = duckdb.connect()
con.execute("SET TimeZone='Asia/Kolkata'")
for t in ["trips", "bookings", "routes", "stops"]:
    con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_csv_auto('data/{t}.csv')")
con.execute("CREATE TABLE raw AS SELECT * FROM read_csv('data/gps_pings.csv', all_varchar=true)")
con.execute(f"SET VARIABLE as_of = TIMESTAMPTZ '{AS_OF}'")

# --- route geometry: cumulative distance (m) at each stop, equirectangular projection --------------
con.execute("""
CREATE TABLE stop_geo AS
WITH s AS (SELECT *, lon*111320*cos(radians(19.1)) x, lat*110540 y FROM stops),
seg AS (SELECT *, coalesce(sqrt(power(x-lag(x) OVER w,2)+power(y-lag(y) OVER w,2)),0) seg_m
        FROM s WINDOW w AS (PARTITION BY route_id ORDER BY seq))
SELECT *, sum(seg_m) OVER (PARTITION BY route_id ORDER BY seq) cum_m,
       sum(seg_m) OVER (PARTITION BY route_id) route_m
FROM seg""")

# --- clean pings ------------------------------------------------------------------------------------
con.execute(f"""
CREATE TABLE pings AS
SELECT ping_id, vehicle_id, operator_id::int operator_id, lat::double lat, lon::double lon,
       speed_kmph::double speed_kmph, recorded_at, try_cast(recorded_at AS timestamptz) rec,
       received_at::timestamptz recv
FROM raw""")
con.execute(f"""
CREATE TABLE ping_flags AS
WITH b AS (
  SELECT *,
    CASE WHEN rec IS NULL THEN 'unparseable_recorded_at'
         WHEN lat NOT BETWEEN {BOX[0]} AND {BOX[1]} OR lon NOT BETWEEN {BOX[2]} AND {BOX[3]} THEN 'outside_mumbai'
         WHEN speed_kmph < 0 OR speed_kmph > 150 THEN 'bad_device_speed'
         WHEN row_number() OVER (PARTITION BY vehicle_id, rec, lat, lon ORDER BY recv) > 1 THEN 'duplicate_fix'
    END flag
  FROM pings),
j AS (  -- implied speed from previous unflagged fix on the same vehicle
  SELECT *, 3.6*sqrt(power((lon-lag(lon) OVER w)*111320*cos(radians(19.1)),2)+power((lat-lag(lat) OVER w)*110540,2))
            / nullif(epoch(rec)-epoch(lag(rec) OVER w),0) implied_kmph
  FROM b WHERE flag IS NULL WINDOW w AS (PARTITION BY vehicle_id ORDER BY rec))
SELECT b.*, coalesce(b.flag, CASE WHEN j.implied_kmph > {MAX_KMPH} THEN 'position_jump' END) final_flag,
       b.recv > getvariable('as_of') received_after_asof
FROM b LEFT JOIN j USING (ping_id, vehicle_id, rec)""")

# --- assign pings to trips (vehicle + fix time within the trip buffer) -------------------------------
con.execute("""
CREATE TABLE trip_pings AS
SELECT t.trip_id, t.route_id, t.service_date, t.scheduled_start, t.scheduled_end, r.scheduled_runtime_min,
       p.* FROM ping_flags p
JOIN trips t ON t.vehicle_id = p.vehicle_id
 AND p.rec BETWEEN t.scheduled_start - INTERVAL 20 MINUTE AND t.scheduled_end + INTERVAL 45 MINUTE
JOIN routes r USING (route_id)
WHERE p.final_flag IS NULL""")

# --- project each fix onto the route polyline -> distance along route ------------------------------
con.execute("""
CREATE TABLE progress AS
WITH segs AS (
  SELECT route_id, seq, lag(x) OVER w x1, lag(y) OVER w y1, x x2, y y2, lag(cum_m) OVER w c1, route_m
  FROM stop_geo WINDOW w AS (PARTITION BY route_id ORDER BY seq)),
pp AS (SELECT *, lon*111320*cos(radians(19.1)) px, lat*110540 py FROM trip_pings),
cand AS (
  SELECT pp.*, s.seq, s.route_m,
    greatest(0, least(1, ((px-x1)*(x2-x1)+(py-y1)*(y2-y1)) / nullif(power(x2-x1,2)+power(y2-y1,2),0))) u,
    s.x1, s.y1, s.x2, s.y2, s.c1
  FROM pp JOIN segs s ON s.route_id = pp.route_id AND s.x1 IS NOT NULL),
d AS (
  SELECT *, sqrt(power(px-(x1+u*(x2-x1)),2)+power(py-(y1+u*(y2-y1)),2)) off_m,
         c1 + u*sqrt(power(x2-x1,2)+power(y2-y1,2)) along_m
  FROM cand)
SELECT * EXCLUDE (x1,y1,x2,y2,c1,u,px,py) FROM d
QUALIFY row_number() OVER (PARTITION BY ping_id, trip_id ORDER BY off_m) = 1""")

# schedule-progress lateness at the fix time: when was the schedule due at this distance?
con.execute("""
CREATE TABLE progress_late AS
SELECT *, along_m/route_m frac,
  (epoch(rec) - epoch(scheduled_start + to_seconds(along_m/route_m*scheduled_runtime_min*60)))/60 late_min
FROM progress""")

# --- per-trip as-of state ----------------------------------------------------------------------------
asof_date = AS_OF[:10]
state = con.sql(f"""
WITH known AS (SELECT * FROM progress_late WHERE NOT received_after_asof AND service_date = '{asof_date}'),
last AS (
  SELECT trip_id, max(recv) last_recv, arg_max(rec, recv) last_fix, arg_max(frac, rec) last_frac,
         -- smooth over the last 3 fixes so one GPS wobble can't move the number
         median(late_min) FILTER (WHERE rk <= 3) late_now, count(*) n_known,
         count(*) FILTER (WHERE rec >= getvariable('as_of') - INTERVAL 10 MINUTE) n_recent
  FROM (SELECT *, row_number() OVER (PARTITION BY trip_id ORDER BY rec DESC) rk FROM known) GROUP BY 1),
first AS (SELECT trip_id, min(rec) first_fix FROM known GROUP BY 1),
held AS (SELECT trip_id, count(*) n_held, max(rec) held_last_fix FROM progress_late
         WHERE received_after_asof AND service_date = '{asof_date}' AND rec <= getvariable('as_of') GROUP BY 1),
moved AS (SELECT trip_id, max(along_m) max_along_m FROM known GROUP BY 1),
hist_clean AS (  -- drop history fixes after the bus first reached the destination (dwell inflates lateness)
  SELECT h.* FROM progress_late h
  QUALIFY h.rec <= coalesce(min(CASE WHEN h.frac >= 0.97 THEN h.rec END) OVER (PARTITION BY h.trip_id), h.rec)),
hist AS (  -- Mon/Tue lateness at the same fraction of route as today's last fix
  SELECT t.trip_id, t.route_id, median(h.late_min) hist_late
  FROM last l JOIN trips t USING (trip_id)
  JOIN hist_clean h ON h.route_id = t.route_id AND h.service_date < '{asof_date}'
   AND abs(h.frac - l.last_frac) < 0.05
  GROUP BY 1, 2),
bk AS (SELECT trip_id, count(*) riders FROM bookings GROUP BY 1)
SELECT t.trip_id, t.route_id, r.route_name, t.vehicle_id, t.scheduled_start, t.scheduled_end,
  (SELECT any_value(operator_id) FROM pings p WHERE p.vehicle_id = t.vehicle_id) operator_id,
  bk.riders, f.first_fix, l.last_fix, l.last_recv, l.n_known, round(l.last_frac,2) frac_done,
  round(l.late_now,1) late_now, round(h.hist_late,1) hist_late, hd.n_held, hd.held_last_fix,
  round(m.max_along_m) max_along_m, l.n_recent,
  round((epoch(getvariable('as_of')) - epoch(l.last_fix))/60,1) silent_min,
  round((epoch(f.first_fix) - epoch(t.scheduled_start))/60,1) start_delay_min
FROM trips t JOIN routes r USING (route_id)
LEFT JOIN last l USING (trip_id) LEFT JOIN first f USING (trip_id) LEFT JOIN hist h USING (trip_id)
LEFT JOIN moved m USING (trip_id)
LEFT JOIN held hd USING (trip_id) LEFT JOIN bk USING (trip_id)
WHERE t.service_date = '{asof_date}' ORDER BY t.route_id, t.scheduled_start""").df()


ORIGIN_RADIUS_M = 300   # "no substantial movement" from origin; geometry is not trusted below ~100 m
MIN_RECENT_FIXES = 3    # reliability gate for a push: >= 3 known fixes in the last 10 min


def verdict(r):
    """Returns (verdict, reason_code, action, note). Order of checks matters: a push is the last resort."""
    import pandas as pd
    asof = pd.Timestamp(AS_OF)
    if r.scheduled_start > asof:
        return "HOLD", "NOT_DUE", "none", "not due to depart yet"
    if pd.isna(r.last_fix):
        mins = (asof - r.scheduled_start).total_seconds() / 60
        if mins > SILENCE_MIN:
            return "CALL_DRIVER", "NO_TELEMETRY", "driver", f"due {mins:.0f} min ago, no ping received"
        return "NO_VERDICT", "NO_TELEMETRY_YET", "watch", "due to depart, no ping yet"
    if r.frac_done >= 0.97 and r.silent_min > SILENCE_MIN:
        return "HOLD", "COMPLETED", "none", "trip completed"
    if r.silent_min > SILENCE_MIN:
        if pd.notna(r.n_held) and r.n_held > 0 and r.held_last_fix >= asof - pd.Timedelta(minutes=SILENCE_MIN):
            # device kept fixing; the server just didn't have it by the cut-off
            return ("NO_VERDICT", "TELEMETRY_INGESTION_DELAY", "telemetry/ops team",
                    f"server's last fix {r.last_fix:%H:%M}; device has {int(r.n_held)} fixes up to "
                    f"{r.held_last_fix:%H:%M} that landed after 07:45")
        return ("CALL_DRIVER", "VEHICLE_SILENT", "driver",
                f"no fix for {r.silent_min:.0f} min, last at {r.last_fix:%H:%M} {r.frac_done:.0%} along; "
                f"nothing arrived late either")
    if (asof - r.scheduled_start).total_seconds() / 60 > SILENCE_MIN and r.max_along_m < ORIGIN_RADIUS_M:
        return ("NO_VERDICT", "ORIGIN_STATIONARY", "ops: confirm departure",
                f"device still within {r.max_along_m:.0f} m of origin {(asof - r.scheduled_start).total_seconds()/60:.0f} "
                f"min after departure time; device position != bus position")
    if r.late_now >= PUSH_MIN:
        # push gates: on the way (passed origin check), reliable (recent fixes), known by cut-off (as-of filter)
        if r.n_recent < MIN_RECENT_FIXES:
            return "NO_VERDICT", "SPARSE_TELEMETRY", "watch", f"{int(r.n_recent)} fixes in last 10 min"
        return (f"PUSH_LATE {round(r.late_now)} min", "LATE_VS_SCHEDULE", "rider push",
                f"{r.frac_done:.0%} along, left {r.start_delay_min:+.0f} min; Mon/Tue at this point {r.hist_late:+} min")
    return "HOLD", "ON_SCHEDULE", "none", f"{r.late_now:+} min vs schedule, {r.frac_done:.0%} along"


state[["verdict", "reason", "action", "note"]] = state.apply(lambda r: verdict(r), axis=1, result_type="expand")

# worry-order (set by Manish): not-departed > vehicle silent > ingestion fault > confirmed late > the rest
RANK = {"ORIGIN_STATIONARY": 1, "VEHICLE_SILENT": 2, "NO_TELEMETRY": 2, "TELEMETRY_INGESTION_DELAY": 3,
        "LATE_VS_SCHEDULE": 4}
state["worry_rank"] = state.reason.map(RANK).fillna(9)
state = state.sort_values(["worry_rank", "riders"], ascending=[True, False])

import pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30); pd.set_option("display.max_colwidth", 110)
print(f"As of {AS_OF}\n")
print(state[["trip_id", "route_id", "vehicle_id", "operator_id", "riders", "start_delay_min", "frac_done", "max_along_m",
             "late_now", "hist_late", "silent_min", "n_recent", "n_held"]].to_string(index=False))
print()
print(state[["route_id", "route_name", "trip_id", "verdict", "reason", "action", "note"]].to_string(index=False))
print("\nDropped pings by reason:")
print(con.sql("SELECT final_flag, count(*) n, string_agg(ping_id, ', ') ids FROM ping_flags WHERE final_flag IS NOT NULL GROUP BY 1"))
state.to_csv("verdicts_asof.csv", index=False)

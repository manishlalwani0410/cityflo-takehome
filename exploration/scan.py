"""First-pass data scan: load CSVs into DuckDB and profile them. No lateness logic yet."""
import duckdb

con = duckdb.connect("cityflo.duckdb")
con.execute("SET TimeZone='Asia/Kolkata'")
for t in ["gps_pings", "trips", "bookings", "routes", "stops"]:
    con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM read_csv_auto('data/{t}.csv')")
# recorded_at has one malformed value (P-0001797, '06:60:23'); keep raw text, parse safely
con.execute("""CREATE OR REPLACE TABLE gps_pings AS
  SELECT * REPLACE (try_cast(recorded_at AS timestamptz) AS recorded_at), recorded_at AS recorded_at_raw
  FROM gps_pings""")


def q(title, sql):
    print(f"\n=== {title} ===")
    print(con.sql(sql))


q("row counts", """
select 'gps_pings' t, count(*) n from gps_pings union all select 'trips', count(*) from trips
union all select 'bookings', count(*) from bookings union all select 'routes', count(*) from routes
union all select 'stops', count(*) from stops""")
q("schema gps_pings", "describe gps_pings")
q("routes", "select * from routes order by route_id")
q("trips per route/day", """
select route_id, service_date, count(*) trips, string_agg(vehicle_id||' '||strftime(scheduled_start,'%H:%M'), ', ' order by scheduled_start) detail
from trips group by all order by route_id, service_date""")
q("vehicle -> operator(s)", """
select vehicle_id, string_agg(distinct operator_id::varchar, ',') ops, count(*) pings
from gps_pings group by 1 order by 1""")
q("clock skew received-recorded (sec) by vehicle", """
select vehicle_id, operator_id,
  round(min(epoch(received_at)-epoch(recorded_at))) min_s,
  round(median(epoch(received_at)-epoch(recorded_at))) med_s,
  round(max(epoch(received_at)-epoch(recorded_at))) max_s,
  sum(case when abs(epoch(received_at)-epoch(recorded_at))>120 then 1 else 0 end) n_gt_2min
from gps_pings group by all order by 1""")
q("data quality flags", """
select
  sum(case when lat is null or lon is null then 1 else 0 end) null_pos,
  sum(case when lat=0 or lon=0 then 1 else 0 end) zero_pos,
  sum(case when lat not between 18.8 and 19.4 or lon not between 72.7 and 73.2 then 1 else 0 end) outside_mumbai,
  sum(case when speed_kmph<0 or speed_kmph>100 then 1 else 0 end) bad_speed,
  count(*) - count(distinct ping_id) dup_ping_id,
  sum(case when received_at < recorded_at then 1 else 0 end) received_before_recorded
from gps_pings""")
q("exact duplicate fixes (vehicle,recorded_at,lat,lon)", """
select count(*) from (select vehicle_id, recorded_at, lat, lon, count(*) c from gps_pings group by all having c>1)""")
q("pings on live morning per vehicle, last ping <= 07:45 (by both clocks)", """
select p.vehicle_id, p.operator_id, count(*) n,
  max(case when recorded_at <= timestamptz '2026-06-17 07:45:00+05:30' then recorded_at end) last_rec,
  max(case when received_at <= timestamptz '2026-06-17 07:45:00+05:30' then received_at end) last_recv
from gps_pings p where cast(recorded_at as date)='2026-06-17' or cast(received_at as date)='2026-06-17'
group by all order by 1""")
q("bookings: promised_eta vs schedule sanity", """
select t.route_id, t.service_date, count(*) n,
  sum(case when b.promised_eta < t.scheduled_start or b.promised_eta > t.scheduled_end then 1 else 0 end) eta_outside_trip,
  sum(case when b.booked_at > t.scheduled_start then 1 else 0 end) booked_after_start,
  sum(case when split_part(b.boarding_stop_id,'-',2)[1:length(t.route_id::varchar)] <> t.route_id::varchar then 1 else 0 end) stop_route_mismatch
from bookings b join trips t using(trip_id) group by all order by 1,2""")

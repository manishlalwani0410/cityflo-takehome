"""Audit checks on raw data (one-off; not part of the pipeline)."""
import duckdb
c = duckdb.connect()
c.execute("SET TimeZone='Asia/Kolkata'")
c.execute("CREATE TABLE raw AS SELECT * FROM read_csv('data/gps_pings.csv', all_varchar=true)")
c.execute("""CREATE TABLE p AS SELECT ping_id, vehicle_id, operator_id::int op, lat::double lat, lon::double lon,
 speed_kmph::double spd, try_cast(recorded_at AS timestamptz) rec, received_at::timestamptz recv FROM raw""")
A = "TIMESTAMPTZ '2026-06-17 07:45:00+05:30'"
def q(t, s): print(f"\n=== {t} ==="); print(c.sql(s))

q("A. V-04 and V-05 on 17th: last 6 pings RECEIVED by 07:45 (lag trend before silence)", f"""
select vehicle_id, ping_id, rec, recv, epoch(recv)-epoch(rec) lag_s, spd from (
 select *, row_number() over (partition by vehicle_id order by recv desc) rk from p
 where vehicle_id in ('V-04','V-05') and recv <= {A} and cast(recv as date)='2026-06-17') where rk<=6 order by 1, recv""")
q("B. V-04 first held pings (received after 07:45, recorded before)", f"""
select ping_id, rec, recv, epoch(recv)-epoch(rec) lag_s from p where vehicle_id='V-04' and recv > {A} and rec <= {A}
order by rec limit 4""")
q("C. pings recorded <= 07:45 but received after, by vehicle", f"""
select vehicle_id, count(*) n, min(rec) first_rec, max(rec) last_rec, min(epoch(recv)-epoch(rec)) min_lag
from p where rec <= {A} and recv > {A} group by 1 order by 1""")
q("D. max receive-gap during trips, all days (validates 10-min silence threshold)", """
select vehicle_id, cast(recv as date) d, round(max(gap)/60,1) max_gap_min, round(median(gap)) med_gap_s from (
 select *, epoch(recv)-epoch(lag(recv) over (partition by vehicle_id, cast(recv as date) order by recv)) gap from p)
group by all order by max_gap_min desc limit 8""")
q("E. history days fully received before as-of?", f"select max(recv) from p where cast(rec as date) < '2026-06-17'")
q("F. ingestion health at 07:45 across fleet: vehicles with a ping received in last 2 min", f"""
select count(distinct vehicle_id) from p where recv between {A} - interval 2 minute and {A}""")
q("G. V-04 lag profile on 17th by recv bucket (pre-cutoff only)", f"""
select time_bucket(interval 5 minute, recv) b, count(*) n, max(epoch(recv)-epoch(rec)) max_lag
from p where vehicle_id='V-04' and recv <= {A} and cast(recv as date)='2026-06-17' group by 1 order by 1""")
q("H. Route 21 bookings with promised_eta outside trip window", """
select b.booking_id, b.trip_id, b.boarding_stop_id, b.promised_eta, t.scheduled_start, t.scheduled_end
from read_csv_auto('data/bookings.csv') b join read_csv_auto('data/trips.csv') t using (trip_id)
where b.promised_eta not between t.scheduled_start and t.scheduled_end""")
q("I. stop-level promised_eta vs linear-by-distance schedule (is ETA linear?)", """
select t.route_id, b.boarding_stop_id, min(epoch(b.promised_eta)-epoch(t.scheduled_start))/60 eta_offset_min
from read_csv_auto('data/bookings.csv') b join read_csv_auto('data/trips.csv') t using (trip_id)
where t.route_id in (12, 21) group by all order by 1, 2""")

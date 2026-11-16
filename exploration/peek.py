import duckdb
c = duckdb.connect()
c.execute("SET TimeZone='Asia/Kolkata'")
c.execute("CREATE TABLE raw AS SELECT * FROM read_csv('data/gps_pings.csv', all_varchar=true)")
c.execute("CREATE TABLE p AS SELECT ping_id, vehicle_id, operator_id::int operator_id, lat::double lat, lon::double lon, speed_kmph::double speed_kmph, try_cast(recorded_at AS timestamptz) recorded_at, received_at::timestamptz received_at FROM raw")
c.execute("CREATE TABLE s AS SELECT * FROM read_csv_auto('data/stops.csv')")
def q(t, s): print(f"\n=== {t} ==="); print(c.sql(s))
q("V-06 on 17th, per 5 min: position spread & speed", """
select time_bucket(interval 5 minute, recorded_at) b, count(*) n, round(min(lat),5) lat_min, round(max(lat),5) lat_max,
 round(min(lon),5) lon_min, round(max(lon),5) lon_max, round(avg(speed_kmph),1) spd, count(distinct (lat,lon)) uniq_pos
from p where vehicle_id='V-06' and cast(recorded_at as date)='2026-06-17' group by 1 order by 1""")
q("route 11 stops", "select stop_id, seq, lat, lon from s where route_id=11 order by seq")
q("V-11 on 17th clock skew per 10 min", """
select time_bucket(interval 10 minute, received_at) b, count(*) n, round(min(epoch(received_at)-epoch(recorded_at))) mn,
 round(max(epoch(received_at)-epoch(recorded_at))) mx from p where vehicle_id='V-11' and cast(received_at as date)='2026-06-17' group by 1 order by 1""")
q("V-05 last pings 17th", """select ping_id, lat, lon, speed_kmph, recorded_at, received_at from p where vehicle_id='V-05'
 and cast(recorded_at as date)='2026-06-17' order by recorded_at desc limit 6""")
q("route 17 stops", "select stop_id, seq, lat, lon from s where route_id=17 order by seq")
q("exact duplicate fix", """select * from p where (vehicle_id, recorded_at, lat, lon) in
 (select (vehicle_id, recorded_at, lat, lon) from p group by all having count(*)>1)""")

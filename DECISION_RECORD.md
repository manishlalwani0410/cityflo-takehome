# Decision record — Cityflo 07:45 lateness verdicts

**As-of:** Wednesday 2026-06-17 07:45 IST. **Knowledge clock:** `received_at`.
**Reproduce:** `python -m lateness --as-of "2026-06-17 07:45:00+05:30"` → `outputs/20260617T074500+0530/`.

---

## 1. The 07:45 verdict table (worry-order)

| # | route | trip | verdict | who acts | why |
|---|---|---|---|---|---|
| 1 | 11 Borivali → BKC | TRIP_014 · V-06 · op 5 · 8 riders | `NO_VERDICT` — ORIGIN_STATIONARY | **Ops: confirm departure now** | Device still within 14 m of the origin 59 min after the 06:46 departure, while reporting normally (30 fixes received in the last 10 min). The bus has not left, or the device is not on the bus. A lateness number would be meaningless. |
| 2 | 17 Vashi → Worli | TRIP_017 · V-05 · op 5 · 16 riders | `CALL_DRIVER` — TELEMETRY_SILENT | **Driver** (+ telemetry informed) | Nothing received for 17 min. The last fix (07:27:40) is 71% along, at S-1706, slowing to 4 km/h. |
| 3 | 14 Kandivali → Lower Parel | TRIP_016 · V-04 · op 6 · 12 riders | `CALL_DRIVER` — TELEMETRY_SILENT | **Driver** (+ telemetry informed) | Nothing received for 48 min. The last fix (06:56:54) is 22% along. **At 07:45 this is indistinguishable from Route 17** (see §4). |
| 4 | 12 Mulund → Andheri E | TRIP_015 · V-10 · op 5 · 9 riders | `PUSH_LATE 10 min` | **Rider push** | Left 10 min late (first fix 07:15 vs 07:05) and still +10.0 min at 50% along. Mon/Tue at the same point: +1.0 (37 fixes). Passes every push gate. |
| 5 | 9 Thane → Powai | TRIP_013 · V-11 · op 5 · 12 riders | `HOLD` — ARRIVED | none | Reached the terminus at 07:43, +0.3 min vs schedule. |
| 6 | 21 Ghatkopar → BKC | TRIP_018 · V-03 · op 5 · 6 riders | `HOLD` | none | -0.2 min at 63% along. |
| 7 | 21 Ghatkopar → BKC | TRIP_019 · V-09 · **op 7** · 6 riders | `HOLD` | none | +1.1 min at 30% along. Operator 7 kept (D3). |

**Why this order:**
1. **Route 11 first.** The bus has not left, which a push cannot fix, and every minute of delay compounds.
2. **Route 17 next.** A bus that was definitely moving went quiet, and it has the most riders (16).
3. **Route 14 third.** It is also silent, and for longer. It ranks below Route 17 because its last known position is earlier on the route, so its riders are further from a missed connection, and there are fewer of them (12). *This is a judgment call.* The opposite order is defensible: 48 min of silence is the longer unknown.
4. **Route 12.** Genuinely late, and the push handles it.
5. **Route 9 and Route 21.** Fine.

Rows with the same worry level are sorted by number of riders, then route.

### What changed from the first submission draft

| trip | v1 verdict | final verdict | why |
|---|---|---|---|
| TRIP_016 (Route 14) | `NO_VERDICT` TELEMETRY_INGESTION_DELAY, action telemetry only | `CALL_DRIVER` TELEMETRY_SILENT | **Hindsight bug.** v1 decided "ingestion delay" by counting the 145 V-04 fixes that were received at 08:27–09:14, all after the cut-off. At 07:45 the server had none of them. Its live view of V-04 matched its view of V-05: normal 1–3 s lag until the last ping, then nothing. The delivery finding moved to `retrospective.csv`. |
| TRIP_017 (Route 17) | `CALL_DRIVER` VEHICLE_SILENT, note "nothing arrived late either" | `CALL_DRIVER` TELEMETRY_SILENT | Same verdict. v1's reason and note also relied on hindsight ("nothing arrived late" is only known later), so the reason is now neutral. |
| TRIP_013 (Route 9) | `HOLD` ON_SCHEDULE "+0.3 min, 99% along" | `HOLD` ARRIVED "terminus at 07:43, +0.3 min" | v1 only marked a trip completed if it had *also* gone silent. A bus at 99% is arrived either way. Same action. |
| others | — | unchanged | |

---

## 2. Problem framing

At the 07:45 standup Priya needs one line per route: is it late, by how much, and what do we do. The options are push a lateness notice to riders, hold, or call the driver, and "I don't know, here's why" is a valid line. Her hard constraint: **a confidently wrong number is worse than no number.** So the system is built to suppress or escalate whenever the data cannot support a number, and a push is the *last* thing it reaches, not the first.

The assignment's own warning, that an agent on autopilot produces a clean, plausible, wrong table, came true twice here. The first was the Route 11 "PUSH_LATE 58 min" (§D5). The second was the v1 Route 14 verdict, which used information the server did not have at 07:45 (§D4).

## 3. Data assumptions (each checked against the rows, not the notes)

| assumption | evidence | if wrong |
|---|---|---|
| `received_at` is a trustworthy server clock | For all 7 vehicles on all days, the median lag is 2–3 s and the maximum is 4 s, except V-04 after 06:57 on the 17th (5,400 s exactly). Never `received < recorded`. | The whole as-of construction would need a different clock (for example ingest batch ids). |
| Normal ping interval ≈ 20 s | The median receive gap is 20 s for every vehicle and day. The maximum gap is 0.4 min, except V-04 on the 17th (90.3 min). | The 10-min silence threshold would need re-deriving. It currently sits about 25× above the worst normal gap. |
| Mon/Tue were fully received before 07:45 Wed | Last history `received_at` is 2026-06-16 08:26:58. History is still filtered by `received_at <= as_of`, so this holds by construction. | — |
| One operator per vehicle | True in the data. The operator is taken from the first received ping. | If a vehicle changed operator, the output would show the first operator seen. |
| Stops polyline ≈ route | Off-route distance is checked per fix: anything over 1 km is rejected (none on the real data). | The polyline only goes through the stops, so positions are good to about 100 m. That's why the stationary threshold is 300 m, not 50 m. |
| `promised_eta` is a usable rider-impact signal, not a yardstick | TRIP_018 promises S-2102 at 07:09, *before* the 07:20 departure, and promises S-2104 (+22.8 min) later than S-2105 (+12.2 min). | Reinforces D2: never use it as the lateness definition. |

## 4. Knowledge clock and live vs retrospective

**Definition.** A fact is known at T only if the server received it by T: `received_at <= as_of`. `recorded_at` says *when the bus was there*. It is used for the lateness arithmetic and to order fixes along a trace, never to decide what we knew.

**Enforced where:** `lateness/clean.py:clean()` filters on `recv <= as_of` *before* any other processing. Deduplication and the jump filter therefore cannot be influenced by future rows: a jump filter that sees a future fix as "the previous fix" would leak. Every feature downstream works only on that filtered set:
- **silence:** `as_of − last received_at`;
- **recent-fix count:** fixes with `received_at` in the last 10 min;
- **history baseline:** earlier dates, received by the cut-off.

**Retrospective is a separate output.** `lateness/retro.py` re-runs the same rules in a hindsight world where every fix recorded by the cut-off is treated as delivered instantly. It reports `hindsight_verdict`, `held_pings` and a `finding`. Nothing in it reaches `decide()`. Three regression tests make this concrete for Route 14:
1. Deleting the 145 held pings leaves every live field byte-identical.
2. Moving them to the destination leaves every live field byte-identical.
3. Control: delivering them on time *would* flip Route 14 to `HOLD`. This proves the first two tests are able to detect a change.

**What hindsight says about 07:45** (`retrospective.csv`):
- **Route 14:** the silence was a **delivery fault**. The device kept recording until 07:44:46, and those fixes arrived exactly 90 min late. In hindsight the bus was on time (+0.6 min), so the 07:45 driver call was unnecessary.
- **Route 17:** the silence was **real**. No fix recorded during the gap ever arrived, and the device never reported again in the extract. The call was right.

That asymmetry is only visible afterwards. At 07:45 both look identical, and the live rule has to treat them identically.

---

## 5. Decision rules

`lateness/decide.py:decide()` applies the checks below in order, and the first that fires wins. Defaults are in `lateness/config.py:Thresholds`, and each one can be overridden from the command line.

| order | condition | verdict / reason | action |
|---|---|---|---|
| 1 | as_of < scheduled_start | `HOLD` NOT_DUE | none |
| 2 | nothing received for the trip, and more than 10 min past departure | `CALL_DRIVER` NO_TELEMETRY | driver + telemetry |
| 2b | the same, but 10 min or less past departure | `NO_VERDICT` AWAITING_FIRST_PING | watch |
| 3 | some accepted fix has reached 97% of the route | `HOLD` ARRIVED (lateness at the arrival fix) | none |
| 4 | more than 10 min since the last ping **received** | `CALL_DRIVER` TELEMETRY_SILENT | driver + telemetry |
| 5 | pings received, but none usable for position | `NO_VERDICT` SPARSE_TELEMETRY | telemetry |
| 6 | furthest progress under 300 m, and more than 10 min past departure | `NO_VERDICT` ORIGIN_STATIONARY | ops: confirm departure |
| 6b | the same within 10 min, and lateness at least 5 min | `NO_VERDICT` AT_ORIGIN_WITHIN_GRACE | watch |
| 7 | fewer than 3 usable fixes received in the last 10 min | `NO_VERDICT` SPARSE_TELEMETRY | watch |
| 8 | lateness at least 5 min | `PUSH_LATE n` LATE_VS_SCHEDULE | rider push |
| 9 | otherwise | `HOLD` ON_SCHEDULE (flags RUNNING EARLY at -5 min or below) | none |

### D1. Knowledge clock = `received_at`, cut-off inclusive
- **Rule:** only rows with `received_at <= as_of` exist for the live decision. Rows recorded before but received after are counted as *held* and reported in the retrospective file only.
- **Rationale:** the 07:45 wall can only show what the server had at 07:45. Anything else is a report the system could never have produced live.
- **Breaks on:** TRIP_016. A complete, on-time trace existed on the device, and the rule blanks it and triggers a needless driver call.
- **Cost / who pays:** one unnecessary call to the depot, and 12 Route 14 riders with no ETA information. The alternative, using data that hadn't arrived, isn't a rule a live system can run at all.
- **Flips if:** the ingest system exposes a per-device backlog or heartbeat signal *at* the cut-off (see D4).

### D2. "Late" = progress along the route vs schedule progress
- **Rule:** snap each fix to the stop polyline to get the distance travelled, `along_m`. Due time = `scheduled_start + along_m / route_length × scheduled_runtime`. Lateness = `recorded_at − due`, taken as the median of the 3 most recent accepted fixes.
  - Mon/Tue lateness at the same progress (±5%, history only up to arrival) is *supporting context* in the note. It never changes a verdict.
  - `promised_eta` is reported as `riders_past_promised_eta` (rider impact). It is never used as lateness.
  - Distance remaining ÷ speed is not computed. It would be a projected ETA, not lateness, and the speed field is unreliable (P-0003995 is negative).
- **Rationale:** this is a progress-based measure grounded in the published timetable, as Priya's ops team already understands it. A rider-promised ETA can be wrong itself (TRIP_018 above), and distance at current speed falls apart in stop-and-go traffic.
- **Breaks on:** TRIP_017 shows -8.5 min ("early") at 71% along. The schedule assumes even pace across the route, but traffic is not even; congestion in the middle of a route makes mid-trip readings off by a few minutes in either direction.
- **Cost:** a borderline route (around 4–6 min) could be pushed or held wrongly. The push gates (D5) and the 5-min threshold limit it.
- **Flips if:** Mon/Tue show a consistent offset of more than 3 min at the same point on a route. Then that route needs a historical progress curve instead of the even-pace assumption.

### D3. Both rev. C "reconciliation rules" rejected
- **Rule:**
  - Route 12 is computed like every other route.
  - Operator 7 vehicles stay in the table. If their data isn't trustworthy they get `NO_VERDICT`, but they are never dropped.
- **Rationale:** forcing Route 12 to 0 overwrites a measured +10 min to fit a contract. That is the "confidently wrong number" Priya says cost them before. Dropping operator 7 hides TRIP_019's 6 riders from the wall.
- **Breaks on:** Route 12 is precisely where the rule changes the answer: 9 riders would get no warning of a 10-min delay.
- **Cost:** our figures won't match the ops dashboard, and Route 12 shows a breach of its contractual on-time commitment. That conversation belongs to whoever owns the contract. Changing the data isn't the way to have it.
- **Flips if:** nothing in the data. For operator 7: V-09's telemetry is clean today (1–4 s lag). If it weren't, it would get `NO_VERDICT`, not removal.

### D4. Silence → CALL_DRIVER, treated identically whatever the cause
- **Rule:** an active trip with nothing received for more than 10 min → `CALL_DRIVER TELEMETRY_SILENT`, and the telemetry team is informed in the same action. The note says explicitly that at the cut-off we cannot tell a stopped vehicle from a delivery fault.
- **Rationale:** with only data received by 07:45, V-04 and V-05 look the same: normal lag right up to the last ping, then nothing. The fleet-wide ingester was healthy at 07:45 (5 vehicles had a ping in the last 2 min), which rules out a global outage but not a per-device one. One call resolves both possibilities: "where are you?" confirms whether the bus is moving and therefore whether the problem is the device. This applies the 10-minute rule you set in D-ledger entry 4.
- **Breaks on:** TRIP_016. In hindsight it was a delivery fault and the bus was on time, so the call was unnecessary. Your D-ledger entry 6 said the driver shouldn't be called for an ingestion delay. That instruction holds when the delay is *known*, and at 07:45 it was not.
- **Cost:** one unnecessary depot call for Route 14 on this morning. The other way, calling no one, would leave Route 17 (a real silence, 16 riders) uncalled, because live the two cases look the same.
- **Flips if:**
  - the ingester could report per-device backlog (for example "device V-04 has unacknowledged packets" or a modem heartbeat), so delivery faults are detectable at the cut-off;
  - or V-04 has a track record of delivery stalls. The extract has none: V-04 was clean on Mon/Tue.
  - **Business assumption to confirm with ops:** whether an unexplained silence should go to CALL_DRIVER, as here, or to NO_VERDICT plus telemetry first. I applied your stated 10-minute rule.

### D5. A push needs more than crossing the threshold
- **Rule:** `PUSH_LATE n` requires all of the following, and fails safe to `NO_VERDICT` or an escalation otherwise:
  - lateness of at least 5 min;
  - not silent (D4);
  - departed, meaning progress beyond 300 m (D6);
  - at least 3 usable fixes received in the last 10 min;
  - every input received by the cut-off (D1).
  The reliability gate applies to `HOLD` as well: a confident "on time" needs data too.
- **Breaks on:** TRIP_014. Before this gate, the rules produced `PUSH_LATE 58 min` for a device sitting 14 m from the origin.
- **Cost:** a gated route that is really late gets no push until the data recovers. Route 11's 8 riders hear nothing until ops confirm.
- **Flips if:** the push audience could be told "delay of unknown length". That is a product change, not a data rule.

### D6. ORIGIN_STATIONARY (300 m)
- **Rule:** more than 10 min after scheduled departure, with the *furthest* progress along the route still under 300 m → `NO_VERDICT ORIGIN_STATIONARY`, and ops confirms departure.
- **Why 300 m:** the polyline only goes through stops, so it is only accurate to about 100 m. 300 m is three times that, while still well inside the first stop-to-stop segment on every route (roughly 1.4–2.5 km). V-06 reached at most 14 m.
- **Why "furthest", not "latest":** a bus that left and came back to the depot is a different problem from one that never left. It would be caught by its lateness and the other checks, not misread as stationary.
- **Breaks on:** if the device was left at the depot while V-06 is actually on the road, the riders at later stops get no warning about a bus that may be on time.
- **Cost:** 8 riders without information until ops call back. A push of "58 min" would be worse, because they would plan around it.
- **Flips if:** a phone call, a second device, or a rider check-in places V-06 on the road.

### D7. Data cleaning, recorded and never silent
Every rejected or flagged row appears in `data_quality.csv` with its file row and reason.

| row | code | handling | effect on verdicts |
|---|---|---|---|
| P-0001797 | `06:60:23` timestamp → UNPARSEABLE_RECORDED_AT | rejected | none (a Tuesday ping) |
| P-0003148–50 | OUTSIDE_SERVICE_AREA (6,500–7,800 km/h implied) | rejected for position, but still counted as proof the device was transmitting | Route 9 position not distorted |
| P-0003995 | IMPLAUSIBLE_SPEED_FIELD (-3.8 km/h) | **kept**: the position is valid and the speed field isn't used | none. v1 dropped it, which was wrong in principle and made no difference in practice |
| P-0002821 (V-11) | DUPLICATE_FIX of P-0003248; also DUPLICATE_PING_ID (V-05 uses the same id) | duplicate rejected; rows are keyed on file position, not `ping_id`, so the V-05 row is untouched | none |

The pipeline also checks:
- device clock more than 60 s *ahead* of the server clock (rejected);
- conflicting fixes with the same device timestamp;
- fixes more than 1 km off the route.

A jump filter rejects any fix implying more than 120 km/h from the last accepted fix. After 5 consecutive rejections it re-anchors, so one bad anchor cannot blank a whole trip. None of these fired on the real data apart from the rows above, and all are covered by tests.

---

## 6. Trade-offs and rejected alternatives

| alternative | rejected because |
|---|---|
| Use `recorded_at` as the clock | It produces the v1 Route 14 bug: it reports positions the server didn't have yet. |
| Promised ETA as the lateness yardstick | The ETAs are internally inconsistent (TRIP_018), and lateness should be measured against the operational timetable. |
| Mon/Tue median runtime as the yardstick | Two days of history is not a baseline. It's kept as context only. |
| `NO_VERDICT` for every silence | It would leave Route 17's real silence (16 riders) uncalled. |
| Infer an "ingestion delay" from the held pings live | It's hindsight. That was the v1 bug. |
| Interpolate a lateness for silent buses | That's a confident number with no data behind it. |
| A historical progress curve per route | It's the right next step but needs more than 2 days of data. Listed under §8. |
| DuckDB / pandas | Not needed at this scale. The final pipeline is standard-library Python only, which makes it easy to run anywhere and easy to test. |

## 7. Known limitations
1. **Route 14 versus Route 17 is a genuine blind spot at the cut-off.** This morning it costs one unnecessary call. Fixing it needs a signal from the ingester, not a cleverer rule.
2. **Even-pace schedule assumption** (D2): mid-route readings carry a few minutes of error.
3. **Thresholds are initial policy** (10 min, 5 min, 300 m, 3 fixes). They were checked against this extract, not tuned on many weeks of data. The 10-min silence threshold has a wide margin: the worst normal gap is 0.4 min.
4. **Two history days** make a thin baseline. `hist_n` is shown so readers can judge it.
5. **Pings are assigned to trips by vehicle and scheduled window.** Overlapping buffers pick the nearest window. That is tested but never occurs in this data.
6. **Rider impact is a count only:** `riders_past_promised_eta` counts bookings whose promised time has passed at a stop the bus hasn't reached. Per-stop push timing is not built.
7. **Not traced:** the 4 TRIP_018 bookings whose promised ETA falls outside the trip's schedule window, and the out-of-order S-2104/S-2105 promises.

## 8. Production improvements
- An ingest-health signal per device (backlog, last modem heartbeat) so delivery faults are distinguishable live. That would resolve limitation 1.
- A per-route, per-time-of-day progress curve learned from weeks of data, replacing the even-pace schedule assumption.
- Running every few minutes rather than once a day, with verdict-change tracking. A route changing from `HOLD` to `PUSH_LATE` and back should be damped so riders aren't sent contradictory messages.
- Thresholds stored as versioned config, with each output recording which thresholds produced it. `run_meta.json` already records the thresholds and the SHA-256 of each input file.
- Back-testing against recorded rider complaints and actual arrivals, to calibrate the 5-min push threshold.
- Alerting when the data-quality issue counts jump.

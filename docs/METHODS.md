# PigeonOps methods

This file lists every formula the backend uses and every assumption behind it. All inputs are synthetic or estimated (see [DATA.md](../DATA.md)). Nothing here is measured.

The code lives in `backend/outcomes.py` (trips and action outcomes), `backend/assignment.py` (truck assignment), `backend/decision_engine.py` (ranking and value of inspection) and `backend/indicators.py` (plan indicators). Settings live in `config/port_scenario.json` under `decision_model`.

## Worked example

In the `disruption` variant, truck SYN-TRK-07 carries SYN-SHP-1042 on the `primary` route. Its appointment window is 10:15 to 10:45 and its planned ETA is 10:03.

| Action | Road | Arrival | Miles left | Driver min | Late min | Added cost $ | CO2 kg | Missed window | Path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold-course | open | 10:03 | 8.13 | 24.6 | 0 | 0 | 13.0 | 0 | primary |
| hold-course | blocked | 11:03 | 26.99 | 84.5 | 47.9 | 59.46 | 43.2 | 1 | primary to obstruction, 10 min turnaround, back to start, detour |
| reroute | either | 10:29 | 19.11 | 50.7 | 14.0 | 29.46 | 30.6 | 0 | primary back to start, detour |
| swap-truck | open | 10:39 | 11.37 | 47.4 | 23.8 | 38.03 | 23.4 | 0 | SYN-TRK-12 drives to the load, 15 min handover, primary |
| swap-truck | blocked | 11:39 | 30.23 | 107.3 | 83.7 | 97.49 | 53.5 | 1 | as above, then obstruction, turnaround, back to start, detour |

For `swap-truck` the arrival, miles and driver minutes are SYN-SHP-1042's own. Lateness, cost and CO2 also include the change to SYN-SHP-1048 (section 4). Every action reports both shipments:

| Action | Road | SYN-SHP-1042 truck, arrival, slack | SYN-SHP-1048 truck, arrival, slack |
| --- | --- | --- | --- |
| hold-course | open | SYN-TRK-07, 10:03, +12 min | SYN-TRK-12, 10:42, +18 min |
| hold-course | blocked | SYN-TRK-07, 11:03, −47.9 min | SYN-TRK-12, 10:42, +18 min |
| reroute | either | SYN-TRK-07, 10:29, −14 min | SYN-TRK-12, 10:42, +18 min |
| swap-truck | open | SYN-TRK-12, 10:39, −23.8 min | SYN-TRK-07, 10:57, +3 min |
| swap-truck | blocked | SYN-TRK-12, 11:39, −83.7 min | SYN-TRK-07, 10:57, +3 min |

Slack is appointment window start minus arrival; negative means late.

Regenerate these numbers with `analyze(load_scenario(variant="disruption"))`.

## 1. Where the truck and the obstruction are

Each route has an OSRM road geometry (a GeoJSON LineString), a `distance_m` and a `duration_s`.

The truck's latitude and longitude are projected onto the nearest point of its route's geometry. The result is a share of the route, from 0 (start) to 1 (end). The same is done for the obstruction (the `incident` location) on the blocked route.

```text
share = length of geometry before the nearest point / total geometry length
```

In the example the truck is at share 0.212 of `primary` and the obstruction is at share 0.594.

The projection uses a flat (equirectangular) approximation of the Earth. Over a 30 km route the error is far below the precision of the synthetic positions.

## 2. Distance and time of a trip

A trip is a list of pieces of routes. For each piece between shares `a` and `b`:

```text
piece meters  = |b - a| × route distance_m
piece minutes = |b - a| × route duration_s / 60
```

This assumes constant speed along each route. OSRM reports one duration for the whole route, so we cannot do better without segment speeds.

The trip an action produces depends on its `plan.route` and the road state:

1. **Planned trip.** The truck stays on its current route and drives from its share to the end.
2. **Switch route** (for example `reroute`). The truck drives back along the current route to its start, then drives the whole new route. The new route must start where the current route starts; the backend raises an error otherwise.
3. **Slowdown** (heavy traffic, `blockage.effect: "slowdown"`). If the trip uses the affected route and the truck has not yet passed the queue, it drives to the queue, waits `blockage.slowdown_min` crawling through it, and continues on the same route. It drives no extra miles.
4. **Blocked road** (every other incident). If the trip uses the blocked route (`blockage.route`) and the truck has not yet passed the obstruction, it drives to the obstruction, waits `blockage.turnaround_min`, drives back to the route start, then drives the whole `blockage.fallback_route`.

```text
driver minutes = driving minutes + waiting minutes
miles          = meters / 1609.344
```

## 3. Action outcomes

Each action gets one outcome per road state (`open` and `blocked`).

```text
arrival             = planned ETA + (driver minutes of this trip − driver minutes of the planned trip)
lateness_min        = max(0, arrival − appointment window start)
constraint_breaches = 1 if arrival > appointment window end, else 0
emissions_kg        = miles × co2_kg_per_mile
trip cost           = miles × cost_per_mile + driver minutes × driver_hourly_rate / 60
incremental_cost    = trip cost − planned trip cost
```

The shipment's ETA anchors the clock. It is a synthetic input that already includes things the route model does not see, such as gate time. Route data only supplies the change from that plan.

Each computed outcome has `basis: "estimated"` and a `detail` block with the affected shipment's arrival, miles, driver minutes, trip cost and path, the added miles of the whole assignment pool, and `shipments`, one row per pool shipment (section 4).

## 4. Truck assignment

`decision_model.assignment_pool` lists the shipments and trucks the dispatcher may reassign. In every variant it is SYN-SHP-1042 and SYN-SHP-1048 with their trucks, SYN-TRK-07 and SYN-TRK-12. The pool must contain the affected shipment and each pool shipment's truck. A pool truck may not carry any shipment outside the pool, because the model does not sequence several loads on one truck.

The backend lists every assignment of pool trucks to pool shipments with `itertools.permutations(trucks, number of shipments)`. Two trucks over two shipments give two assignments: the plan and the swap. With a spare third truck there would be 3 × 2 = 6. The backend refuses pools with more than 5,040 assignments.

### Arrival of one shipment

Each shipment has a planned truck and a planned ETA. If its planned truck keeps it, the trip is the one in section 2:

```text
arrival = planned ETA + (driver minutes of this trip − driver minutes of the planned trip)
```

If another truck takes it, that truck has to fetch the load from the planned truck. Each pool truck has a synthetic `available_at` time.

```text
fetch miles      = straight-line distance from the new truck to the planned truck × fetch_circuity
fetch minutes    = fetch miles / fetch_speed_mph × 60
handover starts  = latest of (new truck available_at + fetch minutes, planned truck available_at, load ready time)
haul             = the planned truck's remaining trip on the chosen route, with the blockage rules of section 2
planned start    = latest of (planned truck available_at, load ready time)
arrival          = planned ETA + (new truck available_at − planned start)
                   + fetch minutes + wait for handover + handover_min + haul minutes − planned trip minutes
```

The new truck's trip is the fetch, any wait for the handover, the handover and the haul. Its miles, driver minutes, cost and CO2 follow section 2. Lateness and missed window use the shipment's own appointment.

Worked example: SYN-TRK-12 is free at 09:25, 13 minutes after SYN-TRK-07. It is 3.2 road miles from SYN-TRK-07 (2.5 straight-line miles × 1.3), which takes 7.7 minutes at 25 mph. With the 15-minute handover, SYN-SHP-1042 arrives 13 + 7.7 + 15 = 35.7 minutes after its 10:03 ETA, at 10:39. The load is still before the obstruction, so a blocked road costs the swap the same hour it costs holding course.

### Score of an assignment

Every action is scored on the same pool, so actions stay comparable:

```text
lateness_min        = affected shipment's lateness + Σ other pool shipments (lateness − planned lateness)
constraint_breaches = same pattern
emissions_kg        = same pattern
incremental_cost    = Σ pool shipments (trip cost − planned trip cost)
```

For `hold-course` and `reroute` the other shipments run on plan, so these equal the single-shipment numbers of section 3. Loss and expected loss are those of section 5, with the same weights. The $85 per late minute therefore applies to SYN-SHP-1048 too.

`swap-truck` is the action whose `plan.trucks` gives the swapped assignment. The planned assignment matches `hold-course`. The analysis response returns every assignment ranked by expected loss, under `assignments.before` (prior) and `assignments.after` (posterior, after an observation).

## 5. Ranking and value of inspection

```text
loss = lateness_min × lateness_cost_per_minute
     + incremental_cost
     + emissions_kg × emissions_cost_per_kg
     + constraint_breaches × hard_constraint_penalty

expected loss = P(blocked) × blocked loss + (1 − P(blocked)) × open loss
```

Actions are ranked by expected loss, lowest first.

An inspection returns "blocked" or "clear" and is right with probability `drone.accuracy`. Bayes' rule gives the updated blockage probability:

```text
P(blocked | says blocked) = accuracy × p / (accuracy × p + (1 − accuracy) × (1 − p))
P(blocked | says clear)   = (1 − accuracy) × p / ((1 − accuracy) × p + accuracy × (1 − p))
```

An "inconclusive" result leaves the probability unchanged. The headline inspection value assumes reviewed footage always gives a definite answer (P(inconclusive) = 0), so it is an upper bound when clips can be inconclusive; only the `cv_only` comparison models inconclusive results.

In the demo the observation is the dispatcher's verdict on a drone clip. The CV pipeline preselects a suggestion from vehicle motion on the marked road (`cv/README.md`), and the dispatcher confirms or overrides it. `drone.accuracy` describes that reviewed verdict, not the CV suggestion alone; `drone.cv_only` is the measured table for still-image CV alone, used only for the comparison line.

### Inspection delay

An inspection takes `drone.launch_and_review_minutes` (6 minutes). It starts at the scenario time, `generated_at` (09:12), and ends at 09:18. No pool truck acts on the decision before then.

```text
hold = max(0, scenario time + launch_and_review_minutes − latest of (truck available_at, scenario time))
```

A held truck waits where it is. The wait is added to the front of its trip, so it moves the arrival, the slack, the lateness and the missed-window check of sections 3 and 4, and it is paid driver time. SYN-TRK-07 is free at 09:12, so it waits 6 minutes. SYN-TRK-12 is not free until 09:25, so it does not wait.

Each action and each assignment therefore has two sets of outcomes: `outcomes` (act now) and `outcomes_after_inspection` (act after the wait). After the wait in the worked example:

| Action | Road | SYN-SHP-1042 arrival, slack | SYN-SHP-1048 arrival, slack | Late min | Added cost $ |
| --- | --- | --- | --- | --- | --- |
| hold-course | open | 10:09, +6 min | 10:42, +18 min | 0 | 3.50 |
| hold-course | blocked | 11:09, −53.9 min | 10:42, +18 min | 53.9 | 62.96 |
| reroute | either | 10:35, −20 min | 10:42, +18 min | 20.0 | 32.96 |
| swap-truck | open | 10:39, −23.8 min | 10:58, +2.2 min | 23.8 | 38.47 |
| swap-truck | blocked | 11:39, −83.7 min | 10:58, +2.2 min | 83.7 | 97.94 |

On a clear road the 6-minute wait uses half of SYN-SHP-1042's 12-minute slack and causes no lateness. A wait longer than 12 minutes would make it late even on a clear road. The swap barely changes, because SYN-TRK-12 could not start before 09:25 anyway.

```text
P(says blocked)          = accuracy × p + (1 − accuracy) × (1 − p)
loss with inspection     = P(says blocked) × best delayed loss after "blocked" + P(says clear) × best delayed loss after "clear"
gross value              = best expected loss now − loss with inspection
net value                = gross value − drone.direct_cost
recommend inspection     = net value > 0
```

"Delayed loss" uses `outcomes_after_inspection`. The ranking shown after an observation (`after`, `assignments.after`) uses them too. The earlier flat `delay_cost` of $75 is gone, so the wait is counted once. Gross value can now be negative: evidence that cannot change the action still costs the wait.

The sensitivity curve repeats the net value at 21 evenly spaced priors from 0 to 1.

### Results per variant

| Variant | Prior P(blocked) | Best action now | Best loss now $ | Loss with inspection $ | Net value $ | Inspect? |
| --- | --- | --- | --- | --- | --- | --- |
| normal | 0.02 | hold-course | 120.72 | 131.90 | −101.18 | no |
| disruption | 0.40 | reroute | 1227.11 | 1020.91 | +116.20 | yes |
| disruption_b | 0.80 | reroute | 1227.11 | 1740.61 | −603.50 | no |
| congestion | 0.50 | hold-course | 12.80 | 15.13 | −92.33 | no |
| flood | 0.60 | reroute | 1227.11 | 1911.78 | −774.67 | no |

Net value is best loss now minus loss with inspection minus the $90 direct cost.

In `disruption` an inspection can change the decision. A "clear" result drops the blockage probability to 7%, and keeping the current route becomes best. Inspecting is still worth it, by $116.20 instead of the $279.30 the flat delay cost gave. The inspection pays off once its evidence is at least 86.4% accurate (`evoi.break_even_accuracy`); the assumed accuracy is 90%.

In `disruption_b` the reports are corroborated, so the prior is 80%. Even after a "clear" result the probability stays at 31%, and rerouting is still best. The inspection cannot change the decision. It only makes the reroute start 6 minutes later: 6 more late minutes at $85 plus $3.50 of paid waiting, $513.50, plus the $90 direct cost.

In `congestion` the queue is outside Pier 400's gate on one outbound access road, near the start of the primary route. The released truck is upstream of it. Its synthetic 09:40 planned arrival gives 35 minutes of slack; the assumed severe queue adds 30 minutes, giving a 10:10 arrival. The alternate road avoids the queue but adds miles; a swap keeps the affected load on the queued route and disrupts the other delivery. Continuing wins before inspection and after either finding. Traffic-check accuracy is assumed to be 95% with a 4-minute review, but the extra information does not improve the choice. This is not a gate-processing delay.

In `flood` the hazard is farther north on the connector approach. The synthetic prior of impassability is 60%. With assumed aerial-review accuracy of 65% and an 8-minute wait, the posterior is 45% after a favorable finding or 74% after an unfavorable finding. The detour wins in both cases; inspection adds an estimated $774.67 in loss, including its $90 direct cost. In the modeled impassable case, the truck loses an assumed 15 minutes withdrawing before returning to the route start and detouring. Footage cannot establish safe water depth or pavement condition. These are planning comparisons, not permission to enter water.

`evoi.response_by_observation` exposes the engine's estimated probability, posterior and best delayed response for each possible finding. The UI uses these results to explain whether checking can change the action, rather than inferring it from the incident title.

In `normal`, waiting costs only $3.50 of driver time because SYN-SHP-1042 has 21 minutes of slack, but there is almost nothing to learn.

## 6. Plan indicators

Indicators describe the whole plan under the top-ranked option before any inspection. `p` is the prior blockage probability. Pool shipments (section 4) are re-simulated one by one. Every other shipment with an assigned truck is assumed to run on plan.

| Indicator | Formula |
| --- | --- |
| `shipments_at_risk` | Assigned shipments outside the pool with negative `slack_min`, plus each pool shipment with P(arrives after window start) ≥ `at_risk_probability` |
| `appointments_feasible` | Assigned shipments, minus those outside the pool with negative slack, minus each pool shipment with P(misses window end) ≥ `at_risk_probability`. `of` is the total number of shipments, including unassigned ones |
| `expected_delay_min` | p × blocked lateness + (1 − p) × open lateness of the top option |
| `added_miles` | p × blocked added miles + (1 − p) × open added miles of the top option, against the planned trip |
| `co2_kg` | Full route miles of every assigned shipment × `co2_kg_per_mile`, plus the top option's expected emissions (which include the change to other pool shipments) minus the planned trip's emissions |
| `community_exposure` | Distinct schools within 300 m of any route the plan drives: every assigned shipment's route, with the affected shipment on the top option's route. Schools are OpenStreetMap `amenity=school` points, saved by `scripts/build_community_exposure.py` in `config/community_exposure.json` |

P(...) here is the sum of the probabilities of the road states in which the condition holds.

Every indicator is `basis: "estimated"`. Each ranked response also carries `schools_within_300m`, the same count for the one route it sends the affected load on (0 on the CA-47 route, 6 on the Anaheim St detour). It counts the route the response chooses; a truck forced onto the detour by a closure is not counted.

Each shipment counts as one full trip on its route. Return trips and empty repositioning are not counted, so `co2_kg` understates fleet emissions.

## 7. Assumptions

| Setting | Value | Basis | Notes |
| --- | --- | --- | --- |
| `rates.cost_per_mile` | $1.30 | assumed | Non-driver operating cost (fuel, maintenance, tires, truck payments, insurance). Round value of the size reported in ATRI's annual *Operational Costs of Trucking*; not checked against the latest edition. |
| `rates.driver_hourly_rate` | $35 | assumed | Loaded driver cost per hour, charged for driving and waiting. Round value; not sourced. |
| `rates.co2_kg_per_mile` | 1.6 kg | estimated | About 10.2 kg CO2 per gallon of diesel (EPA emission factor) divided by about 6.4 miles per gallon for a heavy truck. Tailpipe only. Battery-electric trucks are treated the same; this overstates their emissions. |
| `blockage.turnaround_min` | 10 min, 15 min in `flood` | assumed | Time lost reaching the closure and turning around; longer when the truck has to back out of standing water. |
| `blockage.slowdown_min` | 30 min in `congestion` | assumed | Time lost in a queue on the outbound access road outside Pier 400's gate. |
| `blockage.route` / `fallback_route` | primary / detour | assumed | The obstruction sits on `primary`; drivers fall back to the Anaheim St detour. |
| `indicator_rules.at_risk_probability` | 0.5 | assumed | Probability threshold for "at risk" and "infeasible". |
| Truck `available_at` | SYN-TRK-07 09:12, SYN-TRK-12 09:25 | synthetic | SYN-TRK-07 is free now; 09:25 comes from the scenario's "is free for a handover from 09:25". |
| `assignment_pool.handover_min` | 15 min | assumed | Time to move a container from one truck to another. Not sourced. |
| `assignment_pool.fetch_circuity` | 1.3 | assumed | Road distance over straight-line distance on the drive to the load. Round value. |
| `assignment_pool.fetch_speed_mph` | 25 mph | assumed | Speed on the drive to the load, on surface streets. Round value. |
| Route distance and duration | OSRM | estimated | OSRM's car profile with free-flow speeds. Trucks are usually slower, so all times are optimistic. |
| Constant speed along a route | n/a | assumed | Needed because OSRM gives one duration per route. |
| Truck position and ETA | scenario | synthetic | From the seeded scenario. |
| Prior blockage probability | 0.02 / 0.40 / 0.80 / 0.50 / 0.60 | synthetic | Chosen per variant (normal, disruption, disruption_b, congestion, flood) to tell the demo story. |
| Inspection accuracy | 0.90 crash, 0.95 traffic, 0.65 flood visibility | assumed | Sensitivity parameter per kind of check, not validated on footage; aerial footage cannot certify depth or pavement integrity. |
| Inspection time | 6 min crash, 4 min traffic, 8 min road damage | assumed | `drone.launch_and_review_minutes` per kind of check. |
| Schools near a route | 300 m | estimated | OpenStreetMap may miss schools or list closed ones; distance is to the school's mapped point or outline centre. |
| Lateness, emissions and breach weights | $85/min, $0.25/kg, $2,500 | assumed | Unchanged; see DATA.md. |

## 8. LLM explanations

The code lives in `backend/llm.py` (client) and `backend/copilot.py` (roles). A language model writes text around the engine's results. It never sets a number, a ranking, the Bayes update or the preselected observation. Everything it writes carries `basis: "llm_generated"` and the model name, and the page labels it as LLM-written, advisory and not validated.

There are three roles:

1. **Second read of the footage.** The model gets a clip's CV assessment (motion counts, shares, rule thresholds), its detection counts, its image quality, the provisional keyframe annotation and the allowed observations. It returns an observation from that list, a confidence, a short rationale and up to three things to check in the clip. The dialog shows it under the CV suggestion. The review log records it as `llm_suggestion` and `agrees_with_llm`.
2. **Dispatcher brief.** The model gets `decision_facts`: the incident, the blockage chance before and now in whole percents, the inspection economics, the ranges of blockage chance where inspecting pays (the zero crossings of `sensitivity`), the ranked options with each road state's outcome, and the indicators. Differences it might quote, such as each option's gap to the best expected loss, are computed in advance so it does no arithmetic.
3. **Questions.** The model answers by calling four tools that run the engine: the decision summary, `information_value` at a given blockage chance, `rank_actions` at a given blockage chance, and one action's per-shipment rows. The loop stops after four rounds of tool calls.

Two guards check the output:

- **Rank-1 check.** A brief is dropped unless it recommends the engine's top-ranked option. Before an inspection it must also agree with `recommend_inspection`.
- **Number grounding.** Every number in the text must appear in what the model was given. A number counts as given if it matches a source value rounded to 0, 1 or 2 decimals, its absolute value, its percent when the value is a probability, or a number inside a source string (so `SYN-SHP-1042` and `09:30` pass). A footage read or brief that fails is dropped. An answer to a question is shown with a warning instead.

Without an API key the features are off. On a timeout, a network or HTTP error, or a reply that fails validation, the panel stays hidden and the page works as before. After a failure the client waits 30 seconds before trying again. `python -m scripts.llm_stub` serves canned replies for development and tests; it is not a model.

The guards catch a wrong pick and invented figures. They do not catch a wrong explanation built from real figures, so the text remains unvalidated advice.

## 9. Web check before the drone

When an incident report arrives, `backend/triage.py` searches public feeds near the reported spot before the dispatcher decides on a drone check. `backend/web_feeds.py` reads three sources with no key: the CHP incident log (every open California Highway Patrol incident, filtered to 10 km and to logs within 3 hours of the newest one), Caltrans District 7 message signs that show a message, and National Weather Service alerts for the point.

An LLM agent calls search tools over those feeds (CHP, signs, weather, each with a radius it chooses from 0.5 to 10 km) and ends with one finding: `corroborates` (a record shows the bad road state the check names), `contradicts` (a record shows the good state, such as traffic getting through) or `nothing_found`. Its report is rejected if it cites a record its searches did not return, if a corroborating or contradicting finding cites nothing, if it reported without searching, or if its summary has a number not in the records (the grounding check of section 8). The loop stops after five rounds. Without a usable LLM reply a keyword rule decides: a CHP log or sign within 2 km that mentions the hazard corroborates, unless it also says traffic is getting through, which contradicts. Weather alerts are shown but never decide the rule.

Nothing changes until the dispatcher clicks "Use this finding". The finding is then a Bayes update of the blockage chance with the assumed table `decision_model.web_triage.likelihoods`:

| Road state | corroborates | contradicts | nothing_found |
| --- | --- | --- | --- |
| blocked | 0.60 | 0.05 | 0.35 |
| open | 0.08 | 0.20 | 0.72 |

At the `disruption` variant's 40%, a corroborating finding gives 0.4 × 0.60 / (0.4 × 0.60 + 0.6 × 0.08) = 83%, and the drone check's net value drops from +$116 to −$604, so it is no longer recommended. A contradicting finding gives 14% and nothing found gives 24%. The updated chance becomes the prior for everything after it, including the drone's value and the drone update; the two are treated as independent given the road state.

Live feeds describe real roads now, while the incidents are fictional, so a live check usually finds nothing that matches. `config/web_triage_fixtures.json` holds synthetic records written for each incident (`SYN-` ids), used when the dispatcher picks "Demo feed" or when every live feed fails.

## 10. Offline route candidates and synthetic traffic

`scripts/build_route_candidates.py` requests up to three OSRM driving routes for each synthetic shipment and commits their geometry, distance and duration in `config/route_candidates.json`. For a truck's first en-route load, routing starts at its synthetic current position; later loads start at their synthetic origins. A saved scenario corridor is included when it connects the shipment's actual start and destination. OSRM sometimes returns only one route. These routes are **estimated driving candidates, not validated truck routes**.

`config/synthetic_traffic.json` supplies synthetic time-of-day duration multipliers, maximum extra-delay ranges and a few route-specific multipliers. `backend/route_optimizer.py` searches every combination of saved candidates for each truck's ordered loads. It propagates positive delay from an earlier load to a later load on the same truck. A candidate's estimated arrival is its synthetic planned ETA plus the difference between its traffic-adjusted duration and the baseline candidate's saved duration, plus any carried delay. The illustrative chance of arriving after the appointment starts assumes additional delay is uniformly distributed from zero to the period's `extra_delay_max_min`. Estimated loss uses **expected** lateness and missed-appointment probability under that uniform delay, plus the same driving and emissions weights as the decision engine. This is an assumed distribution, not a calibrated forecast. An optional time is a **hypothetical shift start** from the saved synthetic truck positions: all planned ETAs move with it; it is not a live GPS update.

Run `python3 scripts/preview_route_plan.py normal` to inspect the advisory search. It does not change dispatch, replace the incident/drone response model, account for incident probability, or validate restrictions such as truck access, bridge clearance or hazardous cargo. No route is applied without the dispatcher.

## Known limitations

- The web check's reliability table is assumed. A missing CHP log is weak evidence, and the table does not depend on how long ago the incident was reported.

- Rerouting is modeled as driving back to the start of the current route. A real dispatcher would use the nearest junction, so detour costs are overstated.
- Waiting in the queue counts toward driver time and cost but not toward emissions. Idling emissions are left out.
- During an inspection a free pool truck waits where it is. A real driver might keep driving toward the obstruction, which would cost nothing if the road is clear and add backtracking if it is not.
- The wait applies to every pool truck that is free before the inspection ends, even one that ends up keeping its own load.
- Only pool shipments respond to the disruption. Shipments outside the pool run on plan.
- The drive to the load is a straight line at a fixed speed. It ignores the blockage, so in the blocked state it understates how long SYN-TRK-12 needs to reach SYN-TRK-07 from beyond the obstruction.
- Each reassigned shipment is computed on its own. In a swap each truck drives to the other's position instead of both meeting once, which overstates the swap's driving.
- A truck that hands its load over is treated as free at its `available_at`, not at the end of the handover.
- A truck's own position is used as its load's position, even for trucks that have not picked their load up yet.

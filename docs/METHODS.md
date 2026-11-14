# PigeonOps methods

This file lists every formula the backend uses and every assumption behind it. All inputs are synthetic or estimated (see [DATA.md](../DATA.md)). Nothing here is measured.

The code lives in `backend/outcomes.py` (action outcomes), `backend/decision_engine.py` (ranking and value of inspection) and `backend/indicators.py` (plan indicators). Settings live in `config/port_scenario.json` under `decision_model`.

## Worked example

In the `disruption` variant, truck SYN-TRK-07 carries SYN-SHP-1042 on the `primary` route. Its appointment window is 10:15 to 10:45 and its planned ETA is 10:03.

| Action | Road | Arrival | Miles left | Driver min | Late min | Added cost $ | CO2 kg | Missed window | Path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold-course | open | 10:03 | 8.13 | 24.6 | 0 | 0 | 13.0 | 0 | primary |
| hold-course | blocked | 11:03 | 26.99 | 84.5 | 47.9 | 59.46 | 43.2 | 1 | primary to obstruction, 10 min turnaround, back to start, detour |
| reroute | either | 10:29 | 19.11 | 50.7 | 14.0 | 29.46 | 30.6 | 0 | primary back to start, detour |
| swap-truck | open / blocked | n/a | n/a | n/a | 12 / 9 | 410 / 460 | 20 / 22 | 0 | override, no route data |

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
3. **Blocked road.** If the trip uses the blocked route (`blockage.route`) and the truck has not yet passed the obstruction, it drives to the obstruction, waits `blockage.turnaround_min`, drives back to the route start, then drives the whole `blockage.fallback_route`.

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

Each computed outcome has `basis: "estimated"` and a `detail` block with arrival, miles, added miles, driver minutes, trip cost and path.

### Overrides

An action may have an `override` block with `open` and/or `blocked` values for any of `lateness_min`, `incremental_cost`, `emissions_kg` and `constraint_breaches`. An override value always wins over the computed value. An outcome with any overridden value gets `basis: "override"` and `override_basis` (for example `"assumed"`).

An action with no `plan` must override all four values in both states. `swap-truck` does this, because there is no route data for the truck transfer yet. Its numbers are hand-entered and include the knock-on cost to SYN-SHP-1048.

## 4. Ranking and value of inspection

These formulas are unchanged from the kickoff version.

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

An "inconclusive" result leaves the probability unchanged.

```text
P(says blocked)          = accuracy × p + (1 − accuracy) × (1 − p)
loss with inspection     = P(says blocked) × best loss after "blocked" + P(says clear) × best loss after "clear"
gross value              = best expected loss now − loss with inspection
net value                = gross value − (drone.direct_cost + drone.delay_cost)
recommend inspection     = net value > 0
```

The sensitivity curve repeats the net value at 21 evenly spaced priors from 0 to 1.

### Results per variant

| Variant | Prior P(blocked) | Best action now | Best loss now $ | Loss with inspection $ | Net value $ | Inspect? |
| --- | --- | --- | --- | --- | --- | --- |
| normal | 0.02 | hold-course | 120.72 | 68.23 | −112.51 | no |
| disruption | 0.40 | reroute | 1227.11 | 782.81 | +279.30 | yes |
| disruption_b | 0.80 | reroute | 1227.11 | 1227.11 | −165.00 | no |

In `disruption` an inspection can change the decision. A "clear" result drops the blockage probability to 7%, and keeping the current route becomes best.

In `disruption_b` the reports are corroborated, so the prior is 80%. Even after a "clear" result the probability stays at 31%, and rerouting is still best. The inspection cannot change the decision, so its gross value is zero and its net value is minus its cost.

## 5. Plan indicators

Indicators describe the whole plan under the top-ranked option before any inspection. `p` is the prior blockage probability. Only the affected shipment is re-simulated. Every other shipment with an assigned truck is assumed to run on plan.

| Indicator | Formula |
| --- | --- |
| `shipments_at_risk` | Other assigned shipments with negative `slack_min`, plus 1 if P(affected shipment arrives after window start) ≥ `at_risk_probability` |
| `appointments_feasible` | Assigned shipments, minus other shipments with negative slack, minus 1 if P(affected shipment misses window end) ≥ `at_risk_probability`. `of` is the total number of shipments, including unassigned ones |
| `expected_delay_min` | p × blocked lateness + (1 − p) × open lateness of the top option |
| `added_miles` | p × blocked added miles + (1 − p) × open added miles of the top option, against the planned trip |
| `co2_kg` | Full route miles of every assigned shipment × `co2_kg_per_mile`, plus the top option's expected emissions minus the planned trip's emissions |
| `community_exposure` | Not computed yet. It needs a school location dataset. Value `null`, basis `stub` |

P(...) here is the sum of the probabilities of the road states in which the condition holds.

Indicators are `basis: "estimated"` when the top option's outcomes come from route data. If the top option uses an override, they are `basis: "stub"`, and `added_miles` is `null` because an override carries no miles.

Each shipment counts as one full trip on its route. Return trips and empty repositioning are not counted, so `co2_kg` understates fleet emissions.

## 6. Assumptions

| Setting | Value | Basis | Notes |
| --- | --- | --- | --- |
| `rates.cost_per_mile` | $1.30 | assumed | Non-driver operating cost (fuel, maintenance, tires, truck payments, insurance). Round value of the size reported in ATRI's annual *Operational Costs of Trucking*; not checked against the latest edition. |
| `rates.driver_hourly_rate` | $35 | assumed | Loaded driver cost per hour, charged for driving and waiting. Round value; not sourced. |
| `rates.co2_kg_per_mile` | 1.6 kg | estimated | About 10.2 kg CO2 per gallon of diesel (EPA emission factor) divided by about 6.4 miles per gallon for a heavy truck. Tailpipe only. Battery-electric trucks are treated the same; this overstates their emissions. |
| `blockage.turnaround_min` | 10 min | assumed | Time lost reaching the queue and turning around. |
| `blockage.route` / `fallback_route` | primary / detour | assumed | The obstruction sits on `primary`; drivers fall back to the Anaheim St detour. |
| `indicator_rules.at_risk_probability` | 0.5 | assumed | Probability threshold for "at risk" and "infeasible". |
| Route distance and duration | OSRM | estimated | OSRM's car profile with free-flow speeds. Trucks are usually slower, so all times are optimistic. |
| Constant speed along a route | n/a | assumed | Needed because OSRM gives one duration per route. |
| Truck position and ETA | scenario | synthetic | From the seeded scenario. |
| Prior blockage probability | 0.02 / 0.40 / 0.80 | synthetic | Chosen per variant to tell the demo story. |
| Inspection accuracy | 0.90 | assumed | Sensitivity parameter, not validated on footage. |
| Lateness, emissions and breach weights | $85/min, $0.25/kg, $2,500 | assumed | Unchanged; see DATA.md. |

## Known limitations

- Rerouting is modeled as driving back to the start of the current route. A real dispatcher would use the nearest junction, so detour costs are overstated.
- Waiting in the queue counts toward driver time and cost but not toward emissions. Idling emissions are left out.
- The inspection's six-minute delay is a fixed `delay_cost`; it does not move the truck's arrival time.
- `swap-truck` is still a hand-entered override.
- Only one shipment responds to the disruption. Knock-on effects on other shipments appear only through the swap override.

# PigeonOps data methodology

PigeonOps uses **publicly calibrated synthetic data**. Public sources shape plausible geography, traffic, cargo, truck, cost, and emissions patterns, while every operational record shown in the demo is fictional. The prototype does not contain carrier, terminal, driver, appointment, container, or drone-operation records.

## What the demo contains

The shared operating picture in `config/port_scenario.json` contains:

- 12 synthetic shipments across three port terminals and six destination facilities
- 9 synthetic trucks with assignments, powertrains, status, duty time, and approximate simulated positions
- 4 illustrative corridors, including a current route, candidate detour, and two fleet context routes
- 3 scenario variants: routine morning operations, an unverified road disruption, and a corroborated one
- 3 dispatcher actions whose delay, cost, emissions, and constraint outcomes are computed from route data (one still uses a hand-entered override); see [docs/METHODS.md](docs/METHODS.md)
- 1 simulated observation with an explicit accuracy assumption

Identifiers use the `SYN-` prefix so they cannot be mistaken for valid container, carrier, truck, or shipment identifiers.

## Provenance classes

Every data element belongs to one of four classes:

| Class | Meaning | Examples in the prototype |
| --- | --- | --- |
| Public baseline | Directly based on public geography or published reference material | General port geography, highway corridors, terminal names |
| Publicly calibrated | Synthetic values shaped by public aggregates or distributions | Cargo categories, speed, turn time, truck characteristics |
| Derived estimate | Calculated from other scenario values | ETA slack, expected loss, posterior blockage probability |
| Synthetic or assumed | Created specifically for the demonstration | Shipments, appointments, truck assignments, incident, inspection accuracy |

The UI exposes these distinctions because a realistic-looking operational record must not be presented as measured real-world data.

## Public reference sources

### Port activity

- [Port of Los Angeles Container Statistics](https://portoflosangeles.org/business/statistics/container-statistics) publishes monthly and annual TEU totals.
- [BTS Port Performance Freight Statistics](https://www.bts.gov/ports) provides consistent port throughput, vessel, and berthing measures.
- [Port of Los Angeles Cargo Operations Dashboard](https://portoflosangeles.org/business/operations) describes terminal, appointment, turn-time, dwell, and trucking indicators. Some detailed Port Optimizer data requires registration.

Port-level TEUs are not treated as truck moves. TEU totals include rail movements and empty containers and cannot be converted directly into dispatch records without additional assumptions.

### Freight and cargo

- [Freight Analysis Framework 6](https://www.bts.gov/faf) provides 2022 tonnage and value by origin, destination, mode, and 42 commodity classes.
- [2022 Commodity Flow Survey](https://www.census.gov/programs-surveys/cfs.html) provides public freight statistics and privacy-protected microdata.

The demo cargo labels are synthetic examples aligned to Standard Classification of Transported Goods categories. They are not a claim about a specific terminal or container.

### Roads and traffic

- [Caltrans Performance Measurement System](https://dot.ca.gov/programs/traffic-operations/mpr/pems-source) provides historical detector, speed, volume, incident, closure, and travel-time data. A free account is required for detailed downloads.
- [Caltrans Traffic Census](https://dot.ca.gov/programs/traffic-operations/census) publishes AADT, truck AADT, ramp volume, and peak-hour data.
- [California Truck Network](https://dot.ca.gov/programs/traffic-operations/legal-truck-access/truck-network-map) is the official state reference for legal truck access.
- The live map uses an OpenStreetMap basemap through the ArcGIS Maps SDK. Route lines in the scenario are illustrative polylines, not turn-by-turn navigation.

The current 31 mph network speed and 47 minute terminal turn time are demonstration estimates. They are labeled as publicly calibrated rather than direct measurements for November 14, 2026.

### Trucks, costs, and emissions

- [2021 Vehicle Inventory and Use Survey](https://www.bts.gov/vius) covers truck configuration, use, weight, fuel, mileage, and fuel economy.
- [Caltrans Transportation Economics and Cal-B/C](https://dot.ca.gov/programs/transportation-planning/division-of-transportation-planning/state-planning/transportation-economics) provides reference values for truck time and vehicle operating costs.
- [CARB EMFAC](https://ww2.arb.ca.gov/our-work/programs/msei/on-road-emfac) is California's on-road emissions model.

The prototype's `$85 per late minute` is an assumed service-level penalty for the priority shipment, not a general value of truck travel time. The emissions weight is `$0.25 per kg`, corresponding to `$250 per metric ton` for demonstration purposes. The physical emissions values remain synthetic until route distance, speed, vehicle, and idling calculations are connected to an EMFAC export.

## Scenario generation logic

The prototype currently uses a hand-curated, deterministic scenario so the hackathon demonstration is repeatable.

1. Select a terminal, destination, commodity class, truck, appointment, and readiness state.
2. Assign a corridor and an ETA with appointment slack.
3. Mark shipments as on time, watch, at risk, planned, or unassigned.
4. In the disruption variant, introduce an uncertain road state with a 40% prior blockage probability.
5. Compute each action's outcomes from route distance and duration, then its loss under open and blocked states.
6. Calculate the value of an imperfect observation and update the probability with Bayes' rule after a simulated result.

The normal scenario uses a 2% blockage prior, so inspection is not cost-effective. The disruption scenario makes information valuable because the preferred action differs materially between road states. The corroborated disruption (`disruption_b`, 80% prior) shows the opposite case: rerouting is best whatever the inspection says, so inspection is not worth its cost.

## Decision model

For each action and possible road state:

```text
loss = lateness minutes × service penalty per minute
     + incremental operating cost
     + CO₂ kg × emissions value per kg
     + hard constraint breaches × breach penalty
```

Expected loss is the probability-weighted average across open and blocked states. Net information value is:

```text
best expected loss when acting now
− expected best loss after an imperfect observation
− observation direct and delay cost
```

Lower loss is better. The score is decision-support logic, not a market price, invoice, safety determination, or autonomous dispatch command.

## Known limitations

- Route lines are illustrative and must not be used for navigation.
- Terminal turn time, traffic speed, and emissions are not live measurements.
- Observation accuracy is assumed rather than validated on labeled footage.
- Driver hours are synthetic summaries and do not implement hours-of-service compliance.
- Community exposure is described qualitatively; the demo does not yet calculate population-weighted pollution or noise exposure.
- Action outcomes are point estimates from route data and assumed rates (see [docs/METHODS.md](docs/METHODS.md)) rather than Monte Carlo distributions.
- Inspection delay is monetized as a configured cost; a future model should also propagate the six-minute wait through every shipment ETA and appointment constraint.

## Recommended next data step

After the hackathon, add an offline generator that snapshots permitted public aggregates, records retrieval dates and checksums, fits empirical distributions, and produces seeded scenario files. Keep the two curated scenarios for the presentation and use generated cases for sensitivity testing.

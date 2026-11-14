# PigeonOps: Drone-Assisted Logistics Decision Intelligence

A dependency-light, one-day-hackathon starter for deciding **whether more information is worth gathering before a dispatcher acts**. The demo uses synthetic Port of Los Angeles / Long Beach operations, a simulated road disruption, and a simulated observation feed. It never controls or deploys a real drone.

The network view uses ArcGIS Maps SDK for JavaScript 5.2 with an OpenStreetMap basemap, so it does not require an ArcGIS API key or a paid ArcGIS location service. If the CDN or map tiles are unavailable, the original schematic remains as an offline fallback.

Repository slug: `pigeon-ops`

## Run it

Requires Python 3.9+ and no package installation.

```bash
python3 run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Click **Run simulated inspection**, then choose the evidence returned by the stub. The ranked dispatcher options and blockage probability update immediately.

Run the automated checks with:

```bash
python3 -m unittest discover -v
```

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

## Demo story (about 90 seconds)

1. `SHP-1042`, a medical-device container, may miss its warehouse appointment because CA-47 could be blocked.
2. The dashboard ranks acting now: keep course, reroute, or swap trucks.
3. The expected-value-of-information card explains whether a six-minute simulated inspection is worth its cost.
4. Run the inspection and select **Road blocked** or **Road clear** as the prerecorded/CV-stub result.
5. Show the posterior blockage probability and newly ranked options. Emphasize that a dispatcher approves any action.

## How the decision model works

Every action has outcomes for two possible states: `open` and `blocked`. Each outcome records lateness, incremental cost, emissions, and hard-constraint breaches. The engine converts these to a configurable loss:

```text
loss = lateness × $/minute
     + direct cost
     + emissions × $/kg
     + constraint breaches × penalty
```

It ranks actions by probability-weighted expected loss. Inspection accuracy is used for Bayesian updates. The expected value calculation is intentionally visible:

```text
net information value
  = best expected loss when acting now
  − expected best loss after an imperfect observation
  − inspection direct and delay cost
```

This is a decision-support prototype, not an operational safety or flight system. The numbers are synthetic and deliberately easy to challenge during judging.

## Project layout

```text
config/port_scenario.json     Scenario, map, shipments, weights, actions
backend/decision_engine.py    Auditable scoring, ranking, Bayes, information value
backend/scenario.py           Scenario loading and basic validation seam
backend/server.py             Tiny JSON API and static-file server
frontend/index.html           Dashboard structure
frontend/styles.css           Responsive visual design
frontend/app.js               Map rendering and interaction
tests/test_decision_engine.py Decision-model tests
run.py                        One-command entry point
```

Useful endpoints:

- `GET /api/health`
- `GET /api/scenario`
- `GET /api/analysis?observation=blocked`
- `GET /api/analysis?observation=clear`
- `GET /api/analysis?observation=inconclusive`

## Extend it quickly

- Add actions or tune weights in `config/port_scenario.json`; no UI code changes are needed.
- Add an ArcGIS Location Platform API key only if Esri-hosted basemaps, routing, geocoding, or other metered services are needed. Review current quotas before enabling them.
- Replace the observation buttons with a prerecorded video plus a CV adapter that returns `blocked`, `clear`, or `inconclusive` and confidence.
- Add a `ScenarioRepository` behind `scenario.py` for SQLite, Postgres, or a logistics API.
- Add authentication, audit logs, geofencing, aviation checks, and explicit authorization before considering any real-world field integration.

## Suggested partner split

**Partner A — operations and decision intelligence**

- Add two scenarios (terminal congestion and missed appointment cascade).
- Interview an operator/judge and tune weights, constraints, and labels.
- Add sensitivity analysis: show when the recommendation flips as blockage probability or inspection time changes.
- Prepare the demo narrative and validate that every metric is defensible.

**Partner B — evidence and product experience**

- Add a 10–20 second prerecorded inspection clip.
- Implement a `cvAdapter` interface returning label, confidence, timestamp, and source.
- Upgrade the map to MapLibre/Leaflet and animate truck position.
- Add a dispatcher “Approve option” interaction and an in-memory audit trail.

**Work together last**

- Integrate one additional scenario, rehearse failure paths, run tests, and verify the demo without internet access.

## Guardrails

- Synthetic data only.
- No real drone command, mission planning, or automated deployment.
- No action is executed; recommendations remain advisory.
- Real deployment would require operator authorization, safety review, airspace/legal compliance, privacy controls, and validated data sources.

## ArcGIS licensing and cost boundary

- ArcGIS Maps SDK for JavaScript requires no additional purchase when used with ArcGIS Location Platform. This demo loads the SDK from Esri's CDN.
- ArcGIS Location Platform uses pay-as-you-go pricing. Esri-hosted basemap tiles currently include a free monthly allowance, then charge for additional usage.
- PigeonOps deliberately does not configure an ArcGIS token or call a metered ArcGIS location service. Its basemap is OpenStreetMap and its route/marker data stays client-side.

Official references: [SDK terms of use](https://developers.arcgis.com/javascript/latest/licensing/) and [ArcGIS Location Platform pricing](https://developers.arcgis.com/pricing/).

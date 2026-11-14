# PigeonOps: Drone-Assisted Logistics Decision Intelligence

A dependency-light hackathon prototype for deciding **whether more information is worth gathering before a dispatcher acts**. The demo uses publicly calibrated synthetic Port of Los Angeles / Long Beach operations, a simulated road disruption, and a simulated observation feed. It never controls or deploys a real drone.

The network view uses ArcGIS Maps SDK for JavaScript 5.2 with a muted OpenStreetMap basemap and no API key. A timeline under the map plays the morning forward: every truck moves along road-following paths, and in the disruption scenario you can play out each dispatch option with CA-47 open or blocked. If the CDN is unavailable, or the screen is narrower than 920px, an SVG schematic plays the same timeline.

Repository slug: `pigeon-ops`

## Run it

Requires Python 3.9+ and no package installation.

```bash
python3 run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Switch between **Normal flow** and **Changed condition**, inspect the shipment and truck network, then click **Run simulated inspection**. The ranked dispatcher options and blockage probability update immediately.

The demo includes 12 fictional shipments, 9 fictional trucks, multiple port-to-warehouse corridors, appointment and readiness context, and visible data-provenance labels. Read [DATA.md](DATA.md) for the source inventory, modeling choices, and limitations.

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

## Demo story (about 90 seconds)

1. Start with **Normal flow**: the fleet is visible, four appointments are tight, and inspection is not worth its cost.
2. Switch to **Changed condition**: `SYN-SHP-1042` may miss its appointment because CA-47 could be blocked.
3. Compare keep course, reroute, and truck-swap options, including stakeholder and environmental effects.
4. Show why a six-minute simulated inspection is worth gathering in this condition.
5. Press play under the map to watch the model's pick unfold. Switch **Play out** or the CA-47 toggle to compare options, and click a truck to follow it.
6. Run the inspection and select **Road blocked**, **Road clear**, or **Inconclusive** as the stub result. The map switches to the new best option.
7. Show the posterior blockage probability and newly ranked options. Emphasize that a dispatcher approves any action.

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
AGENTS.md                     Team rules, file ownership, data contracts (CLAUDE.md imports it)
DATA.md                       Data sources, modeling choices, limitations
config/port_scenario.json     Scenario, map, shipments, trucks, weights, actions, variants
backend/decision_engine.py    Auditable scoring, ranking, Bayes, information value, sensitivity
backend/scenario.py           Scenario loading and variant overlays
backend/playback.py           Minute-by-minute truck schedules for the map timeline
config/road_geometry.json     Saved road paths for every playback leg (from OSRM)
scripts/build_road_geometry.py  Refreshes road_geometry.json after scenario edits
backend/server.py             Tiny JSON API and static-file server
frontend/index.html           Dashboard structure
frontend/styles.css           Responsive visual design
frontend/arcgis.css           ArcGIS map styles
frontend/app.js               Map rendering and interaction
frontend/evidence/            Inspection evidence files from the CV pipeline
tests/                        Decision-model, contract, and server tests
run.py                        One-command entry point
```

Useful endpoints:

- `GET /api/health`
- `GET /api/scenarios`
- `GET /api/scenario?scenario=normal`
- `GET /api/scenario?scenario=disruption`
- `GET /api/analysis?scenario=disruption&observation=blocked`
- `GET /api/analysis?scenario=disruption&observation=clear`
- `GET /api/analysis?scenario=disruption&observation=inconclusive`
- `GET /api/playback?scenario=disruption&action=swap-truck&road=blocked`

## Extend it quickly

- Add actions or tune weights in `config/port_scenario.json`; no UI code changes are needed.
- After moving locations or trucks, or adding shipments, run `python3 scripts/build_road_geometry.py` to fetch road paths for the new legs. It calls the public OSRM demo server about once per second; the app itself reads only the saved file.
- Add an ArcGIS Location Platform API key only if Esri-hosted basemaps, routing, geocoding, or other metered services are needed. Review current quotas before enabling them.
- Replace the observation buttons with a prerecorded video plus a CV adapter that returns `blocked`, `clear`, or `inconclusive` and confidence.
- Add a `ScenarioRepository` behind `scenario.py` for SQLite, Postgres, or a logistics API.
- Add authentication, audit logs, geofencing, aviation checks, and explicit authorization before considering any real-world field integration.

## Team split

Three tracks (engine, map and UI, drone evidence) with separate file ownership, all working on `main`. See `AGENTS.md`.

## Guardrails

- Synthetic data only.
- No real drone command, mission planning, or automated deployment.
- No action is executed; recommendations remain advisory.
- Real deployment would require operator authorization, safety review, airspace/legal compliance, privacy controls, and validated data sources.

## ArcGIS licensing and cost boundary

- ArcGIS Maps SDK for JavaScript requires no additional purchase when used with ArcGIS Location Platform. This demo loads the SDK from Esri's CDN.
- ArcGIS Location Platform uses pay-as-you-go pricing. Esri-hosted basemap tiles currently include a free monthly allowance, then charge for additional usage.
- PigeonOps deliberately does not configure an ArcGIS token or call a metered ArcGIS location service. Its basemap is OpenStreetMap, road paths were fetched once from OSRM and are served from this repository, and route and marker data stays client-side.

Official references: [SDK terms of use](https://developers.arcgis.com/javascript/latest/licensing/) and [ArcGIS Location Platform pricing](https://developers.arcgis.com/pricing/).

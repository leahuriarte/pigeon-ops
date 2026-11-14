# PigeonOps: Drone-Assisted Logistics Decision Intelligence

A dependency-light hackathon prototype for deciding **whether more information is worth gathering before a dispatcher acts**. The demo uses publicly calibrated synthetic Port of Los Angeles / Long Beach operations, a simulated road disruption, and a simulated observation feed. It never controls or deploys a real drone.

The network view uses ArcGIS Maps SDK for JavaScript 5.2 with a muted OpenStreetMap basemap and no API key. A timeline under the map plays the morning forward: every truck moves along road-following paths, and after an incident report you can play out each dispatch option with CA-47 open or blocked. If the CDN is unavailable, or the screen is narrower than 920px, an SVG schematic plays the same timeline.

Repository slug: `pigeon-ops`

The inspection step plays top-down drone clips annotated by an offline CV pipeline
(vehicle detection, speed net of camera motion, a road-state suggestion). The
annotated library is committed; rebuilding it is documented in [cv/README.md](cv/README.md).

## Run it

Requires Python 3.9+ and no package installation.

```bash
python3 run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On Windows use `python` instead of `python3`. Press **Start the morning**, or jump straight to one of the four incident reports, then follow the steps on the right: incident, drone check, response, outcome.

The demo includes 12 fictional shipments, 9 fictional trucks, multiple port-to-warehouse corridors, appointment and readiness context, and visible data-provenance labels. Read [DATA.md](DATA.md) for the source inventory, modeling choices, and limitations.

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

## Demo story

1. Start screen: the usual morning. The fleet is on the map, the indicator tiles show the usual plan, and the note under the start button says a drone check is not worth it with no report (2% chance, a $101 loss).
2. Pick an incident report. Each one calls for a different kind of drone check:

   | Incident | Kind of check | Chance | Best response now | Check first? |
   | --- | --- | --- | --- | --- |
   | Possible overturned vehicle | Crash check | 40% blocked | Reroute | Yes, worth $116 |
   | Corroborated overturned vehicle | Crash check | 80% blocked | Reroute | No, it cannot change the decision |
   | Heavy truck queue | Traffic check | 50% jammed | Keep current route | Yes, worth $196 |
   | Burst water main, possible flooding | Road damage check | 25% impassable | Reroute | Yes, worth $220 |

3. Open **Show the numbers** for the calculation, the chart of when a check pays off, and the accuracy the check needs.
4. Click **Review drone footage**. The dialog shows the clips that fit the kind of check. The CV suggestion is preselected and marked unvalidated; you confirm or change it. To show a check changing the decision, pick a clear-road clip in the crash incident: the chance drops from 40% to 7%, **Keep current route** becomes best, and the 11-mile detour past 6 schools is avoided. The road damage check shows a flooded lane with no vehicles in view: the CV, which only tracks vehicles, stays inconclusive, and the dispatcher decides from what they see.
5. Choose a response. Each card shows delay, cost, CO₂ and the schools within 300 m of its route. Picking a response the model did not rank first asks for a reason, which goes in the decision log.
6. Watch it play out, then open the shift report: the usual morning against today on six indicators, and the decision log.

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
scripts/build_network.py      Refreshes each scenario route's geometry, distance_m and duration_s
backend/server.py             Tiny JSON API and static-file server
frontend/index.html           Dashboard structure
frontend/styles.css           Responsive visual design
frontend/arcgis.css           ArcGIS map styles
frontend/app.js               Map rendering and interaction
frontend/inspection.js        Drone clip review dialog (library served by backend/inspection.py)
cv/                           Offline CV pipeline with its own environment; see cv/README.md
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

# PigeonOps agent guide

PigeonOps is a one-day hackathon prototype (GeoAI track, perspective 3: resilience, cost and sustainability). A port dispatcher sees shipments on a map, a disruption appears, and the tool calculates whether a drone inspection is worth its cost before recommending a response. All data is synthetic (see DATA.md), no real drone is controlled, and the dispatcher makes every decision.

## Run and test

- `python run.py`, then open http://127.0.0.1:8000
- `python -m unittest discover -s tests -v` must pass before every commit.

## Who owns what

| Track | Owns |
| --- | --- |
| A: decision engine, route data, indicators | `backend/`, `config/`, `tests/`, `scripts/`, `DATA.md`, `docs/METHODS.md` |
| B: map and dispatcher UI | `frontend/` except `frontend/evidence/` |
| C: drone footage and computer vision | `cv/`, `frontend/evidence/` |
| Shared, change only with team agreement | `AGENTS.md`, `CLAUDE.md`, `README.md`, `run.py`, `.gitignore` |

Never edit files another track owns. If you need a change there, say so in your final message instead.

## Rules

- Everyone works on `main` for now. Do not create branches or worktrees. Pull before starting a task, keep commits small, and push often.
- `backend/` uses the Python standard library only. Heavy dependencies (ultralytics, opencv) live in `cv/` with its own virtual environment and write precomputed files.
- The frontend is plain HTML, CSS and JavaScript with no build step and no framework. The map is the ArcGIS Maps SDK for JavaScript 5.2 loaded from Esri's CDN with an OpenStreetMap basemap and no API key; keep it, and keep the offline SVG schematic fallback working. Other third-party libraries go in `frontend/vendor/`, not a CDN.
- Label every stub, synthetic, assumed or estimated number, in data (`basis` or `data_provenance`) and in the UI. Never present one as measured. Synthetic ids use the `SYN-` prefix.
- Recommendations are advisory. Nothing triggers an action without the dispatcher.
- Do not change a contract below without the team agreeing. Update this file and `tests/test_contracts.py` in the same commit.
- Do not add frameworks or change the stack.

## Contracts

### 1. Scenario data: `config/port_scenario.json`

One shared operating picture plus named variants:

- Base fields: `map_bounds`, `locations[]`, `shipments[]`, `trucks[]`, `routes[]` (`id`, `name`, `role`, `color`, `points` as `[lat, lon]` pairs), `operations`, `data_provenance[]`, `decision_model` (weights, `drone` settings, `actions` with `open` and `blocked` outcomes and a `stakeholder_effect`).
- `scenario_variants`: currently `normal` (inspection not worth it) and `disruption` (inspection worth it). `backend/scenario.py` deep-merges the chosen variant over the base; `shipment_overrides` and `truck_overrides` patch records by id.
- Each variant sets `id`, `name`, `scenario_type`, a `disruption` (with `prior_blocked_probability`; the disruption variant also lists `evidence_clips`, clip ids in `frontend/evidence/`), and `indicators_stub`, the indicator list from contract 2, used until Track A calculates indicators.
- Planned (Track A, A1): each route gains a GeoJSON LineString `geometry` with `[lon, lat]` coordinates, plus `distance_m` and `duration_s`.

### 2. API

- `GET /api/scenarios` returns `{"scenarios": [{id, label}]}`.
- `GET /api/scenario?scenario=<variant>` returns the merged scenario plus `analysis`. The default variant is `disruption`. Unknown variant returns 404.
- `GET /api/analysis?scenario=<variant>&observation=<blocked|clear|inconclusive>` returns `scenario_id`, `prior_blocked_probability`, `posterior_blocked_probability`, `observation`, `before[]`, `after[]`, `evoi`, `sensitivity` (21 points of `{prior, net_value}` from 0 to 1), `indicators` and `assignments`. Bad observation returns 400.
- `assignments` is `{shipments[], trucks[], count, before[], after[]}`: every assignment of the `decision_model.assignment_pool` trucks to its shipments, ranked by expected loss (`after` is empty without an observation). Each option has `id`, `trucks` (shipment id to truck id), `planned`, `action` (matching action id or `null`), `rank`, `expected_loss` and `outcomes`. Every computed outcome, for options and for actions, carries `detail.shipments[]`, one row per pool shipment with at least `shipment`, `truck`, `route`, `reassigned`, `arrival` (HH:MM), `appointment`, `slack_min`, `lateness_min`, `constraint_breaches` and `basis`.
- `indicators` is a list of `{id, label, value, unit, basis}`, plus `of` for `appointments_feasible`. Ids: `shipments_at_risk`, `appointments_feasible`, `expected_delay_min`, `added_miles`, `co2_kg`, `community_exposure`. `basis` is `stub`, `estimated`, `simulated` or `measured`. `value` may be `null` while not yet computed. Indicators describe the whole plan under the top-ranked option before any inspection.

### 3. Inspection evidence: `frontend/evidence/<clip_id>.json`

Served at `/evidence/<clip_id>.json`. Fields: `clip_id`, `basis`, `label` (`blocked`, `clear` or `inconclusive`), `confidence` (0 to 1), `vehicle_count`, `stationary_count`, `flow_per_min`, `frames_analyzed`, `model`, `video` (path relative to `frontend/`, such as `evidence/<clip_id>.mp4`, or `null` until a clip exists), `location` (`{lat, lon}`), `location_simulated`, `source`, `license`.

`confidence` is the classifier's own score, not the probability that the road is blocked. The engine's Bayes update uses the inspection accuracy in `decision_model.drone`.

## Gotchas

- Coordinate order: GeoJSON, OSRM and ArcGIS `paths` use `[lon, lat]`. The scenario's `points` use `[lat, lon]`.
- Browsers refuse OpenCV's default mp4 codec. Re-encode clips with `ffmpeg -i in.mp4 -c:v libx264 -pix_fmt yuv420p -movflags +faststart out.mp4`.
- The built-in Python server does not support HTTP range requests, so keep clips short; they play but may not seek.

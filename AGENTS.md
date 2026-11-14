# PigeonOps agent guide

PigeonOps is a one-day hackathon prototype (GeoAI track, perspective 3: resilience, cost and sustainability). A port dispatcher sees shipments on a map, a disruption appears, and the tool calculates whether a drone inspection is worth its cost before recommending a response. All data is synthetic (see DATA.md), no real drone is controlled, and the dispatcher makes every decision.

## Run and test

- `python run.py`, then open http://127.0.0.1:8000
- `python -m unittest discover -s tests -v` must pass before every commit.

## Who owns what

| Track | Owns |
| --- | --- |
| A: decision engine, route data, indicators | `backend/`, `config/`, `tests/`, `scripts/`, `DATA.md`, `docs/METHODS.md` |
| B: map and dispatcher UI | `frontend/` except `frontend/inspection.*` |
| C: drone footage and computer vision | `cv/`, `frontend/inspection.*`, `backend/inspection.py` |
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
- `scenario_variants`: `normal` (inspection not worth it) and four incident reports: `disruption` (possible crash, inspection worth it), `disruption_b` (corroborated crash, not worth it), `congestion` (heavy traffic) and `flood` (road damage). `backend/scenario.py` deep-merges the chosen variant over the base; `shipment_overrides` and `truck_overrides` patch records by id.
- Each variant sets `id`, `name`, `scenario_type` and a `disruption` (with `prior_blocked_probability`). An incident's `disruption.check` names the kind of drone check: `type` (`crash`, `congestion` or `environment`), `label`, and the words `bad` and `good` for the two road states (blocked/open, jammed/flowing, impassable/passable). A variant may override `decision_model.drone` (accuracy and time per kind of check) and `decision_model.blockage` (`effect: "slowdown"` with `slowdown_min` for heavy traffic).
- `decision_model.web_triage.likelihoods` is the assumed P(finding | road state) for the web check, with findings `corroborates`, `contradicts` and `nothing_found`. `config/web_triage_fixtures.json` holds synthetic feed records (`SYN-` ids) per incident variant for the demo feed.
- Planned (Track A, A1): each route gains a GeoJSON LineString `geometry` with `[lon, lat]` coordinates, plus `distance_m` and `duration_s`.

### 2. API

- `GET /api/scenarios` returns `{"scenarios": [{id, label}]}`.
- `GET /api/scenario?scenario=<variant>` returns the merged scenario plus `analysis`. The default variant is `disruption`. Unknown variant returns 404.
- `GET /api/analysis?scenario=<variant>&observation=<blocked|clear|inconclusive>` returns `scenario_id`, `prior_blocked_probability`, `posterior_blocked_probability`, `observation`, `web_evidence`, `before[]`, `after[]`, `evoi`, `sensitivity` (21 points of `{prior, net_value}` from 0 to 1), `indicators` and `assignments`. Bad observation returns 400.
- `GET /api/triage?scenario=<variant>&mode=<live|demo>` runs the web check: `{available, mode_requested, mode, fallback_reason, agent ("llm" or "rules"), basis, model, finding, confidence, summary, cited_ids[], searches[], sources[], evidence[], effect, check}`. Each evidence item has `id`, `source`, `source_url`, `title`, `detail`, `time`, `distance_km`, `basis` (`live` or `synthetic`) and `cited`. `effect` has `prior_before`, `prior_after`, `inspection_before` and `inspection_after` (`{net_value, recommend}`). `available` is false for `normal`. Bad mode returns 400.
- The dispatcher-applied web finding is passed as `web=<corroborates|contradicts|nothing_found>` on `/api/scenario`, `/api/analysis` and `/api/llm/brief`, and as `web` in the bodies of `POST /api/llm/ask` and `POST /api/inspection-review`. The server then starts from the updated `prior_blocked_probability`, and `analysis.web_evidence` is `{finding, prior_before, prior_after, basis}` (null without one). A bad value, or one for `normal`, returns 400.
- `assignments` is `{shipments[], trucks[], count, before[], after[]}`: every assignment of the `decision_model.assignment_pool` trucks to its shipments, ranked by expected loss (`after` is empty without an observation). Each option has `id`, `trucks` (shipment id to truck id), `planned`, `action` (matching action id or `null`), `rank`, `expected_loss` and `outcomes`. Every computed outcome, for options and for actions, carries `detail.shipments[]`, one row per pool shipment with at least `shipment`, `truck`, `route`, `reassigned`, `arrival` (HH:MM), `appointment`, `slack_min`, `lateness_min`, `constraint_breaches` and `basis`.
- `indicators` is a list of `{id, label, value, unit, basis}`, plus `of` for `appointments_feasible`. Ids: `shipments_at_risk`, `appointments_feasible`, `expected_delay_min`, `added_miles`, `co2_kg`, `community_exposure`. `basis` is `stub`, `estimated`, `simulated` or `measured`. Indicators describe the whole plan under the top-ranked option before any inspection. Each entry of `before` and `after` also has `schools_within_300m`, the schools near the route that response sends the affected load on (from `config/community_exposure.json`, built by `scripts/build_community_exposure.py`).

### 3. Drone inspection library: `cv/review_library/library.json`

Built offline from top-down drone clips by `python -m cv.prepare_review` (catalog `cv/drone_review.json`) and committed, so the demo works without CV dependencies:

- `GET /api/inspection-cases` returns `{available, cases[]}`; `available` is false and `cases` empty when the library is missing, and the page falls back to simulated observation buttons.
- Each case has `id`, `title`, `visual_label`, `review_note`, `image_quality`, `annotated_video`, `annotated_image` and `source_image` (served under `/inspection-assets/`), `vehicle_detections`, `excluded_detections`, `check_types` (the kinds of drone check the clip fits; the dialog shows only clips that fit the incident's `check.type`), `allowed_observations` (a subset of `blocked`, `clear`, `inconclusive`), `cv_assessment` and `source` (`source_url`, `sha256`, `credit`, `license`, `location`, `historical`).
- `cv_assessment` is the CV suggestion from vehicle motion on the marked road: `suggested_observation`, `motion_state`, `reason`, `vehicles_on_road`, `stopped`, `moving` and `basis`. It is unvalidated; the dialog preselects it and the dispatcher confirms or overrides it.
- `POST /api/inspection-review` takes `{case_id, scenario, observation, note, simulated_application: true}`, appends the review (with `cv_suggestion` and `agrees_with_cv`) to `cv/outputs/inspection-reviews.jsonl` and returns `{analysis, review}`. An observation outside the case's `allowed_observations` returns 400.

A reviewed clip is an illustration applied to the fictional scenario. The Bayes update uses `decision_model.drone.accuracy` (assumed, for dispatcher-reviewed footage); `decision_model.drone.cv_only` holds the measured table for automated still-image CV alone, shown for comparison only.

## Gotchas

- Coordinate order: GeoJSON, OSRM and ArcGIS `paths` use `[lon, lat]`. The scenario's `points` use `[lat, lon]`.

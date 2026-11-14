# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

PigeonOps is a hackathon prototype for the GeoAI track's "Port-to-Warehouse Visibility and Decision Support" challenge. It is a synthetic Port of LA / Long Beach dispatcher dashboard. Its core idea is to decide **whether gathering more evidence (a simulated drone inspection) is worth its cost before acting**, using expected value of information (EVOI). `AGENTS.md` holds the shared contributor rules, and `DATA.md` documents data provenance and modeling assumptions.

## Commands

The demo needs Python 3.9+ and only the standard library.

```bash
python run.py                                   # dashboard + API at http://127.0.0.1:8000
python -m unittest discover -s tests -v         # full suite
python -m unittest tests.test_decision_engine -v
python -m unittest tests.test_decision_engine.DecisionEngineTests.test_demo_recommends_inspection
```

No build, lint, or formatter step is configured. There's no hot reload, so restart `run.py` after backend or config changes. Frontend files are served straight from disk.

The CV pipeline (`cv/`) is separate and has heavy dependencies. Install them into `.venv` with `pip install -r cv/requirements.txt` (`requirements-lock.txt` pins the full set). `tests/test_cv_metrics.py` only imports `cv.metrics`, which is pure Python, so the suite still runs without ultralytics installed.

```bash
python -m cv.download_data [--dataset uav-obb|transset]   # fetch + checksum-verify into cv/data/
python -m cv.benchmark --model yolo11n.pt --imgsz 640 --split valid [--limit N] [--tile-size 640]
python -m cv.inspect_images --source <image-or-folder> --name <out-folder> [--tile-size N]
python -m cv.inspect_video --source <video> --name <out-folder>
python -m cv.evaluate_road_state --model yolo11s.pt --imgsz 1280 --tile-size 640   # road-state confusion vs annotations
```

`--model aerial-ensemble` unions COCO `yolo11s.pt` at 1280 with the DOTA-trained `yolo11m-obb.pt` at 1024 and ignores `--imgsz`. It's the best detector so far: on the first 40 UAV-OBB valid frames, F1 0.72 against 0.58–0.59 for either model alone. OBB results are converted to axis-aligned envelopes in `common.detections`. `cv/road_state.py` is pure Python. It turns boxes inside a road polygon into `dense_traffic`/`light_traffic`/`unclear` using nearest-neighbour spacing, and maps those to the engine's `blocked`/`clear`/`inconclusive` signals. `cv/road_rois.json` holds hand-drawn road polygons for 27 UAV-OBB frames.

Run CV commands as modules (`python -m cv.x`) from the repo root, because they import `cv.common`. Outputs go to `cv/outputs/<name>/` (`evidence.json`, `report.html`, annotated images). `cv/data/`, `cv/models/`, and `cv/outputs/` are gitignored.

## Architecture

**Scenario config → variant overlay → decision engine → JSON API → static dashboard.**

- `config/port_scenario.json` is the single source of scenario values: locations, shipments, trucks, routes, data provenance, the `decision_model` (loss weights, drone accuracy/cost, actions with `open`/`blocked` outcomes), and `scenario_variants`.
- `backend/scenario.py` `load_scenario(variant=...)` deep-merges a variant (`normal` or `disruption`) over the base config. Variants can also hold `shipment_overrides` and `truck_overrides`, keyed by id. A variant can replace the whole `decision_model.actions` list; the normal variant does this. New scenarios are added as new variants. `test_scenario_catalog_contains_demo_pair` asserts the exact variant list, so update it when you add one.
- `backend/decision_engine.py` is the whole model. Each action's loss is `lateness×$/min + cost + emissions×$/kg + breaches×penalty`, weighted by P(blocked). `posterior_blocked` takes a P(signal | road state) table. `observation_likelihoods(drone)` builds that table from `drone.observation_model` (measured CV error × assumed traffic behaviour), or falls back to a symmetric `drone.accuracy`, under which `inconclusive` leaves the prior unchanged. EVOI sums over all three signals. The demo's `accuracy: 0.9` stands for a dispatcher reviewing drone imagery with CV help (`accuracy_basis`). `drone.cv_only_model` holds the measured automated-CV evidence, which `analyze()` reports as `evoi.cv_only`; today it is not worth a flight. `evoi.break_even_accuracy` is found by bisection, which is valid because information value never decreases as accuracy rises. `analyze()` returns `before`/`after` rankings plus an `evoi` block (gross value − drone `direct_cost` + `delay_cost`). Tests pin the demo story: disruption ⇒ inspection recommended, normal ⇒ act now. Retuning config values can break these.
- `backend/server.py` is a `ThreadingHTTPServer` with no framework. Its endpoints are `/api/health`, `/api/scenarios`, `/api/scenario?scenario=`, and `/api/analysis?scenario=&observation=blocked|clear|inconclusive`. Everything else is served from `frontend/` with a path-traversal guard.
- `frontend/app.js` is vanilla JS. It tries the ArcGIS Maps SDK (CDN, OSM basemap, no API key) and falls back to an SVG schematic (`drawSchematicMap`) if the CDN or tiles fail. Keep that offline fallback working. The "Run simulated inspection" buttons call `/api/analysis` with a stub observation.

**CV pipeline (`cv/`)** is offline, CPU-only, and not yet wired into the server. `common.predict_frame` runs pretrained YOLO11 (COCO car/bus/truck classes only, no fine-tuning). Optional overlapping tiles are merged with class-agnostic IoU-0.5 suppression. `benchmark.py` evaluates against UAV-OBB by collapsing oriented boxes into axis-aligned envelopes, so its precision/recall/F1 are *not* official OBB mAP. The inspection scripts always emit `label: "inconclusive"` and `review_status: "needs_human_review"`. That is deliberate: a vehicle detection is not a blockage probability. The planned integration point is a CV adapter that returns `blocked`/`clear`/`inconclusive` plus confidence to the existing `observation` parameter. Datasets (UAV-OBB in China, TRANSSET in North Carolina) are illustrative and are not Port of LA footage. Reports must keep that provenance and attribution visible.

## Constraints that shape changes

- Keep decision math in the backend and scenario numbers in config, not in JS.
- Everything is synthetic and advisory. No real drone commands, mission planning, or automatic dispatch; the dispatcher approves every action.
- The challenge requires visibly labeling simulated/estimated values and uncertainty, and showing at least two responsible-design considerations (fairness/stakeholder impact or environmental impact, plus one more). Don't present model outputs or CV detections as confirmed facts.
- Add focused `unittest` regressions when changing ranking, Bayes updates, EVOI, or CV matching metrics.

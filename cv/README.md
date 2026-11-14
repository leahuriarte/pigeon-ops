# Offline CV inspection

This optional component detects cars, buses and trucks in still images and video
and turns them into reviewable evidence. Nothing here controls a drone or changes
a dispatch decision on its own. It feeds the dashboard in two ways:

1. **Image review library (used live).** Prepared California images with marked
   road areas. A dispatcher picks the observation in the dashboard; the engine
   then uses its assumed 90% accuracy for reviewed imagery.
2. **Measured CV-only evidence (shown for comparison).** An automatic road-state
   rule is scored against annotated aerial images. Its error table becomes
   `decision_model.drone.cv_only` in the scenario config, and the dashboard shows
   what automated CV alone would be worth.

[SOURCES.md](SOURCES.md) lists data and model provenance and terms.
[RUN_RESULTS.md](RUN_RESULTS.md) records the measured results.

## Setup on Windows

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r cv/requirements.txt
```

Models download on first use. Inference runs on CPU. `requirements-lock.txt`
records the tested environment. The main logistics demo needs none of this.
Run every script as a module from the repository root (`python -m cv.<name>`).

## Which model

| View | Model | Why |
| --- | --- | --- |
| Side-on or oblique cameras (Caltrans, TRANSSET) | `yolo11s.pt` at 1280 | Highway F1 0.844; the ensemble adds nothing here and is 3× slower |
| Top-down drone (UAV-OBB) | `aerial-ensemble` | COCO `yolo11s` plus DOTA-trained `yolo11m-obb`; aerial F1 0.755 against 0.499 for `yolo11s` alone |

`--tile-size` adds overlapping crops. It was slower and less accurate than both
choices above, so leave it off.

## Commands

| Task | Command |
| --- | --- |
| Download Caltrans snapshots (new dated folder each run) | `python -m cv.download_california` |
| Download labeled datasets | `python -m cv.download_data` and `python -m cv.download_data --dataset transset` |
| Score a detector | `python -m cv.benchmark --model aerial-ensemble` (add `--dataset transset --limit 200` for highway images) |
| Annotate any images | `python -m cv.inspect_images --source <image-or-folder> --name <run>` |
| Annotate a video | `python -m cv.inspect_video --source <video> --name <run> --seconds 15` |
| Score the road-state rule | `python -m cv.evaluate_road_state --model aerial-ensemble` |
| Build the dashboard image library | `python -m cv.prepare_review` |
| Collect all runs into one page | `python -m cv.build_report`, then open `cv/outputs/index.html` |

Use the CV environment's Python (`.\.venv\Scripts\python.exe`). Outputs go to
`cv/outputs/<run>/`. A `--limit` run is saved as `...-firstN` so it never
overwrites a full run. Data, models and outputs are ignored by Git.

## Road-state rule

`road_state.py` is pure Python and tested without CV dependencies. Given vehicle
boxes and a road polygon, it counts vehicles whose nearest neighbour is within 1.5
vehicle lengths. 12 or more closely spaced vehicles is `dense_traffic` (signal
`blocked`), 3 or fewer is `light_traffic` (`clear`), and anything between is
`unclear` (`inconclusive`). A dark frame with few detections is `unclear`, because
missing detections at night are not evidence of an empty road.

`evaluate_road_state.py` applies the same rule to annotated boxes and to detector
boxes on the 27 hand-drawn road polygons in `road_rois.json`. This measures only
the error the detector adds. It then combines that table with an assumed link
between traffic and blockage (`ASSUMED_STATE_GIVEN_ROAD`) and prints
`decision_engine_likelihoods`. Paste those values into
`decision_model.drone.cv_only.likelihoods` in `config/port_scenario.json`.

## Image review library

`prepare_review.py` reads `california_review.json` (road polygons and provisional
AI-assisted visual notes), runs `yolo11s.pt`, keeps detections inside the road
area and writes `cv/outputs/review-cases/`. The backend serves that folder through
`/api/inspection-cases` without importing CV dependencies, and
`POST /api/inspection-review` appends each review to
`cv/outputs/inspection-reviews.jsonl`. Without the folder, the dashboard falls back
to simulated observation buttons.

Unavailable or insufficient-detail images allow only an inconclusive observation.
Applying an image is an illustration for the fictional scenario, not a report of
current conditions.

## Limits

- A still image shows vehicle spacing, not movement. A red-light queue and a
  blockage queue look the same.
- Detection scores are not blockage probabilities.
- Only 4 of the 27 evaluation frames show dense traffic and none shows a blocked
  road, so the dense-traffic row of the measured table is mostly smoothing.
- External images are illustrations. They do not document the fictional CA-47
  incident.

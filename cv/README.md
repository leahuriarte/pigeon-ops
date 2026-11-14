# Offline CV inspection

This offline component detects cars, buses and trucks in top-down drone video,
measures whether they move, and turns that into reviewable evidence. Nothing
here controls a drone or changes a dispatch decision on its own. It feeds the
dashboard in two ways:

1. **Drone inspection library (used live, committed).** Five top-down clips with
   a marked road region, annotated playback and a suggested observation. The
   dialog preselects the suggestion; the dispatcher confirms or overrides it, and
   the engine then uses its assumed 90% accuracy for reviewed footage.
2. **Measured CV-only evidence (shown for comparison).** The still-image
   vehicle-spacing rule (`assess_road`) is scored against 27 annotated aerial
   frames. Its error table becomes `decision_model.drone.cv_only` in the scenario
   config, and the dashboard shows what that CV alone would be worth. The motion
   rule (`assess_motion`) that suggests observations on the clips has no measured
   accuracy yet; five self-labelled clips are too few to estimate one.

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
| Side-on or oblique cameras (TRANSSET highway images) | `yolo11s.pt` at 1280 | Highway F1 0.844; the ensemble adds nothing here and is 3× slower |
| Top-down drone (UAV-OBB) | `aerial-ensemble` | COCO `yolo11s` plus DOTA-trained `yolo11m-obb`; aerial F1 0.755 against 0.499 for `yolo11s` alone |
| Low-altitude drone clips (review library) | `drone-ensemble` | `aerial-ensemble` plus a 640 px OBB pass; finds trucks several hundred pixels long that the 1024 px pass misses. Not benchmarked |

`--tile-size` adds overlapping crops. It was slower and less accurate than both
choices above, so leave it off.

## Commands

| Task | Command |
| --- | --- |
| Download labeled datasets | `python -m cv.download_data` and `python -m cv.download_data --dataset transset` |
| Score a detector | `python -m cv.benchmark --model aerial-ensemble` (add `--dataset transset --limit 200` for highway images) |
| Annotate any images | `python -m cv.inspect_images --source <image-or-folder> --name <run>` |
| Annotate a video | `python -m cv.inspect_video --source <video> --name <run> --seconds 15` |
| Score the road-state rule | `python -m cv.evaluate_road_state --model aerial-ensemble` |
| Build the drone inspection library | `python -m cv.prepare_review` (clips in `cv/clips/raw/`, see SOURCES.md) |
| Collect all runs into one page | `python -m cv.build_report`, then open `cv/outputs/index.html` |

Use the CV environment's Python (`.\.venv\Scripts\python.exe`). Outputs go to
`cv/outputs/<run>/`, except the committed library in `cv/review_library/`. A `--limit` run is saved as `...-firstN` so it never
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

## Drone inspection library

`prepare_review.py` reads `drone_review.json` (clip, start time, road polygon and
a provisional visual note for each case) and, for each clip:

1. Samples 4 seconds at 8 frames per second and detects vehicles with `drone-ensemble`.
2. Fits the camera's own motion between samples from background features
   (ECC alignment with vehicle boxes masked out) and moves the road polygon with it.
3. Finds each vehicle again a quarter-second later by template matching in the
   motion-corrected frame (`motion.py`). Its shift is its own speed. Pixels are
   converted to approximate km/h by taking the median box short side as a 2 m
   vehicle width.
4. Applies `road_state.assess_motion` to vehicles inside the road polygon:
   - **blocked** when at least one vehicle stands still (under 3 km/h) and at
     least 60% of those on the road are not moving (standing or crawling under
     10 km/h);
   - **clear** when at least two vehicles move faster than 10 km/h and they are
     at least 80% of those on the road;
   - **inconclusive** otherwise, including an empty road, which can also mean a
     closure upstream.
   Every count is the median over samples, so one bad frame cannot flip it.
5. Writes an annotated 960 px clip, a source frame, an annotated frame and
   `library.json` to `cv/review_library/` (about 1 MB in total).

The backend serves that folder through `/api/inspection-cases` without
importing CV dependencies, and `POST /api/inspection-review` appends each
review, with the CV suggestion and whether the dispatcher agreed, to
`cv/outputs/inspection-reviews.jsonl`.

The thresholds are hand-set, not fitted, and there is no labelled blockage
footage to score them against. The suggestion is a starting point for the
dispatcher, not evidence the engine trusts on its own.

## Limits

- A still image shows vehicle spacing, not movement. Video adds movement, but a
  red-light queue and a blockage queue both stand still; the motion rule is meant
  for highway segments.
- The detector misses some heavy vehicles at unusual angles (the blue flatbed in
  the first crash clip). A wreck that is not detected as a vehicle cannot count as
  standing traffic.
- Speeds assume a 2 m vehicle width and real-time playback. Stock footage may be
  slowed down, which is one reason the moving threshold is only 10 km/h.
- Detection scores are not blockage probabilities.
- Only 4 of the 27 evaluation frames show dense traffic and none shows a blocked
  road, so the dense-traffic row of the measured table is mostly smoothing.
- External clips are illustrations. They do not document the fictional CA-47
  incident.

# CV run results

Run date: November 14, 2026. Repository pulled through `4f20599` before setup.
The optional environment uses Python 3.11, Ultralytics 8.4.213 and CPU inference.
Package versions are recorded in `requirements-lock.txt`. No detector training
was performed in these baseline runs.

## Downloaded data

- UAV-OBB v4: 1,617 labeled aerial images and two supplementary videos.
- TRANSSET v5: 4,704 labeled North Carolina highway images.
- Caltrans: 25 California snapshots from SR-47, I-710, I-110, I-405 and SR-91;
  153 camera locations and 648 corridor closure-feed records.
- Both labeled dataset archives passed publisher checksum verification.

The California snapshot used for the report was retrieved November 14 at
11:17 AM Pacific. That is retrieval time, not a guaranteed image capture time.
Raw metadata and images are in `data/california/20261114T191704Z/`.
Closure records include planned and ended entries; 648 is not an active-closure count.

## Detector comparison

All runs used confidence 0.25, class-agnostic vehicle matching and IoU 0.5.
Rows marked * ran before a fix that merges one vehicle detected as two classes
(for example car and truck); their precision is slightly understated.

| Dataset and sample | Detector | Precision | Recall | F1 |
| --- | --- | --- | --- | --- |
| UAV-OBB validation, 218 images | YOLO11n, 640* | 86.0% | 6.7% | 0.125 |
| UAV-OBB validation, 218 images | YOLO11n, 1280* | 89.0% | 15.5% | 0.265 |
| UAV-OBB validation, 218 images | YOLO11n, 640 + overlapping crops | 80.9% | 24.1% | 0.371 |
| UAV-OBB validation, 218 images | YOLO11s, 1280 | 90.8% | 34.4% | 0.499 |
| UAV-OBB validation, 218 images | YOLO11s, 1280 + overlapping crops | 82.0% | 40.9% | 0.546 |
| UAV-OBB validation, 218 images | **aerial-ensemble** (YOLO11s + YOLO11m-OBB) | 89.5% | 65.2% | **0.755** |
| UAV-OBB test, 16 images | YOLO11s, 1280* | 88.9% | 31.1% | 0.461 |
| TRANSSET development sample, 200 images | **YOLO11s, 1280** | 79.0% | 90.5% | **0.844** |
| TRANSSET development sample, 200 images | aerial-ensemble | 78.4% | 90.5% | 0.840 |

Defaults depend on the camera view:

- **Top-down drone imagery: `aerial-ensemble`.** It combines COCO YOLO11s with the
  DOTA-trained YOLO11m-OBB and nearly doubles aerial recall (65% against 34%) at
  about 1.5 seconds per image on CPU.
- **Side-on and oblique cameras: YOLO11s at 1280.** On highway images the ensemble
  adds nothing (F1 0.840 against 0.844) and is three times slower (1.6 against
  0.55 seconds per image). The Caltrans review library uses this model.

Overlapping crops help YOLO11s recall but lose to the ensemble on both accuracy
and speed. Timings are approximate and machine-specific.

The aerial results remain weak: many vehicles are missed. The highway results
are more useful, but extra detections remain. A manual check of a false-positive
heavy highway image showed that the reference boxes covered road traffic while
parked background vehicles were unannotated. California examples also show a
false detection on a Caltrans logo. These findings motivate a road-area mask
and manual annotation review before using counts as decision evidence.

These are not official OBB mAP scores or blockage accuracy. UAV-OBB rotated boxes
were converted to enclosing rectangles. Highway frames can be correlated across
the deterministic image partitions. California snapshots have no reference labels.

## Outputs and interpretation

Open `outputs/index.html` for the combined report. The California report examined
25 images, analyzed 19 road scenes and flagged six provider-unavailable cards.
It found 71 vehicle detections across the 19 scenes; this is neither an accurate
fleet count nor a count of unique vehicles. Twelve highway examples and a
3.5-second annotated aerial video are also included.

Unreviewed observations remain `inconclusive`. These initial detector runs are
separate from the subsequent dashboard integration described below.

## Road-state rule and measured CV-only evidence

`road_state.py` labels a road polygon as dense traffic (12 or more vehicles
within 1.5 vehicle lengths of a neighbour), light traffic (3 or fewer) or unclear.
Dark frames with few detections are unclear. `evaluate_road_state.py` compares the
rule on detector boxes with the rule on annotated boxes for 27 hand-drawn road
polygons from UAV-OBB (`road_rois.json`, highest- and lowest-count frame per video).

With `aerial-ensemble`, the detector-based state agreed with the annotation-based
state on 17 of 27 frames. No dense-traffic frame came back as light traffic; misses
fell to unclear. Only 4 frames show dense traffic and none shows a blocked road, so
that row is mostly additive smoothing.

Combined with an assumed traffic link (a blocked road looks dense 75% of the time,
an open one 15%), automated CV alone flags 24% of blockages, says "clear" for 22%
of them, and raises a false alarm on 10% of open roads. In the disruption scenario
that evidence never changes the best action, so its information value is zero and
an inspection based on it alone is not worth its $165 cost. The dashboard shows
this next to the assumed 90% reviewed-imagery figure and the accuracy at which an
inspection pays off (80% in the disruption scenario).

## Completed review and dashboard integration

The prepared review library contains 19 California cases: 16 selected Caltrans
camera snapshots and three historical images from Caltrans and USGS. Road areas
were marked where visible. The Caltrans Regent's Slide photo shows debris on
SR-1, supported by a February 17, 2026 closure release; it is explicitly historical
and unrelated to the fictional CA-47 incident.

Visual notes and region polygons are provisional AI-assisted annotations, not
independent ground truth. Of 65 vehicle detections across the selected source
images, 33 fall inside the marked road areas and 32 are excluded. This measures
region filtering, not a validated accuracy improvement. The debris outline was
added by visual review; the vehicle model did not detect the debris.

The dashboard now displays each image, its marked road area, location, source,
date and limitations. Reviewers can switch to an unmarked source view and choose
an observation for the synthetic scenario. Unavailable and insufficient-detail
cases permit only an inconclusive signal. The review endpoint records the source
hash, scenario, observation, note and time in a local audit log.

Browser checks on November 14 verified all three flows in the updated disruption
scenario: prior 40%; clear illustration → 7% and keep course; historical debris
illustration → 86% and reroute; unavailable camera → 40%, inconclusive. These are
scenario-model results, not measured California road probabilities. Scenario
changes reset the selected evidence. The review dialog was also checked at
390 × 844 pixels without horizontal overflow.

Start the app with `python run.py`, then open `http://127.0.0.1:8000` and select
**Review inspection images**. See `cv/README.md` to regenerate the prepared library.

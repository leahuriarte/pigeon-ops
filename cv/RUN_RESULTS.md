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

## Drone inspection library (dashboard)

The dashboard library now uses five top-down Pexels drone clips instead of
side-on Caltrans cameras, because the demo's inspection is a drone flight.
`python -m cv.prepare_review` samples 4 seconds of each clip at 8 frames per
second with `drone-ensemble`, removes camera motion, measures vehicle speeds
and applies the motion rule to the marked road (`cv/README.md`).

| Case | Visual review | CV suggestion | Why |
| --- | --- | --- | --- |
| crash-lanes-blocked | wreck across lanes | blocked | crashed trailer standing at 0 km/h; one car crawling at about 7 km/h |
| crash-orbit | wreck across lanes | inconclusive | the detector does not recognise the jackknifed wreck, so the road looks empty |
| highway-free-flow | no obstruction | clear | 11 of 11 vehicles moving at 60–100 km/h |
| city-highway-flow | no obstruction | clear | 6 of 6 vehicles moving |
| rest-area-parked-trucks | no obstruction | clear | 12 moving on the motorway; 63 parked trucks excluded by the road region |

These are five hand-picked clips, not an accuracy measurement. A dense close-up
clip (`38774214`) was dropped: cars fill the frame and the camera-motion estimate
fails. Speeds in the rest-area clip read about 16 km/h, which suggests slowed
stock footage; the 10 km/h moving threshold still classifies it correctly.

Browser check on November 14: confirming the crash suggestion moves the
disruption scenario from 40% to 86% and the best option to the Anaheim St
reroute; overriding the inconclusive crash clip to blocked gives the same
result and is logged as an override; confirming the rest-area clip drops the
probability to 7% and the best option to keeping the current route.

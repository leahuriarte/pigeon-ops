# CV run results

Run date: November 14, 2026. Repository pulled through `4f20599` before setup.
The optional environment uses Python 3.11, Ultralytics 8.4.213 and CPU inference.
Package versions are recorded in `requirements-lock.txt`. No detector training
was performed in these baseline runs.

## Downloaded data

- UAV-OBB v4: 1,617 labeled aerial images and two supplementary videos.
- TRANSSET v5: 4,704 labeled North Carolina highway images.
- Both labeled dataset archives passed publisher checksum verification.

An earlier trial used 25 side-on Caltrans camera snapshots as the review library.
Their findings (a false detection on a camera logo, parked vehicles counted as
traffic) led to the road-area masks used now. That library and its downloader were
retired when the review step switched to top-down drone clips.

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
  0.55 seconds per image).

Overlapping crops help YOLO11s recall but lose to the ensemble on both accuracy
and speed. Timings are approximate and machine-specific.

The aerial results remain weak: many vehicles are missed. The highway results
are more useful, but extra detections remain. A manual check of a false-positive
heavy highway image showed that the reference boxes covered road traffic while
parked background vehicles were unannotated. These findings motivate a road-area mask
and manual annotation review before using counts as decision evidence.

These are not official OBB mAP scores or blockage accuracy. UAV-OBB rotated boxes
were converted to enclosing rectangles. Highway frames can be correlated across
the deterministic image partitions.

## Outputs and interpretation

Open `outputs/index.html` for the combined report (`python -m cv.build_report`).
Twelve highway examples and a 3.5-second annotated aerial video are also included.

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
that evidence never changes the best action, so waiting for it only adds cost: the
held truck's delay ($444 of expected loss) plus the $90 inspection fee gives a net
value of −$534. The dashboard shows this next to the assumed 90% reviewed-imagery
figure and the accuracy at which an inspection pays off (86% in the disruption
scenario). These figures use the inspection delay modelled as a held truck.

This measures the still-image vehicle-spacing rule. The motion rule that suggests
observations on the drone clips has not been measured.

## Drone inspection library (dashboard)

The dashboard library uses eight Pexels drone clips (seven top-down, one oblique),
because the demo's inspection is a drone flight. Each clip lists the check types
it can illustrate (`crash`, `congestion`, `environment`); the three clear-road
clips carry all three.
`python -m cv.prepare_review` samples 4 seconds of each clip at 8 frames per
second with `drone-ensemble`, removes camera motion, measures vehicle speeds
and applies the motion rule to the marked road (`cv/README.md`).

| Case | Visual review | CV suggestion | Why |
| --- | --- | --- | --- |
| crash-lanes-blocked | wreck across lanes | blocked | crashed trailer standing at 0 km/h; one car crawling at about 7 km/h |
| crash-orbit | wreck across lanes | inconclusive | the detector does not recognise the jackknifed wreck, so the road looks empty |
| terminal-truck-queue | standing truck queue | blocked | 16 of 16 trucks in the marked lanes standing at 0 km/h |
| highway-dense-slow | dense, slow traffic | clear | 10 of 10 vehicles moving at about 10–25 km/h, all above the 10 km/h moving threshold |
| flooded-lane | floodwater across the lane | inconclusive | no vehicles in view; the CV tracks vehicles and cannot see water |
| highway-free-flow | no obstruction | clear | 11 of 11 vehicles moving at 60–100 km/h |
| city-highway-flow | no obstruction | clear | 6 of 6 vehicles moving |
| rest-area-parked-trucks | no obstruction | clear | 12 moving on the motorway; 63 parked trucks excluded by the road region |

These are eight hand-picked clips, not an accuracy measurement. In the dense
close-up clip (`38774214`, highway-dense-slow) cars fill the frame and the
camera-motion estimate follows the traffic: the road region slid off the road,
speeds read 0–19 km/h, and the suggestion changed with the start time (blocked
from 0.5, 2.2 and 13 s, inconclusive from 6 and 10 s). The catalog therefore
marks that clip `"camera": "hover"`, which treats the camera as still; the
suggestion is then clear from both start times tried (0.5 and 6 s). The real
drift is about 1 km/h. Its "clear" is the rule working as written, not a
judgment that the traffic is light. The terminal clip is an oblique view, so
its single ground scale is rough, but standing trucks read 0 km/h at any scale.
A wide oblique highway queue clip (`13171071`) was left out: the detector finds
about 8 of several dozen trucks.
Speeds in the rest-area clip read about 16 km/h, which suggests slowed
stock footage; the 10 km/h moving threshold still classifies it correctly.

Browser check on November 14: confirming the crash suggestion moves the
disruption scenario from 40% to 86% and the best option to the Anaheim St
reroute; overriding the inconclusive crash clip to blocked gives the same
result and is logged as an override; confirming the rest-area clip drops the
probability to 7% and the best option to keeping the current route.

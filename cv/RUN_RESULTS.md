# Initial CV run results

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

| Dataset and sample | Detector | Precision | Recall | F1 |
| --- | --- | --- | --- | --- |
| UAV-OBB validation, 218 images | YOLO11n, 640 | 86.0% | 6.7% | 0.125 |
| UAV-OBB validation, 218 images | YOLO11n, 1280 | 89.0% | 15.5% | 0.265 |
| UAV-OBB validation, 218 images | YOLO11n, 640 + overlapping crops | 80.9% | 24.1% | 0.371 |
| UAV-OBB validation, 218 images | YOLO11s, 1280 | 88.4% | 34.4% | 0.495 |
| UAV-OBB test, 16 images | YOLO11s, 1280 | 88.9% | 31.1% | 0.461 |
| TRANSSET development sample, 200 images | YOLO11s, 1280 | 73.7% | 90.5% | 0.813 |

Selected default: **YOLO11s at 1280 without overlapping crops**. It had the best
aerial validation F1 among the tested configurations. CPU processing took about
0.55 seconds per aerial validation image and 1.35 seconds per highway sample,
including input/output work; timings are approximate and machine-specific.

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

Unreviewed observations remain `inconclusive`. The inspection tools are not
automatically connected to the logistics dashboard. Separate road-state and
engine changes were appearing concurrently and are outside these detector results.

## Next work

1. Review 10–20 California images and mark the intended carriageway, excluding
   camera graphics, parking areas and opposing traffic where appropriate.
2. Annotate visible vehicles and evidence quality. A separate road-condition
   evaluation needs independently labeled obstruction examples; the downloaded
   vehicle annotations do not establish blockage.
3. Integrate the annotated image, source time and reviewer controls into the UI.
   Keep simulated incident assumptions separate from real camera snapshots.

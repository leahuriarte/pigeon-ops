# CV data sources and scope

The logistics records in `config/port_scenario.json` remain fictional. External
images are separate visual evidence examples. Do not imply they document the
fictional incident, and do not turn detection confidence into inspection accuracy.

## TRANSSET highway images

- **Creators:** Tewodros Syum Gebre, Quincy Blackston and Leila Hashemi Beni.
- **Pinned release:** <https://doi.org/10.6084/m9.figshare.26082217.v5>.
- **License:** CC BY 4.0, as stated by the data repository and its README.
- **Downloaded archive:** 741,382,444 bytes; publisher MD5
  `3afac47a3a4f9f930f43a109a37fc78f`.
- **Contents:** 4,704 highway images and 21,661 vehicle annotations, collected at
  I-40 and I-440 in North Carolina. This is not California imagery.
- **Evaluation:** use the actual COCO category IDs and boxes in `coco.json`.
  The README's numeric category mapping differs from the COCO file; class-agnostic
  vehicle evaluation avoids assuming the mappings are interchangeable.

No predefined splits are supplied. The runner uses seed 42 to shuffle filenames
into an 80/20 partition. This avoids exact image reuse between partitions but
does not prevent adjacent frames from crossing partitions. Results are descriptive
checks, not evidence of independent-scene generalization.

## UAV-OBB aerial imagery

- **Creators:** Israr Ahmad, Shang Fengjun, Kiran Bibi and Muhammad Slaman Pathan.
- **Release:** <https://data.mendeley.com/datasets/6snrjwcpkh/4>.
- **License:** CC BY 4.0.
- **Downloaded archive:** 601,733,549 bytes; publisher SHA-256
  `bf65b0d6a00bc1a320002012146bf44913e2b3f97b9e6acbd7954775d61f69e2`.
- **Contents:** 1,617 annotated aerial images from Chongqing and Wuhan, China;
  1,383 training, 218 validation and 16 test images. The downloaded archive contains
  two supplementary MP4 files; the publisher's description mentions additional
  sequences, so the archive inventory is the operational source of truth.
- **Evaluation:** collapse bus, car, taxi, truck and other_vehicle to motor
  vehicles; exclude bike. Convert oriented boxes to axis-aligned envelopes.
  Match predictions one-to-one by descending confidence at IoU ≥ 0.5.

These metrics are not official oriented-box mAP. Neither dataset labels road
blockage, congestion severity, travel speed or road safety. No model was trained
on these data during setup.

## Drone inspection clips (dashboard library)

The dashboard's inspection library (`cv/review_library/`, built by
`python -m cv.prepare_review` from `cv/drone_review.json`) uses drone footage,
because the demo's inspection is a drone flight over the road. Six clips are
top-down; the container-terminal queue is an oblique view.
Raw clips go in `cv/clips/raw/` (ignored by Git); the committed library holds a
4-second annotated excerpt, one source frame and one annotated frame per clip.
All are 1920×1080 Pexels downloads under the [Pexels license](https://www.pexels.com/license/),
fetched November 14, 2026. The crash, clear-road and rest-area clips were not
filmed near the Port of Los Angeles or Long Beach. The sources of the two
heavy-traffic clips (Kelly) do not state where they were filmed. Capture dates
are not published.

| File | Library case | Check types | Creator | Source |
| --- | --- | --- | --- | --- |
| `blocked_crash_topdown_12753260.mp4` | crash-lanes-blocked | crash | K | https://www.pexels.com/video/aerial-shot-of-a-car-accident-on-a-highway-12753260/ |
| `blocked_crash_topdown_12901485.mp4` | crash-orbit | crash | K | https://www.pexels.com/video/accident-on-highway-12901485/ |
| `blocked_port_queue_13742716.mp4` | terminal-truck-queue | congestion | Kelly | https://www.pexels.com/video/trucks-in-port-13742716/ |
| `ambiguous_dense_moving_38774214.mp4` | highway-dense-slow | congestion | Kelly | https://www.pexels.com/video/aerial-view-of-vehicles-on-highway-traffic-38774214/ |
| `clear_topdown_13376785.mp4` | highway-free-flow | crash, congestion, environment | tal molcho | https://www.pexels.com/video/traffic-on-highway-13376785/ |
| `clear_topdown_31570754.mp4` | city-highway-flow | crash, congestion, environment | Kenan Turguç | https://www.pexels.com/video/aerial-view-of-city-highway-traffic-31570754/ |
| `ambiguous_rest_area_15978611.mp4` | rest-area-parked-trucks | crash, congestion, environment | DeLuca G | https://www.pexels.com/video/aerial-view-of-a-highway-with-many-trucks-15978611/ |

Download each clip's 1920×1080 file from its page and save it under the file
name above. A clear road is a valid answer to any check, so the three clear-road
clips carry every check type. One earlier candidate,
`blocked_highway_queue_13171071.mp4` (Jozef Papp,
https://www.pexels.com/video/traffic-on-highway-13171071/), is not used: it is a
1280×720 wide oblique view with the horizon in frame, the trucks are 10 to 40
pixels long, and the detector finds about 8 of several dozen.

Road regions and visual notes in `drone_review.json` are AI-assisted and
provisional. No independent annotator has validated them, and no blockage
accuracy is reported from seven hand-picked clips.

## Pretrained models

- `yolo11n.pt`, `yolo11s.pt`: Ultralytics YOLO11 trained on COCO (car, bus, truck).
  Best on side-on and oblique views such as TRANSSET highway images.
- `yolo11s-obb.pt`, `yolo11m-obb.pt`: Ultralytics YOLO11-OBB trained on DOTA v1
  aerial imagery (small vehicle, large vehicle). Used in `aerial-ensemble` for
  top-down drone views.
- All four download from the official Ultralytics release assets on first use and
  are distributed under AGPL-3.0. No model was fine-tuned.
- Community VisDrone and Geo-trax checkpoints on Hugging Face fit the drone domain
  better but were not used: they are unvetted pickled `.pt` files, and VisDrone
  weights inherit a research-only licence.

## Alternatives considered

[VisDrone](https://github.com/VisDrone/VisDrone-Dataset) is a strong aerial
detection benchmark, but the selected releases offer clearer publisher-stated
reuse terms for a small demo. [pNEUMA Vision](https://zenodo.org/records/7426506)
provides aerial imagery and trajectories, but individual archives are several GB.
The selected datasets provide both highway relevance and annotated aerial checks
with smaller downloads.

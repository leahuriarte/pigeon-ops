# Offline CV inspection

This optional component detects cars, buses and trucks in local images or video.
It produces annotated evidence and JSON for human review. It does not classify a
road as blocked or safe, control a drone, or change the logistics engine's assumed
inspection accuracy. Start with **California highway images** for the presentation;
use the annotated datasets to check detector quality.

## Setup on Windows

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r cv/requirements.txt
```

The environment and two pretrained models are already installed on this machine.
The model files download automatically on first use. Inference is configured for
CPU; a GPU is not required. `requirements-lock.txt` records the tested environment.
The main logistics demo still runs without these optional dependencies.

## Download data

```powershell
.\.venv\Scripts\python.exe -m cv.download_california
.\.venv\Scripts\python.exe -m cv.download_data
.\.venv\Scripts\python.exe -m cv.download_data --dataset transset
```

The downloads are already present under `cv/data/`. The first command creates a
new dated snapshot on every run. Dataset archives are verified against publisher
checksums. Read [SOURCES.md](SOURCES.md) for provenance, reuse terms and limitations.
Data, models, environment and generated outputs are ignored by Git.

## Inspect images

Use a single image or a folder. Replace the source path with the dated California
folder printed by the downloader, or any local highway image:

```powershell
.\.venv\Scripts\python.exe -m cv.inspect_images --source "C:\path\to\highway.jpg" --name my-inspection
```

For the downloaded highway dataset:

```powershell
.\.venv\Scripts\python.exe -m cv.inspect_images --source cv/data/TRANSSET/TRANSSET_v2/images --name highway-images --limit 12
```

Open `cv/outputs/<name>/report.html`. It includes annotated images, a contact sheet
and provenance. `evidence.json` includes pixel-coordinate boxes, detector scores,
per-image vehicle counts and camera coordinates/timestamps when available.
California unavailable-camera cards are flagged for source review using a simple
white-image heuristic; this is not an availability classifier or road-state model.

Run `python -m cv.build_report` with the CV environment's Python to collect
completed runs into `cv/outputs/index.html`.

Defaults are YOLO11s, image size 1280 and confidence threshold 0.25. `--limit 0`
analyzes every image. `--tile-size 640` adds overlapping crops, but is slower and
did not outperform the selected larger model on the initial aerial validation run.

## Run labeled evaluations

```powershell
.\.venv\Scripts\python.exe -m cv.benchmark --model yolo11s.pt --imgsz 1280
.\.venv\Scripts\python.exe -m cv.benchmark --dataset transset --model yolo11s.pt --imgsz 1280 --limit 200
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Each evaluation saves `summary.json`, `per-image.csv`, preview images and an HTML
report. Precision measures how many detections match references; recall measures
how many reference vehicles were found. Neither is road-blockage accuracy.

## Optional video inspection

```powershell
.\.venv\Scripts\python.exe -m cv.inspect_video --source cv/data/UAV-OBB/test_video.mp4 --name aerial-video --seconds 15
```

This samples two frames per second by default and produces an annotated H.264
MP4, evidence JSON and an HTML report. Counts are per frame, not unique tracks.

## Hand off to the engine and UI

Every unreviewed result deliberately returns `label: inconclusive` and
`review_status: needs_human_review`. The UI teammate should display the image,
source timestamp and explanation before asking the reviewer to confirm the road
state. Only then pass `blocked`, `clear` or `inconclusive` into the existing
`/api/analysis?scenario=disruption&observation=...` endpoint. Detection confidence
must not replace the engine's configured observation accuracy.

For a still image, use wording such as “vehicle visible in the inspected lane.”
Do not infer speed, a persistent queue or a confirmed obstruction from vehicle
count alone. Keep synthetic incident assumptions visible in the demo.

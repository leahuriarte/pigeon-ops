@AGENTS.md

## Architecture notes

- Flow: `config/port_scenario.json` → `backend/scenario.py` (variant overlay) → `backend/outcomes.py` (action outcomes from route plans) → `backend/decision_engine.py` (ranking, Bayes, EVOI, sensitivity) → `backend/server.py` → `frontend/`.
- `decision_engine.information_value` takes an optional P(signal | road state) table. Without one it uses `drone.accuracy`, an assumed figure for a dispatcher reviewing imagery. `drone.cv_only.likelihoods` is the measured table for automated CV alone, reported as `evoi.cv_only` next to `evoi.break_even_accuracy`.
- `cv/` is offline and has its own virtual environment (`pip install -r cv/requirements.txt`). Run its scripts as modules from the repo root, for example `python -m cv.evaluate_road_state --model aerial-ensemble`. `aerial-ensemble` combines COCO `yolo11s.pt` with the DOTA aerial model `yolo11m-obb.pt` and is the best detector so far.
- `cv/road_state.py` is pure Python and tested without CV dependencies. It counts closely spaced vehicles inside a hand-drawn road polygon (`cv/road_rois.json`). Its `road_likelihoods` turns the measured confusion table into the `cv_only` table in config.
- The inspection dialog (`frontend/inspection.js`, `backend/inspection.py`) reviews prepared California images from `cv/outputs/review-cases/`, built by `python -m cv.prepare_review`. Without that folder the page falls back to the simulated feed buttons.

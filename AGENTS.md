# Repository Guidelines

## Project Structure & Module Organization

PigeonOps is a synthetic logistics decision-support demo. `backend/decision_engine.py` handles loss scoring, action ranking, Bayesian updates, and information value. `backend/scenario.py` loads scenario data; `backend/server.py` serves the JSON API and static files. `run.py` is the entry point.

`frontend/` contains the HTML dashboard, JavaScript interactions, and CSS, including ArcGIS styling. `config/port_scenario.json` defines shipments, routes, weights, and action outcomes. Automated checks live in `tests/test_decision_engine.py`. Keep decision calculations in the backend and scenario-specific values in configuration.

`cv/` contains optional offline image/video inspection and evaluation tools. Follow `cv/README.md` for its separate Python environment and data downloads.

## Build, Test, and Development Commands

Run commands from the repository root with Python 3.9 or newer. No package installation or build step is required.

- `python run.py`: start the demo at `http://127.0.0.1:8000`.
- `python -m unittest discover -s tests -v`: run the automated test suite.
- `python -m unittest discover -v`: discover tests from the repository root.

Use `python3` if that is your platform's Python executable. After frontend changes, check the dashboard in a browser, exercise simulated inspection results, and verify the schematic fallback when map resources are unavailable.

## Coding Style & Naming Conventions

Follow existing file conventions: four-space indentation and `snake_case` functions in Python; two-space indentation, `camelCase`, and semicolons in JavaScript. Python classes use `PascalCase`; constants use `UPPER_SNAKE_CASE`. Preserve existing JSON keys and use descriptive CSS classes. No formatter or linter is configured; avoid unrelated reformatting and keep dependencies minimal.

## Testing Guidelines

Use standard-library `unittest`, with files named `test_*.py` and methods named `test_*`. Add focused regression cases when changing ranking, probability updates, or information-value calculations. Keep tests deterministic with synthetic scenarios. No coverage threshold is configured; run the full suite before submitting changes.

## Commit & Pull Request Guidelines

The short Git history uses plain descriptive subjects, such as `Integrate ArcGIS port map`; no formal commit convention is established. Prefer concise, imperative subjects. PRs should describe the behavior changed, explain validation, and link relevant issues when available. Include screenshots for dashboard changes and identify changes to model assumptions or scenario values.

## Demo Boundaries & Configuration

Use synthetic data and keep recommendations advisory. Do not add real drone commands or automatic dispatch. Preserve the offline map fallback. The current map requires no API key; never commit credentials if adding external services.

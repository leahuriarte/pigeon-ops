"""Collect completed local CV runs into one review page and a results manifest."""

import html
import json
from datetime import datetime, timezone

from cv.common import ROOT


def main():
    output = ROOT / "outputs"
    benchmarks = []
    for path in sorted(output.glob("*/summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        if "precision" in summary and "recall" in summary:
            benchmarks.append({**summary, "report": f"{path.parent.name}/report.html"})
    # Compare only full aerial validation runs; quick --limit sweeps cover fewer images.
    aerial = [item for item in benchmarks if item["dataset"].startswith("UAV-OBB") and item["split"] == "valid"]
    full_size = max((item["images"] for item in aerial), default=0)
    best = max((item for item in aerial if item["images"] == full_size), key=lambda item: item["f1"], default=None)
    library_file = ROOT / "review_library" / "library.json"
    clips = json.loads(library_file.read_text(encoding="utf-8"))["cases"] if library_file.is_file() else []
    highway_images = len(list((ROOT / "data" / "TRANSSET" / "TRANSSET_v2" / "images").glob("*.jpg")))
    aerial_images = sum(len(list((ROOT / "data" / "UAV-OBB" / split / "images").glob("*.jpg"))) for split in ("train", "valid", "test"))
    results = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selected_default": {key: best.get(key) for key in ("model", "image_size", "tile_size", "f1")} if best else None,
        "selection_reason": "Highest F1 on the full UAV-OBB validation split.",
        "benchmarks": benchmarks,
        "downloaded_images": {"transset": highway_images, "uav_obb": aerial_images},
        "drone_review_clips": len(clips),
        "limitations": ["Vehicle detection metrics are not road-blockage accuracy.",
                        "Highway data are North Carolina imagery; aerial data are China imagery.",
                        "The motion rule on the drone clips has no measured accuracy.",
                        "Vehicle count alone does not establish congestion, movement, blockage or road safety."],
    }
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    rows = "".join(
        f'<tr><td>{html.escape(item["dataset"])} / {item["split"]}</td><td>{html.escape(item["model"])} / {item["image_size"]}'
        f'{" + tiles" if item.get("tile_size") else ""}</td><td>{item["images"]}</td>'
        f'<td>{item["precision"]:.1%}</td><td>{item["recall"]:.1%}</td><td>{item["f1"]:.3f}</td>'
        f'<td>{item["seconds_per_image"]:.2f}s</td><td><a href="{item["report"]}">Open</a></td></tr>'
        for item in benchmarks
    )
    report = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PigeonOps CV results</title>
<style>body{{margin:0;background:#f4f7f6;color:#102a35;font:16px/1.55 system-ui}}main{{max-width:1180px;margin:auto;padding:32px 20px}}h1{{font-size:36px;margin-bottom:5px}}h2{{margin-top:35px}}a{{color:#087f75}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin:24px 0}}.card{{background:white;border:1px solid #dce4e6;border-radius:12px;padding:20px}}.card strong{{display:block;font-size:28px}}.notice{{background:#fff4dd;border-left:4px solid #e89116;padding:16px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:12px 8px;border-bottom:1px solid #dce4e6}}.scroll{{overflow:auto}}.muted{{color:#61757d}}</style>
<main><p class="muted">PIGEONOPS / COMPUTER VISION</p><h1>Detector results</h1><p>Measured detector runs on labeled data. See cv/RUN_RESULTS.md for interpretation.</p>
<div class="cards"><div class="card"><strong>{highway_images + aerial_images:,} labeled images</strong>{highway_images:,} highway images from North Carolina and {aerial_images:,} aerial images from China.</div><div class="card"><strong>{html.escape(best["model"]) if best else "No full run"}</strong>Best F1 on the aerial validation split{f" ({best['f1']:.2f})" if best else ""}. Pretrained models only; no training was performed.</div><div class="card"><strong>{len(clips)} drone clips</strong>In the dashboard review library (cv/review_library).</div></div>
<p class="notice">Vehicle boxes are evidence for review. These runs do not establish road blockage or safety, and the external footage is separate from the fictional demo incident.</p>
<h2>Measured detection results</h2><p>Precision: share of predictions matching a reference box. Recall: share of reference vehicles found. Matching uses IoU ≥ 0.5. Aerial oriented annotations are converted to enclosing rectangles; these are not official mAP scores. Highway frames may be correlated.</p>
<div class="scroll"><table><thead><tr><th>Dataset</th><th>Configuration</th><th>Images</th><th>Precision</th><th>Recall</th><th>F1</th><th>Approx. CPU time/image</th><th>Report</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="muted">Sources: <a href="https://doi.org/10.6084/m9.figshare.26082217.v5">TRANSSET</a>, <a href="https://data.mendeley.com/datasets/6snrjwcpkh/4">UAV-OBB</a>. Detailed attribution and terms are in cv/SOURCES.md. <a href="results.json">Machine-readable run comparison</a>.</p></main></html>'''
    (output / "index.html").write_text(report, encoding="utf-8")
    print(f"Review page ready: {output / 'index.html'}")


if __name__ == "__main__":
    main()

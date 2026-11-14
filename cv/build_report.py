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
    camera_file = output / "california-highways" / "evidence.json"
    california = json.loads(camera_file.read_text(encoding="utf-8")) if camera_file.is_file() else {}
    highway_images = len(list((ROOT / "data" / "TRANSSET" / "TRANSSET_v2" / "images").glob("*.jpg")))
    aerial_images = sum(len(list((ROOT / "data" / "UAV-OBB" / split / "images").glob("*.jpg"))) for split in ("train", "valid", "test"))
    results = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selected_default": {"model": "yolo11s.pt", "image_size": 1280, "confidence": 0.25, "tile_size": 0, "device": "cpu"},
        "selection_reason": "Best F1 among four aerial validation configurations; overlapping crops were slower and less accurate than the selected larger model.",
        "benchmarks": benchmarks,
        "downloaded_images": {"transset": highway_images, "uav_obb": aerial_images},
        "california": {key: california.get(key) for key in ["images_examined", "images_analyzed", "images_flagged_for_source_review", "dataset_url", "created_at"]},
        "limitations": ["Vehicle detection metrics are not road-blockage accuracy.",
                        "Highway data are North Carolina imagery; aerial data are China imagery.",
                        "California snapshots are unlabeled roadside CCTV; accuracy is not measured on them.",
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
<style>body{{margin:0;background:#f4f7f6;color:#102a35;font:16px/1.55 system-ui}}main{{max-width:1180px;margin:auto;padding:32px 20px}}h1{{font-size:36px;margin-bottom:5px}}h2{{margin-top:35px}}a{{color:#087f75}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin:24px 0}}.card,section{{background:white;border:1px solid #dce4e6;border-radius:12px;padding:20px}}.card strong{{display:block;font-size:28px}}.notice{{background:#fff4dd;border-left:4px solid #e89116;padding:16px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:12px 8px;border-bottom:1px solid #dce4e6}}.scroll{{overflow:auto}}img{{width:100%;max-width:640px;border-radius:10px}}.muted{{color:#61757d}}</style>
<main><p class="muted">PIGEONOPS / COMPUTER VISION</p><h1>Highway image inspection results</h1><p>Local setup, downloaded sources, measured detector results and reviewable examples.</p>
<div class="cards"><div class="card"><strong>{california.get('images_examined', 0)} California snapshots</strong>{california.get('images_analyzed', 0)} analyzed; {california.get('images_flagged_for_source_review', 0)} flagged as possible unavailable-camera cards.</div><div class="card"><strong>{highway_images + aerial_images:,} labeled images</strong>{highway_images:,} highway images from North Carolina and {aerial_images:,} aerial images from China.</div><div class="card"><strong>YOLO11s at 1280</strong>Selected CPU default. No model training was performed in these baseline runs.</div></div>
<p class="notice">Vehicle boxes are evidence for review. These runs do not establish road blockage or safety. California camera snapshots are separate from the fictional demo incident.</p>
<section><h2>Open the evidence</h2><ul><li><a href="california-highways/report.html">California highway images, camera coordinates and source times</a></li><li><a href="highway-images/report.html">Twelve annotated highway examples</a></li><li><a href="aerial-video/report.html">Optional short aerial video demonstration</a></li><li><a href="results.json">Machine-readable run comparison</a></li></ul><p>California locations include SR-47, I-710, I-110, I-405 and SR-91. The dated data folder also contains a camera GeoJSON and closure-feed records.</p></section>
<h2>Measured detection results</h2><p>Precision: share of predictions matching a reference box. Recall: share of reference vehicles found. Matching uses IoU ≥ 0.5. Aerial oriented annotations are converted to enclosing rectangles; these are not official mAP scores. Highway frames may be correlated.</p>
<div class="scroll"><table><thead><tr><th>Dataset</th><th>Configuration</th><th>Images</th><th>Precision</th><th>Recall</th><th>F1</th><th>Approx. CPU time/image</th><th>Report</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Recommended next work</h2><ol><li>Review a small set of California images and mark the road area of interest. Separate lane traffic from parking, construction sites and opposite carriageways.</li><li>Record visible evidence and uncertainty. Include an unavailable or ambiguous image in the presentation; zero detections must not imply a clear road.</li><li>Have the UI display the image, provenance and review controls. Pass a human-confirmed observation to the engine; keep its inspection accuracy assumption explicit.</li></ol>
<p class="muted">Sources: <a href="https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm">Caltrans</a>, <a href="https://doi.org/10.6084/m9.figshare.26082217.v5">TRANSSET</a>, <a href="https://data.mendeley.com/datasets/6snrjwcpkh/4">UAV-OBB</a>. Detailed attribution and terms are in cv/SOURCES.md.</p></main></html>'''
    (output / "index.html").write_text(report, encoding="utf-8")
    print(f"Review page ready: {output / 'index.html'}")


if __name__ == "__main__":
    main()

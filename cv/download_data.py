"""Download and verify the CC BY 4.0 UAV-OBB v4 or TRANSSET v5 release."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
API = "https://data.mendeley.com/public-api/datasets/6snrjwcpkh/files?folder_id=root&version=4&$start=0&$limit=1000"
SOURCE = "https://data.mendeley.com/datasets/6snrjwcpkh/4"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["uav-obb", "transset"], default="uav-obb")
    args = parser.parse_args()
    target = DATA if args.dataset == "uav-obb" else DATA / "TRANSSET"
    target.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "PigeonOps/0.1 (educational dataset download)", "Accept": "application/vnd.mendeley-public-dataset.1+json"}
    api = API if args.dataset == "uav-obb" else "https://api.figshare.com/v2/articles/26082217/versions/5"
    request = Request(api, headers=headers)
    with urlopen(request, timeout=60) as response:
        files = json.load(response)
    if args.dataset == "transset":
        files = [{"filename": item["name"], "content_details": {
            "sha256_hash": item["computed_md5"], "size": item["size"], "download_url": item["download_url"],
        }} for item in files["files"] if item["name"].endswith(".zip")]
    if isinstance(files, dict):
        files = [files]
    for item in files:
        details = item["content_details"]
        archive = target / Path(item["filename"]).name
        expected = details["sha256_hash"]
        algorithm = "sha256" if args.dataset == "uav-obb" else "md5"
        digest = hashlib.new(algorithm)
        if archive.exists():
            with archive.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        if not archive.exists() or digest.hexdigest() != expected:
            print(f"Downloading {archive.name}: {details['size'] / 1e6:.0f} MB", flush=True)
            digest = hashlib.new(algorithm)
            downloaded = 0
            milestone = 50_000_000
            download = Request(details["download_url"], headers={"User-Agent": headers["User-Agent"]})
            with urlopen(download, timeout=120) as response, archive.open("wb") as handle:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if downloaded >= milestone:
                        print(f"  {downloaded / 1e6:.0f} MB", flush=True)
                        milestone += 50_000_000
        if digest.hexdigest() != expected:
            raise ValueError(f"Checksum mismatch: {archive}")
        print(f"{algorithm.upper()} verified; extracting", flush=True)
        with ZipFile(archive) as zipped:
            for member in zipped.infolist():
                destination = (target / member.filename).resolve()
                if not destination.is_relative_to(target.resolve()):
                    raise ValueError("Archive contains a path outside the data folder")
            zipped.extractall(target)
        manifest = {
            "dataset": "UAV-OBB", "version": 4, "source": SOURCE,
            "doi": "10.17632/6snrjwcpkh.4", "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "creators": ["Israr Ahmad", "Shang Fengjun", "Kiran Bibi", "Muhammad Slaman Pathan"],
            "sha256": expected, "archive": archive.name,
            "download_url": details["download_url"],
            "geography": "Chongqing and Wuhan, China; illustrative footage, not Port of LA observations",
            "limitations": "Vehicle annotations only; no confirmed road-blockage or road-safety labels.",
        }
        if args.dataset == "transset":
            manifest.update({"dataset": "TRANSSET", "version": 5,
                "source": "https://doi.org/10.6084/m9.figshare.26082217.v5",
                "doi": "10.6084/m9.figshare.26082217.v5",
                "creators": ["Tewodros Syum Gebre", "Quincy Blackston", "Leila Hashemi Beni"],
                "geography": "I-40 and I-440, North Carolina, USA; not Port of LA observations",
                "checksum_algorithm": algorithm, "checksum": expected})
            manifest.pop("sha256")
        (target / "source.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Dataset ready: {target}", flush=True)


if __name__ == "__main__":
    main()

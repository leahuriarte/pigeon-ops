# CV data sources and scope

The logistics records in `config/port_scenario.json` remain fictional. External
images are separate visual evidence examples. Do not imply they document the
fictional incident, and do not turn detection confidence into inspection accuracy.

## California highway context

- **Publisher:** California Department of Transportation (Caltrans).
- **Camera feed:** <https://cwwp2.dot.ca.gov/data/d7/cctv/cctvStatusD07.json>.
- **Documentation:** <https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm>.
- **Lane closures:** <https://cwwp2.dot.ca.gov/lane-closure.html>.
- **Terms:** <https://dot.ca.gov/conditions-of-use>.
- **Selection:** District 7, SR-47, I-710, I-110, I-405 and SR-91;
  latitude 33.65–34.05 and longitude −118.55–−117.95. Download up to five
  in-service cameras per route, nearest the port-area reference point.
- **Files:** dated directories under `cv/data/california/`, containing original
  camera metadata, a filtered camera GeoJSON, individual JPEGs, checksums,
  retrieval times and lane-closure records.

These are roadside cameras, not drone images. Some cameras marked in service
return a “Temporarily Unavailable” card. Camera-record timestamps describe metadata
updates, not image capture time. Keep the original image overlay visible. Camera
images are not annotated evaluation data. Lane-closure feeds also contain planned,
ended and cancelled records; check their dates and status flags before use.

Additional California references: [QuickMap](https://quickmap.dot.ca.gov/),
[reported highway conditions](https://roads.dot.ca.gov/),
[truck access](https://dot.ca.gov/programs/traffic-operations/legal-truck-access/truck-network-map),
and [traffic volumes](https://dot.ca.gov/programs/traffic-operations/census).
The CWWP travel-time page does not list District 7; do not assume a D7 travel-time
feed exists or infer measured speed from one image.

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

## Alternatives considered

## Historical California review cases

The review library adds three attributed historical examples:

- [Regent's Slide, Caltrans publication February 17, 2026](https://dot.ca.gov/caltrans-near-me/district-5/d5-news/d5-news-2-17-2026): rocks and soil visibly cover SR-1; the accompanying release reported a closure. Exact photo capture time is unspecified. Caltrans Conditions of Use apply.
- [Mud Creek aerial image, July 7, 2017](https://www.usgs.gov/media/images/big-sur-landslide-july-7-2017): Bob Van Wagenen / USGS, public domain. Landslide and roadworks are visible; temporary-route access remains inconclusive from this photo.
- [Big Sur coast, July 12, 2017](https://www.usgs.gov/media/images/big-sur-coast): Shawn Harrison / USGS, public domain. No obstruction is visible in the photographed road segment; this does not establish current road access or safety.

The source files are downloaded to `cv/data/california-hazards/`. The preparer
records image hashes, date descriptions and credits in the generated library.
Road polygons and visual notes in `california_review.json` were produced through
AI-assisted visual review and remain provisional. No independent annotator has
validated these labels, and no blockage-accuracy score is reported from them.

## Alternatives considered

[VisDrone](https://github.com/VisDrone/VisDrone-Dataset) is a strong aerial
detection benchmark, but the selected releases offer clearer publisher-stated
reuse terms for a small demo. [pNEUMA Vision](https://zenodo.org/records/7426506)
provides aerial imagery and trajectories, but individual archives are several GB.
The selected datasets provide both highway relevance and annotated aerial checks
with smaller downloads.

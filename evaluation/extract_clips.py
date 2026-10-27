"""Copy the sampled clips listed in clips.txt out of AI Hub zips.

Video zips (VS1.zip ... VS20.zip) give <clip>.mp4, label zips (VL1.zip ... VL20.zip)
give the matching <clip>.json with per-frame person keypoints.

usage: python extract_clips.py <output folder> <zip> [<zip> ...]
Safe to re-run: files already copied with the right size are skipped.
"""
import shutil
import sys
import zipfile
from pathlib import Path

CLIPS = Path(__file__).parent / "clips.txt"

wanted = {}  # clip file name -> zip it lives in
for line in CLIPS.read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#"):
        _, zip_name, clip = line.split("\t")
        wanted[clip] = zip_name

out_dir = Path(sys.argv[1])
out_dir.mkdir(parents=True, exist_ok=True)
for zp in map(Path, sys.argv[2:]):
    video_zip = "VS" + zp.name[2:]  # VL3.zip holds the labels for the clips in VS3.zip
    suffix = ".json" if zp.name.startswith("VL") else ".mp4"
    expected = {clip[:-4] + suffix for clip, z in wanted.items() if z == video_zip}
    assert expected, f"{zp.name} is not referenced in clips.txt"
    found = set()
    with zipfile.ZipFile(zp) as z:
        for info in z.infolist():
            clip = info.filename.rsplit("/", 1)[-1]  # member paths are Korean folders; the clip name is ASCII
            if clip not in expected:
                continue
            dest = out_dir / clip
            if not (dest.exists() and dest.stat().st_size == info.file_size):
                with z.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            found.add(clip)
    missing = expected - found
    assert not missing, f"{zp.name}: not found {sorted(missing)}"
    print(f"{zp.name}: {len(found)} files")

videos = sum((out_dir / clip).exists() for clip in wanted)
labels = sum((out_dir / (clip[:-4] + ".json")).exists() for clip in wanted)
print(f"{videos}/{len(wanted)} videos, {labels}/{len(wanted)} labels in {out_dir}")

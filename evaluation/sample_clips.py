"""Draw the evaluation clips from AI Hub dataset 515 (validation split).

Per action: 5 distinct takes, one camera (A or B) per take, fixed seed.
Only file names inside the label zips (VL1.zip ... VL20.zip) are read,
so the sample is fixed before any video is downloaded.

usage: python sample_clips.py <folder containing VL*.zip>
"""
import platform
import random
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

SEED = 20261027
TAKES_PER_ACTION = 5
OUT = Path(__file__).parent / "clips.txt"


def parse(stem):
    # AIHUB_CLIP
    # crime, action, symptom, place, shooting, then
    # scenario (2 digits) + day/night (D/N) + camera (A/B) + length type (S)
    crime, action, symptom, place, shooting, tail = stem.split("_")
    # Same scene and scenario shot by day and by night are separate takes.
    take = "_".join([crime, action, symptom, place, shooting, tail[:3]])
    return action, take, tail[3]


vl_dir = Path(sys.argv[1])
takes = defaultdict(lambda: defaultdict(dict))  # action -> take -> camera -> (zip, stem)
for zp in vl_dir.glob("VL*.zip"):
    vs_zip = "VS" + zp.stem[2:] + ".zip"  # video zip with the same number
    for name in zipfile.ZipFile(zp).namelist():
        if not name.endswith(".json"):
            continue
        stem = name.rsplit("/", 1)[-1][:-5]
        action, take, cam = parse(stem)
        assert cam not in takes[action][take], f"duplicate clip: {stem}"
        takes[action][take][cam] = (vs_zip, stem)

# Sort before every draw so the result does not depend on zip member order.
rng = random.Random(SEED)
picked = []
for action in sorted(takes):
    for take in rng.sample(sorted(takes[action]), TAKES_PER_ACTION):
        cam = rng.choice(sorted(takes[action][take]))
        picked.append((action, *takes[action][take][cam]))

assert len(takes) == 20, f"expected 20 actions, found {len(takes)}"
assert len(picked) == 20 * TAKES_PER_ACTION

with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(f"# seed={SEED} python={platform.python_version()} "
            "source=AI Hub dataSetSn=515, 2.Validation\n")
    f.write(f"# rule: per action, {TAKES_PER_ACTION} distinct takes "
            "(scene + scenario + day/night), one camera (A/B) per take at random\n")
    f.write("# action\tzip\tclip\n")
    for action, vs_zip, stem in picked:
        f.write(f"{action}\t{vs_zip}\t{stem}.mp4\n")

night = sum(stem.split("_")[5][2] == "N" for _, _, stem in picked)
print(f"{len(picked)} clips ({night} night) -> {OUT}")

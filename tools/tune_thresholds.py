# Python Standard Library Dependencies
import argparse
import glob
import itertools
import json
import os
import sys

# External Dependency Imports
from fuzzywuzzy import fuzz

# Internal Dependency Imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import tiktok_scraper.Core.impression as impression

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# Scores the label cutoff and the same-video thresholds on a folder of fixtures made by
# tools/make_fixtures.py (frames named <ad>_tNN.json; frames of one ad share <ad>).
#
#   python tools/tune_thresholds.py tests/fixtures/s3            (report at the current settings)
#   python tools/tune_thresholds.py tests/fixtures/s3 --grid     (try a grid of settings)
#   python tools/tune_thresholds.py tests/fixtures/s3 --lines    (list near-miss label lines)
#
# "same" pairs are consecutive frames of one ad; "different" pairs are frames of two ads.
# Different-ad pairs judged the same are worth reading with --show: two captures of one
# campaign, or two end cards, are expected there and are not errors of the rule.
# The fixtures are all ads, so this cannot measure false alarms on ordinary videos.


def load(folder):
    frames = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        name = os.path.splitext(os.path.basename(path))[0]
        frames[name] = {
            "results": [(d["bbox"], d["text"], d["conf"]) for d in data["detections"]],
            "height": data["crop_size"][1],
            "width": data["crop_size"][0]
        }
    ads = {}
    for name in frames:
        ads.setdefault(name.rsplit("_t", 1)[0], []).append(name)
    return frames, ads


def label_report(frames, ads, cutoff):
    frame_hits = sum(impression.match_label(frame["results"], cutoff=cutoff, width=frame["width"])[0] is not None for frame in frames.values())
    ad_hits = sum(any(impression.match_label(frames[name]["results"], cutoff=cutoff, width=frames[name]["width"])[0] for name in names) for names in ads.values())
    first_hits = sum(impression.match_label(frames[names[0]]["results"], cutoff=cutoff, width=frames[names[0]]["width"])[0] is not None for names in ads.values())
    return frame_hits, ad_hits, first_hits


def video_report(frames, ads):
    texts = {name: impression.screen_text(frame["results"], height=frame["height"], width=frame["width"]) for name, frame in frames.items()}
    same = [(first, second) for names in ads.values() for first, second in zip(names, names[1:])]
    groups = list(ads.values())
    different = [(first, second) for one, two in itertools.combinations(groups, 2) for first in one for second in two]
    same_hits = [pair for pair in same if impression.same_video(texts[pair[0]], texts[pair[1]]) is True]
    collisions = [pair for pair in different if impression.same_video(texts[pair[0]], texts[pair[1]]) is True]
    return texts, same, same_hits, different, collisions


def near_miss_lines(frames, low=55, high=95):
    # Every detection whose best label score falls in the band, for reading by eye
    seen = {}
    for name, frame in frames.items():
        for _, text, confidence in frame["results"]:
            score, flag = max((fuzz.ratio(text, flag), flag) for flag in impression.SPONSORED_FLAGS)
            if low <= score <= high:
                seen.setdefault((score, text, flag), []).append(name)
    for (score, text, flag), names in sorted(seen.items()):
        print(f"  {score:3d}  {text!r:40s} vs {flag!r:18s} in {len(names)} frame(s), e.g. {names[0]}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Score label and same-video thresholds on fixtures")
    parser.add_argument("folder")
    parser.add_argument("--grid", action="store_true", help="try a grid of settings")
    parser.add_argument("--lines", action="store_true", help="list detections that nearly match a label")
    parser.add_argument("--show", action="store_true", help="show the pairs that are judged wrongly")
    args = parser.parse_args()

    frames, ads = load(args.folder)
    print(f"{len(frames)} frames of {len(ads)} ads in {args.folder}")

    print("\nLabel cutoff (a label matches when its score is above the cutoff)")
    cutoffs = range(60, 91, 2) if args.grid else [impression.LABEL_CUTOFF]
    for cutoff in cutoffs:
        frame_hits, ad_hits, first_hits = label_report(frames, ads, cutoff)
        mark = "  <- current" if cutoff == impression.LABEL_CUTOFF else ""
        print(f"  cutoff {cutoff}: label in {frame_hits}/{len(frames)} frames, in the first frame of {first_hits}/{len(ads)} ads, in some frame of {ad_hits}/{len(ads)} ads{mark}")
    if args.lines:
        print("\nDetections scoring 55 to 95 against a label")
        near_miss_lines(frames)

    print("\nSame-video check")
    if args.grid:
        rows = []
        for threshold, min_length, min_total in itertools.product([55, 60, 65, 70, 75, 80], [12, 15, 20], [0, 20, 30, 40]):
            impression.SAME_VIDEO_THRESHOLD, impression.SHARED_LINE_MIN_LENGTH, impression.SHARED_LINES_MIN_TOTAL = threshold, min_length, min_total
            _, same, same_hits, different, collisions = video_report(frames, ads)
            rows.append((len(collisions), -len(same_hits), threshold, min_length, min_total, len(same), len(different)))
        rows.sort()
        print("  different-ad pairs judged same | same-ad pairs judged same | whole-text threshold | shared line length | shared total")
        for collisions, same_hits, threshold, min_length, min_total, same_total, different_total in rows[:25]:
            print(f"  {collisions:5d}/{different_total}   {-same_hits:3d}/{same_total}   threshold {threshold}   line length {min_length}   total {min_total}")
    else:
        texts, same, same_hits, different, collisions = video_report(frames, ads)
        print(f"  same-ad pairs judged the same video: {len(same_hits)}/{len(same)}")
        print(f"  different-ad pairs judged the same video: {len(collisions)}/{len(different)}")
        if args.show:
            for first, second in [pair for pair in same if pair not in same_hits]:
                print(f"\n  MISSED (same ad): {first} / {second}\n    {texts[first]['lines']}\n    {texts[second]['lines']}")
            for first, second in collisions:
                print(f"\n  COLLISION (different ads): {first} / {second}\n    {texts[first]['lines']}\n    {texts[second]['lines']}")

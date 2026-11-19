# Python Standard Library Dependencies
import glob
import json
import os

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_results(folder, name):
    # Fixture JSON -> EasyOCR-shaped results: a list of (bbox, text, confidence)
    with open(os.path.join(FIXTURE_DIR, folder, f"{name}.json"), encoding="utf-8") as file:
        data = json.load(file)
    return [(d["bbox"], d["text"], d["conf"]) for d in data["detections"]]


def fixture_names(folder):
    paths = glob.glob(os.path.join(FIXTURE_DIR, folder, "*.json"))
    return sorted(os.path.splitext(os.path.basename(path))[0] for path in paths)

# Python Standard Library Dependencies
import argparse
import json
import os
import random
import re
import tempfile

# External Dependency Imports
import cv2

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# Builds test fixtures: for each frame, the lower half of the window that the scraper reads
# (saved as JPEG) and the EasyOCR output for that crop (saved as JSON). The tests read
# only the JSON, so they need no EasyOCR, PyTorch or win32.
#
#   python tools/make_fixtures.py s3 --count 30 --out tests/fixtures/s3
#   python tools/make_fixtures.py image shot1.png shot2.png --out tests/fixtures/capture
#   python tools/make_fixtures.py video clip.mp4 --out tests/fixtures/capture
#
# s3 mode reads AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and S3_BUCKET (or S3_Bucket)
# from the environment or from src/.env. Run it from the repository root.

FRAME_TIMES = [1.0, 3.0, 10.0]
_reader = None
RUN_OCR = True


def get_reader():
    # EasyOCR is imported lazily so --help and listing work without it
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


def crop_for_check(image):
    # Lower half of the window at full width, so the whole caption is in the crop
    height = image.shape[0]
    return image[height // 2:(height // 2) * 2, :]


def ocr_crop(crop, run_ocr=True):
    if not run_ocr:
        return []
    result = get_reader().readtext(crop)
    return [
        {
            "bbox": [[int(point[0]), int(point[1])] for point in bbox],
            "text": str(text),
            "conf": round(float(conf), 4)
        }
        for bbox, text, conf in result
    ]


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def write_fixture(image, out_dir, name, source, frame_time):
    crop = crop_for_check(image)
    detections = ocr_crop(crop, RUN_OCR)
    os.makedirs(out_dir, exist_ok=True)
    cv2.imwrite(os.path.join(out_dir, f"{name}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
    with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as file:
        json.dump({
            "source": source,
            "frame_time_s": frame_time,
            "frame_size": [int(image.shape[1]), int(image.shape[0])],
            "crop": "lower half, full width",
            "crop_size": [int(crop.shape[1]), int(crop.shape[0])],
            "detections": detections
        }, file, indent=1, ensure_ascii=False)
    print(f"{name}: {[d['text'] for d in detections]}")


def frames_from_video(video_path, times):
    # Yields (time, frame) for each requested time that exists in the video
    capture = cv2.VideoCapture(video_path)
    fps = capture.get(cv2.CAP_PROP_FPS) or 0
    total = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    for frame_time in times:
        frame_index = int(frame_time * fps)
        if fps <= 0 or frame_index >= total:
            continue
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if ok:
            yield frame_time, frame
    capture.release()


def video_to_fixtures(video_path, out_dir, name, source):
    for frame_time, frame in frames_from_video(video_path, FRAME_TIMES):
        write_fixture(frame, out_dir, f"{name}_t{int(frame_time):02d}", source, frame_time)


def run_s3(args):
    import boto3
    from dotenv import load_dotenv
    load_dotenv(os.path.join("src", ".env"))
    bucket = args.bucket or os.environ.get("S3_BUCKET") or os.environ.get("S3_Bucket")
    if not bucket:
        raise SystemExit("No bucket: pass --bucket or set S3_BUCKET in the environment or src/.env")
    s3_client = boto3.client("s3")

    # List every .mp4 key in the bucket
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=args.prefix):
        for item in page.get("Contents", []):
            if item["Key"].lower().endswith(".mp4"):
                keys.append(item["Key"])
    keys.sort()
    print(f"{len(keys)} videos in the bucket")
    if args.list:
        for key in keys:
            print(key)
        return

    # Seeded sample so the same fixture set can be rebuilt
    sample = random.Random(args.seed).sample(keys, min(args.count, len(keys)))
    with tempfile.TemporaryDirectory() as temp_dir:
        for key in sorted(sample):
            video_path = os.path.join(temp_dir, "video.mp4")
            s3_client.download_file(bucket, key, video_path)
            name = safe_name(os.path.splitext(os.path.basename(key))[0])
            video_to_fixtures(video_path, args.out, name, key)


def run_image(args):
    for path in args.paths:
        image = cv2.imread(path)
        if image is None:
            print(f"Could not read {path}, skipping")
            continue
        name = safe_name(os.path.splitext(os.path.basename(path))[0])
        write_fixture(image, args.out, name, os.path.basename(path), None)


def run_video(args):
    for path in args.paths:
        name = safe_name(os.path.splitext(os.path.basename(path))[0])
        video_to_fixtures(path, args.out, name, os.path.basename(path))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Build OCR test fixtures")
    modes = parser.add_subparsers(dest="mode", required=True)

    s3_mode = modes.add_parser("s3", help="sample captured ad videos from the S3 bucket")
    s3_mode.add_argument("--bucket", default=None)
    s3_mode.add_argument("--prefix", default="")
    s3_mode.add_argument("--count", type=int, default=30)
    s3_mode.add_argument("--seed", type=int, default=20261119)
    s3_mode.add_argument("--list", action="store_true", help="only list the videos")
    s3_mode.add_argument("--out", default=os.path.join("tests", "fixtures", "s3"))
    s3_mode.set_defaults(run=run_s3)

    image_mode = modes.add_parser("image", help="full-window screenshots")
    image_mode.add_argument("paths", nargs="+")
    image_mode.add_argument("--out", default=os.path.join("tests", "fixtures", "capture"))
    image_mode.set_defaults(run=run_image)

    video_mode = modes.add_parser("video", help="local full-window recordings")
    video_mode.add_argument("paths", nargs="+")
    video_mode.add_argument("--out", default=os.path.join("tests", "fixtures", "capture"))
    video_mode.set_defaults(run=run_video)

    parser.add_argument("--no-ocr", action="store_true", help="save the crops only, with empty detections")
    args = parser.parse_args()
    RUN_OCR = not args.no_ocr
    args.run(args)

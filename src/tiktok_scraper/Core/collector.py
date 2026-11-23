# Python Standard Library Dependencies
import importlib.util
import os
import queue
import shutil
import subprocess
import sys
import time
import traceback

# External Dependency Imports
import cv2
import numpy

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data
from tiktok_scraper.Core.impression import match_label, screen_text, same_video, read_region, READ_WIDTH_SHARE

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# The collection loop for the TikTok app, whatever it is running on. The thing it runs on
# is a "device" object (BlueStacksDevice in bluestacks.py, PhoneDevice in phone.py) with:
#   read_frame()                -> full-screen BGR image
#   swipe_up()                  -> sends the swipe to the next video, returns its parameters
#   trim()                      -> frees emulator memory (does nothing on a phone)
#   refresh()                   -> presses back to refresh the feed
#   record_ad(folder, seconds)  -> records the ad into folder, returns {"fps": n} for a folder
#                                  of numbered PNGs or {"video_path": path} for a finished video
#   describe()                  -> details of the device for the session record

# After this many swipes in a row that do not change the video, press back and refresh the feed
STUCK_SWIPE_LIMIT = 5
# Seconds of each ad that are recorded
AD_CAPTURE_SECONDS = 20
# Stored frames are brought to this width, so a phone's 1080-wide screen and the 540-wide emulator
# give frames, text positions and thresholds on the same scale
FRAME_WIDTH = 540
# Seconds to wait after a swipe before the first read, for the next video to settle
SETTLE_SECONDS = 1
# Seconds between reads of the same video
REREAD_GAP_SECONDS = 0.3

# Each video is read at least READS_PER_VIDEO times before it is swiped away: the label can render
# late or be misread on one frame, and any read that shows it starts the ad capture.
# How long a video is then watched is decided by watch_seconds() below. DWELL_SECONDS is the fixed
# answer it gives for now; set it to 0 to swipe as soon as the reads are done.
# Both can be set in src/.env, which configure_from_environment() applies once it is loaded.
DWELL_SECONDS = 5.0
READS_PER_VIDEO = 2

# Which OCR engine reads the screen: "rapidocr" (fast) or "easyocr" (slow, the original). With
# OCR_ENGINE unset, RapidOCR is used when it is installed and EasyOCR otherwise.
# OCR_SCALE is how much larger than a stored frame the image given to the engine is. RapidOCR is
# given a phone's screenshot at its own resolution, not the stored half-size frame. On enlarged
# fixture frames (2026-11-23) the older rapidocr-onnxruntime package read more labels at double
# size (115 of 120 against 111); the newer rapidocr package read the same either way.
OCR_SCALE = {"rapidocr": 2, "easyocr": 1}

# How the screen is read. "ocr" (the default) reads a screenshot, and when that finds no text at all
# on a phone it asks the phone for the text on screen as a backup. "ui" asks the phone first and uses
# OCR only when the phone gives nothing. The phone's text is exact, but on the test Pixel a request
# took about 5 seconds against well under 1 for OCR, so it is the backup, not the main reader.
# Set SCREEN_READER in src/.env to change it.
SCREEN_READER = "ocr"

_engine = None


def configure_from_environment():
    global DWELL_SECONDS, READS_PER_VIDEO, SCREEN_READER
    if (os.environ.get("SCREEN_READER") or "").strip().lower() in ["ocr", "ui"]:
        SCREEN_READER = os.environ["SCREEN_READER"].strip().lower()
    if os.environ.get("DWELL_SECONDS"):
        DWELL_SECONDS = float(os.environ["DWELL_SECONDS"])
    if os.environ.get("READS_PER_VIDEO"):
        READS_PER_VIDEO = max(1, int(os.environ["READS_PER_VIDEO"]))


def load_engine():
    # Returns (name, function taking an image and returning EasyOCR-style [(box, text, confidence)])
    wanted = (os.environ.get("OCR_ENGINE") or "").strip().lower()
    if wanted in ["", "rapidocr"]:
        problems = []
        # Two packages provide RapidOCR. Each is run once here: a missing model file or a broken
        # onnxruntime only shows when the engine is first used, and it is better to find that out
        # now, and move on to the next choice, than to stop at the first video.
        for package, load in [("rapidocr_onnxruntime", load_rapidocr_onnxruntime_package), ("rapidocr", load_rapidocr_package)]:
            if importlib.util.find_spec(package) is None:
                problems.append(f"{package} is not installed")
                continue
            try:
                # First in a separate Python: if the engine's own code crashes, it takes the whole
                # program down with no error message, and that must not be this program
                crash = crashes_in_a_separate_process(load.__name__)
                if crash:
                    problems.append(f"{load.__name__}: {crash}")
                    continue
                read = load()
                read(numpy.zeros((64, 256, 3), dtype=numpy.uint8))
                quiet_rapidocr_logging()
                return "rapidocr", read
            except Exception as error:
                problems.append(f"{load.__name__}: {type(error).__name__}: {error}")
        quiet_rapidocr_logging()
        if wanted == "rapidocr":
            raise RuntimeError("RapidOCR could not start. " + " | ".join(problems))
        print("RapidOCR could not start, so EasyOCR is being used instead, which is slower.")
        for problem in problems:
            print("   " + problem)
        print("To use RapidOCR, set up the Python 3.12 environment in environment.yml (see the README).")
    import easyocr
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return "easyocr", reader.readtext


def crashes_in_a_separate_process(loader_name):
    # Loads the engine and reads one blank image in a second Python. Returns None if that worked,
    # or a description of how it ended.
    code = (
        "import numpy\n"
        "from tiktok_scraper.Core import collector\n"
        f"collector.{loader_name}()(numpy.zeros((64, 256, 3), dtype=numpy.uint8))\n"
    )
    source_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    environment = dict(os.environ, PYTHONPATH=source_dir + os.pathsep + os.environ.get("PYTHONPATH", ""))
    try:
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=300, env=environment)
    except subprocess.TimeoutExpired:
        return "did not finish within 5 minutes"
    if result.returncode == 0:
        return None
    last_line = (result.stderr.decode(errors="replace").strip().splitlines() or ["no error message"])[-1]
    return f"stopped with exit code {result.returncode} ({last_line})"


def load_rapidocr_package():
    # The newer package; it downloads its model files the first time it is used
    from rapidocr import RapidOCR
    engine = RapidOCR()

    def read(image):
        output = engine(image)
        if output.txts is None:
            return []
        return [(box.tolist(), text, float(score)) for box, text, score in zip(output.boxes, output.txts, output.scores)]
    return read


def load_rapidocr_onnxruntime_package():
    # The older package; its model files come with it, so it needs no download
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()

    def read(image):
        result, _ = engine(image)
        return [(box, text, float(score)) for box, text, score in (result or [])]
    return read


def quiet_rapidocr_logging():
    # The package reports every step it takes; only its errors are wanted in the console
    import logging
    for name in list(logging.root.manager.loggerDict):
        if "rapidocr" in name.lower():
            logging.getLogger(name).setLevel(logging.ERROR)


def engine_name():
    global _engine
    if _engine is None:
        _engine = load_engine()
    return _engine[0]


def ocr(region):
    # The engine is loaded on first use so that this module can be imported without one installed
    global _engine
    if _engine is None:
        _engine = load_engine()
    return _engine[1](region)


def normalise_frame(image, width=FRAME_WIDTH):
    height, original_width = image.shape[:2]
    if abs(original_width - width) <= width * 0.1:
        return image
    interpolation = cv2.INTER_AREA if original_width > width else cv2.INTER_CUBIC
    return cv2.resize(image, (width, round(height * width / original_width)), interpolation=interpolation)


def ui_results(nodes, screen_size):
    # Puts the text elements a phone reports into the same form as OCR results: [(box, text, confidence)]
    # for the read region (lower half, count rail removed), on the scale of a stored frame.
    # Returns (results, region width, region height).
    screen_width, screen_height = screen_size
    scale = FRAME_WIDTH / screen_width
    frame_height = round(screen_height * scale)
    region_top = frame_height // 2
    region_height = frame_height // 2
    region_width = int(FRAME_WIDTH * READ_WIDTH_SHARE)
    results = []
    for (left, top, right, bottom), text in nodes:
        left, top, right, bottom = left * scale, top * scale - region_top, right * scale, bottom * scale - region_top
        centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
        if not (0 <= centre_x < region_width and 0 <= centre_y < region_height):
            continue
        results.append(([[left, top], [right, top], [right, bottom], [left, bottom]], text, 1.0))
    return results, region_width, region_height


def read_screen(device):
    # Reads what is on screen. Returns a dict:
    #   results        [(box, text, confidence)] for the read region, positions on the stored-frame scale
    #   region_width, region_height   size of the read region on that scale
    #   read_ms        how long the read took
    #   source         the name of the OCR engine, "ui", or "ui-backup" when OCR found nothing and the phone's text was used
    #   image          the stored-size screenshot if one was taken for this read, else None
    read_start = time.perf_counter()
    if SCREEN_READER == "ui" and hasattr(device, "read_text"):
        found = device.read_text()
        if found is not None:
            results, region_width, region_height = ui_results(*found)
            return {
                "results": results, "region_width": region_width, "region_height": region_height,
                "read_ms": round((time.perf_counter() - read_start) * 1000, 1), "source": "ui", "image": None
            }

    original = device.read_frame()
    image = normalise_frame(original)

    # Read the lower half of the screen, where the account, the whole caption and the ad label sit,
    # without the right-hand count rail. The colour image is read as it is: on the S3 ad frames an
    # Otsu threshold lost the label in about a third of frames, and the left half alone cut the captions short.
    ocr_start = time.perf_counter()
    scale = OCR_SCALE.get(engine_name(), 1)
    if scale == 1:
        region = read_region(image)
        results = ocr(region)
    else:
        # A phone's screenshot already has the extra detail; an emulator's is enlarged
        large = read_region(normalise_frame(original, FRAME_WIDTH * scale))
        results = [
            ([[point[0] / scale, point[1] / scale] for point in box], text, confidence)
            for box, text, confidence in ocr(large)
        ]
        region = read_region(image)
    reading = {
        "results": results, "region_width": region.shape[1], "region_height": region.shape[0],
        "read_ms": round((time.perf_counter() - ocr_start) * 1000, 1), "source": engine_name(), "image": image
    }

    # Backup: OCR found nothing to go on (no label, no account, no caption), which happens while a
    # video is loading or when the text is in a script the engine cannot read. Ask the phone instead.
    nothing_read = (
        match_label(results, width=reading["region_width"])[0] is None
        and not screen_text(results, height=reading["region_height"], width=reading["region_width"])["lines"]
    )
    if nothing_read and SCREEN_READER == "ocr" and hasattr(device, "read_text"):
        found = device.read_text()
        if found is not None:
            backup_results, _, _ = ui_results(*found)
            if backup_results:
                reading["results"] = backup_results
                reading["source"] = "ui-backup"
                reading["read_ms"] = round((time.perf_counter() - ocr_start) * 1000, 1)
    return reading


def watch_seconds(text):
    # How long the video on screen should be watched before it is swiped away, given its account and
    # caption. Every video gets the same time for now. This is where the interest scorer will decide:
    # next to nothing for a video the account should not care about, longer for one it should.
    return DWELL_SECONDS


def leave_video(device, impression_log):
    global monitoring_data
    # Swipe away from the video on screen and record the swipe against its impression
    swipe = device.swipe_up()
    impression_log.note_swipe(time.time(), swipe)
    swipes = impression_log.swipe_count()

    if swipes == 1:
        # First swipe away from a counted video: the routine housekeeping, once per video
        # Trim the memory if it's been 25 videos
        if monitoring_data["videos_watched"] % 25 == 0:
            device.trim()

        # Every 150 videos wait, hit the back button and wait 10 seconds to refresh the feed
        if monitoring_data["videos_watched"] % 150 == 0:
            time.sleep(2)
            device.refresh()
            time.sleep(10)
    elif swipes % STUCK_SWIPE_LIMIT == 0:
        # The feed is stuck (pop-up, frozen video): the swipes are not changing what is on screen
        print(f"Feed has not moved after {swipes} swipes, refreshing")
        time.sleep(2)
        device.refresh()
        impression_log.note_refresh()
        time.sleep(10)


def find_ad(device, impression_log):
    # Watches videos, swiping past the ones that are not ads, and returns when an ad is on screen
    global monitoring_data
    just_swiped = True
    while True:
        # After a swipe give the next video a moment to settle; between reads of one video, a short gap
        time.sleep(SETTLE_SECONDS if just_swiped else REREAD_GAP_SECONDS)
        just_swiped = False
        read_started = time.time()

        # Read the screen once; the same OCR results feed the label check, the swipe check and the log
        reading = read_screen(device)
        results = reading["results"]
        scoring_start = time.perf_counter()
        flag, score, matched_text = match_label(results, width=reading["region_width"])
        text = screen_text(results, height=reading["region_height"], width=reading["region_width"])
        previous_text = impression_log.current_text()
        if previous_text is None:
            same = False
        elif impression_log.swipe_count() == 0:
            # No swipe has been sent since this video was opened, so this is another look at it
            same = True
        else:
            same = same_video(previous_text, text)
        scoring_ms = round((time.perf_counter() - scoring_start) * 1000, 1)

        if same is True:
            impression_log.note_read(text)
            if flag is not None and not impression_log.is_ad():
                # The label was not read at first but is on screen now: it rendered late, was misread
                # the first time, or a swipe was wrongly judged to have failed. An ad is showing.
                print("Ad found on a later read at index: " + str(monitoring_data["data_index"]))
                impression_log.mark_ad(flag, score, matched_text)
                return
            if impression_log.swipe_count() > 0:
                # The text on screen has not changed, so the last swipe did not advance. Swipe again without counting.
                print("Swipe not confirmed at index: " + str(monitoring_data["data_index"]) + ", swiping again")
                leave_video(device, impression_log)
                just_swiped = True
                continue
        else:
            # A new video: write the row for the one just left, count this one and store its frame.
            # A read with no text is still counted, but flagged, since nothing shows that the video changed.
            # This video came on screen when the previous one was swiped away, not when it was first read
            shown_at = impression_log.last_swipe_time() or read_started
            if impression_log.has_open():
                impression_log.close("advanced")
                monitoring_data["data_index"] += 1
            monitoring_data["videos_watched"] += 1
            frame_path = impression_log.next_frame_path()
            # A read that took its text from the phone has no screenshot yet; take the one stored frame now
            image = reading["image"] if reading["image"] is not None else normalise_frame(device.read_frame())
            cv2.imwrite(frame_path, image, [cv2.IMWRITE_JPEG_QUALITY, 80])
            impression_log.open(shown_at, text, flag, score, matched_text, reading["read_ms"], scoring_ms, frame_path, advance_confirmed=len(text["lines"]) > 0, first_read_at=read_started, text_source=reading["source"])
            if flag is not None:
                print("Ad found at index: " + str(monitoring_data["data_index"]))
                return

        # Not an ad so far. Keep watching until the video has had its time and its reads, then move on.
        watched = time.time() - impression_log.shown_time()
        if impression_log.read_count() >= READS_PER_VIDEO and watched >= watch_seconds(impression_log.current_text()):
            print(f"No ad found at index: {monitoring_data['data_index']} ({impression_log.read_count()} reads, {round(watched, 1)}s)")
            leave_video(device, impression_log)
            just_swiped = True


def capture_ad(device, output_dir, data_lock, job_queue, impression_log):
    try:
        # create folder for the recording
        date_time = time.strftime("%Y%m%dat%H%M%S")
        folder_name = f"{date_time}_{str(monitoring_data['ads_found'])}_{monitoring_data['demographic']}_{monitoring_data['location']}"
        ad_folder = os.path.join(output_dir, folder_name)
        os.mkdir(ad_folder)

        recording = device.record_ad(ad_folder, AD_CAPTURE_SECONDS)

        # Submit the job to the processing queue
        with data_lock:
            monitoring_data["ads_this_log"] += 1
            monitoring_data["ads_found"] += 1
            job = {
                "folder_name": folder_name,
                "found_at_time": date_time,
                "found_at_index": monitoring_data["data_index"],
                "demographic": monitoring_data["demographic"],
                "location": monitoring_data["location"]
            }
            job.update(recording)
            job_queue.put(job)
        leave_video(device, impression_log)
    except Exception as e:
        print(e)
        print("An error occurred while capturing the ad. Exiting...")
        traceback.print_exc()
        quit(1)


# TODO we need to process this later for things like the Caption text and the length
def process_ads(stop_event, error_event, keystroke_event, job_queue, output_dir):
    while not stop_event.is_set() and not error_event.is_set() and not keystroke_event.is_set():
        try:
            ad_info = job_queue.get(block=True, timeout=30)
            ad_id = f"{ad_info['found_at_time']}_{ad_info['demographic']}_{ad_info['location']}"
            ad_folder = os.path.join(output_dir, ad_info["folder_name"])
            if ad_info.get("video_path"):
                # The device recorded a finished video
                video_path = ad_info["video_path"]
            else:
                # The device saved numbered screenshots that need stitching
                from tiktok_scraper.Core.processing import stitch_video
                video_path = stitch_video(ad_folder, ad_info["fps"], ad_id)
            # Upload the video to S3
            import boto3
            import pandas
            s3_client = boto3.client('s3')
            s3_client.upload_file(video_path, os.environ.get("S3_BUCKET"), f"{ad_id}.mp4")
            pandas.DataFrame({
                "Ad ID": [ad_id],
                "Demographic": [ad_info["demographic"]],
                "Location": [ad_info["location"]],
                "FPS": [ad_info.get("fps")],
                "Found At Index": [ad_info["found_at_index"]]
            }).to_csv(os.path.join(output_dir, "Data Key.csv"), mode='a', header=False, index=False)
            print(f"Ad {ad_id} processed and uploaded to S3. Removing it from the disk.")
            shutil.rmtree(ad_folder)

        except queue.Empty:
            continue

        except Exception as e:
            print(e)
            print("An error occurred while processing the ad. Exiting...")
            traceback.print_exc()
            error_event.set()
            break


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code

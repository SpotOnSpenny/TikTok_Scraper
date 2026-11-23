# Python Standard Library Dependencies
import os
import queue
import shutil
import time
import traceback

# External Dependency Imports
import cv2

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data
from tiktok_scraper.Core.impression import match_label, screen_text, same_video, read_region

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
# Frames are brought to this width before they are read and stored, so that a phone's
# 1080-wide screen costs the same OCR time, and reads the same, as the 540-wide emulator
FRAME_WIDTH = 540

_reader = None


def ocr(region):
    # EasyOCR is loaded on first use so that this module can be imported without it
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader.readtext(region)


def normalise_frame(image):
    height, width = image.shape[:2]
    if width <= FRAME_WIDTH * 1.1:
        return image
    return cv2.resize(image, (FRAME_WIDTH, round(height * FRAME_WIDTH / width)), interpolation=cv2.INTER_AREA)


def read_screen(device):
    # Returns (full-screen image, EasyOCR results for the read region, height of that region, OCR time in ms)
    image = normalise_frame(device.read_frame())

    # Read the lower half of the screen, where the account, the whole caption and the "Sponsored" badge
    # sit, without the right-hand count rail. The colour image is read as it is: on the S3 ad frames an
    # Otsu threshold lost the label in about a third of frames, and the left half alone cut the captions short.
    ocr_start = time.perf_counter()
    region = read_region(image)
    results = ocr(region)
    ocr_ms = round((time.perf_counter() - ocr_start) * 1000, 1)
    return image, results, region.shape[0], ocr_ms


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
    global monitoring_data
    ad_found = False
    while ad_found is False:
        time.sleep(1)
        # Start timer for process
        start_time = time.time()

        # Read the screen once; the same OCR results feed the label check, the swipe check and the log
        image, results, region_height, ocr_ms = read_screen(device)
        scoring_start = time.perf_counter()
        flag, score, matched_text = match_label(results)
        text = screen_text(results, height=region_height)
        previous_text = impression_log.current_text()
        same = same_video(previous_text, text) if previous_text is not None else False
        scoring_ms = round((time.perf_counter() - scoring_start) * 1000, 1)

        if same is True:
            impression_log.note_read()
            if flag is not None and not impression_log.is_ad():
                # Same video as before, but the label is on screen now: it rendered late, or this was
                # wrongly judged the same. Either way an ad is showing that has not been captured.
                print("Ad found on a later read at index: " + str(monitoring_data["data_index"]))
                impression_log.mark_ad(flag, score, matched_text)
                ad_found = True
            else:
                # The text on screen has not changed, so the last swipe did not advance. Swipe again without counting.
                print("Swipe not confirmed at index: " + str(monitoring_data["data_index"]) + ", swiping again")
                leave_video(device, impression_log)
        else:
            # A new video: write the row for the one just left, count this one and store its frame.
            # A read with no text is still counted, but flagged, since nothing shows that the video changed.
            # This video came on screen when the previous one was swiped away, not when it was first read
            shown_at = impression_log.last_swipe_time() or start_time
            if impression_log.has_open():
                impression_log.close("advanced")
                monitoring_data["data_index"] += 1
            monitoring_data["videos_watched"] += 1
            frame_path = impression_log.next_frame_path()
            cv2.imwrite(frame_path, image, [cv2.IMWRITE_JPEG_QUALITY, 80])
            impression_log.open(shown_at, text, flag, score, matched_text, ocr_ms, scoring_ms, frame_path, advance_confirmed=len(text["lines"]) > 0, first_read_at=start_time)
            if flag is not None:
                print("Ad found at index: " + str(monitoring_data["data_index"]))
                ad_found = True
            else:
                print("No ad found at index: " + str(monitoring_data["data_index"]))
                leave_video(device, impression_log)
        # End timer for process to nearest second
        end_time = time.time()
        time_elapsed = (end_time - start_time).__round__(2)
        print(f"Time elapsed determining if ad or not: {time_elapsed}")
        if time_elapsed < 5:
            wait = 5 - time_elapsed
            time.sleep(wait)


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

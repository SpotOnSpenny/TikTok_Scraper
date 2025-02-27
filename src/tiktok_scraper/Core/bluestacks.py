# Python Standard Library Dependencies
import time
import os
from PIL import Image
import pandas
import shutil
import traceback
import queue
import ctypes
import contextlib

# External Dependency Imports
import win32gui
import win32con
import win32api
import win32ui
import cv2
import easyocr
import boto3
from fuzzywuzzy import fuzz

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data
from tiktok_scraper.Core.processing import stitch_video

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

sponsored_flags = ["Sponsored", "Paid Promotion", "Paid partnership" "Promoted Music"]
ctypes.windll.user32.SetProcessDPIAware()
reader = easyocr.Reader(['en'], gpu=False, verbose=False)

@contextlib.contextmanager
def gdi_context(dcObj, cDC, wDC, dataBitMap, hwnd):
    try:
        yield
    finally:
        dcObj.DeleteDC()
        cDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, wDC)
        win32gui.DeleteObject(dataBitMap.GetHandle())

def find_bluestacks_window(window_name):
    print("locating window")
    def callback(hwnd, results):
        window_title = win32gui.GetWindowText(hwnd)
        if f"{window_name}" in window_title:
            results.append((hwnd, window_title))
    results = []
    win32gui.EnumWindows(callback, results)
    if len(results) > 0:
        index = 0
        for hwnd, window_title in results:
            print(f"{index} - Window found: {window_title}. (HWND: {hwnd})")
            index += 1
        correct_index = input("Which item was the correct window? (Enter it's index, enter 'Not found' if no window is correct): \n")
        if correct_index.lower() == "not found" or int(correct_index) > len(results) - 1:
            print("Window not found. Please try again.")
            return None
    else:
        print("Window not found Please try again.")
        return None
    hwndChild = win32gui.GetWindow(results[int(correct_index)][0], win32con.GW_CHILD)
    return hwndChild

def scroll_down(hwnd, rect):
    global monitoring_data
    x = (rect[2] - rect[0]) // 2
    y = (rect[3] - rect[1]) // 4
    win32api.SetCursorPos((x, y*3))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, -(y*2), 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
    monitoring_data["data_index"] += 1
    monitoring_data["videos_watched"] += 1

    # Trim the memory if it's been 25 videos
    # We trim twice since the first time sometimes just registers the hotkey, the second removes the memory
    if monitoring_data["videos_watched"] % 25 == 0:
        trim_mem()
        time.sleep(1)
        trim_mem()
        time.sleep(1)
    
    # Every 150 videos send CTRL + SHIFT + 2 and wait 10 seconds to hit the back button and refresh the feed 
    if monitoring_data["videos_watched"] % 150 == 0:
        time.sleep(2)
        refresh_feed()
        time.sleep(10)

def find_bluestacks_ad(hwnd):
    global monitoring_data
    rect = win32gui.GetWindowRect(hwnd)
    ad_found = False
    while ad_found is False:
        time.sleep(1)
        # Start timer for process
        start_time = time.time()
        if check_for_ad(hwnd, rect):
            print("Ad found at index: " + str(monitoring_data["data_index"]))
            ad_found = True
        else:
            print("No ad found at index: " + str(monitoring_data["data_index"]))
            scroll_down(hwnd, rect)
        # End timer for process to nearest second
        end_time = time.time()
        time_elapsed = (end_time - start_time).__round__(2)
        print(f"Time elapsed determining if ad or not: {time_elapsed}")
        if time_elapsed < 5:
            wait = 5 - time_elapsed
            time.sleep(wait)
    return rect

def take_screencaps(hwnd, rect, file_name):
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    
    wDC = win32gui.GetWindowDC(hwnd)
    dcObj=win32ui.CreateDCFromHandle(wDC)
    cDC=dcObj.CreateCompatibleDC()
    dataBitMap = win32ui.CreateBitmap()
    dataBitMap.CreateCompatibleBitmap(dcObj, w, h)
    cDC.SelectObject(dataBitMap)
    with gdi_context(dcObj, cDC, wDC, dataBitMap, hwnd):
        cDC.BitBlt((0,0),(w, h) , dcObj, (0,0), win32con.SRCCOPY)
        dataBitMap.SaveBitmapFile(cDC, file_name)

    return 

def check_for_ad(hwnd, rect):
    # Take a screencap of the window
    w = (rect[2] - rect[0]) // 2
    h = (rect[3] - rect[1]) // 2
    check_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad_check_screencap")
    wDC = win32gui.GetWindowDC(hwnd)
    dcObj=win32ui.CreateDCFromHandle(wDC)
    cDC=dcObj.CreateCompatibleDC()
    dataBitMap = win32ui.CreateBitmap()
    dataBitMap.CreateCompatibleBitmap(dcObj, w, h)
    cDC.SelectObject(dataBitMap)
    with gdi_context(dcObj, cDC, wDC, dataBitMap, hwnd):
        cDC.BitBlt((0,0),(w, h) , dcObj, (0,h), win32con.SRCCOPY)
        dataBitMap.SaveBitmapFile(cDC, os.path.join(check_folder, "screencap.png"))

    # check the screencap for a "Sponsored" badge
    image = cv2.imread(os.path.join(check_folder, "screencap.png"))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, thresholded = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY +cv2.THRESH_OTSU)
    result = reader.readtext(thresholded)
    # Close the image to prevent memory leaks
    cv2.destroyAllWindows()
    #iterate through the result and check for "Sponsored"
    for detection in result:
        if any([fuzz.ratio(detection[1], flag) > 78 for flag in sponsored_flags]):
            return True
    return False

def capture_bluestacks_ad(hwnd, output_dir, rect, data_lock, job_queue):
    try:
        # create folder for the screenshots
        date_time = time.strftime("%Y%m%dat%H%M%S")
        folder_name = f"{date_time}_{str(monitoring_data['ads_found'])}_{monitoring_data['demographic']}_{monitoring_data['location']}"
        ad_folder = os.path.join(output_dir, folder_name)
        os.mkdir(ad_folder)

        start_time = time.time()
        end_time = start_time + 20
        screenshot_count = 0
        while time.time() < end_time:
            screenshot_count += 1
            screenshot_timer = time.time()
            take_screencaps(hwnd, rect, f"{ad_folder}/{screenshot_count}.png")
            screenshot_time = time.time() - screenshot_timer
            if screenshot_time < 0.05:
                wait_time = 0.05 - screenshot_time
                time.sleep(wait_time)
        
        # Submit the job to the processing queue
        with data_lock:
            monitoring_data["ads_this_log"] += 1
            monitoring_data["ads_found"] += 1
            job_queue.put({
                "folder_name": folder_name,
                "found_at_time": date_time,
                "fps": screenshot_count / 20,
                "found_at_index": monitoring_data["data_index"],
                "demographic": monitoring_data["demographic"],
                "location": monitoring_data["location"]
            })
        scroll_down(hwnd, rect)
    except Exception as e:
        print(e)
        print("An error occurred while capturing the ad. Exiting...")
        traceback.print_exc()
        quit(1)

# TODO we need to process this later for things like the Caption text and the length
def process_bluestacks_ad(stop_event, error_event, keystroke_event, job_queue, output_dir, s3_client):
    while not stop_event.is_set() and not error_event.is_set() and not keystroke_event.is_set():
        try:
            ad_info = job_queue.get(block=True, timeout=30)
            ad_id = f"{ad_info['found_at_time']}_{ad_info['demographic']}_{ad_info['location']}"
            video_path = stitch_video(os.path.join(output_dir, ad_info["folder_name"]), ad_info["fps"], ad_id)
            # Upload the video to S3
            s3_client = boto3.client('s3')
            s3_client.upload_file(video_path, os.environ.get("S3_Bucket"), f"{ad_id}.mp4")
            pandas.DataFrame({
                "Ad ID": [ad_id],
                "Demographic": [ad_info["demographic"]],
                "Location": [ad_info["location"]],
                "FPS": [ad_info["fps"]],
                "Found At Index": [ad_info["found_at_index"]]
            }).to_csv(os.path.join(output_dir, "Data Key.csv"), mode='a', header=False, index=False)
            print(f"Ad {ad_id} processed and uploaded to S3. Removing it from the disk.")
            shutil.rmtree(os.path.join(output_dir, ad_info["folder_name"]))

        except queue.Empty:
            continue
            
        except Exception as e:
            print(e)
            print("An error occurred while processing the ad. Exiting...")
            traceback.print_exc()
            error_event.set()
            break

def trim_mem():
    print("Trimming memory")
    # Press and hold Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    # Press and hold Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
    # Press T
    win32api.keybd_event(0x54, 0, 0, 0)  # 0x54 is the virtual key code for 'T'
    # Release T
    win32api.keybd_event(0x54, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

def refresh_feed():
    print("Refreshing feed")
    # Press and hold Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    # Press and hold Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
    # Press 2
    win32api.keybd_event(0x32, 0, 0, 0)  # 0x32 is the virtual key code for '2'
    # Release 2
    win32api.keybd_event(0x32, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

# Test code below
if __name__ == '__main__':
    hwnd = find_bluestacks_window("BlueStacks App Player 13")
    rect = win32gui.GetWindowRect(hwnd)
    scroll_down(hwnd, rect)
    refresh_feed()
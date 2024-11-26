# Python Standard Library Dependencies
import datetime
import time
import os
import io
import shutil

# External Dependency Imports
from selenium.webdriver.common.by import By
import cv2
from bs4 import BeautifulSoup
import pandas
import boto3
from dotenv import load_dotenv

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data
from tiktok_scraper.Core.selenium_utils import next_video

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def process_ad(driver, output_dir, data_lock, job_queue):
    # Create folder for the screenshots
    folder_name = f"{str(monitoring_data['ads_found'])}_{monitoring_data['demographic']}_{monitoring_data['location']}"
    ad_folder = os.path.join(output_dir, folder_name)
    os.mkdir(os.path.join(output_dir, folder_name))

    # Check the length of the ad, and start timer
    tiktok_element = driver.find_element(By.ID, f"one-column-item-{monitoring_data['data_index']}")
    tiktok_length = "00:00 / 00:00"
    while tiktok_length == "00:00 / 00:00":
        tiktok_element = driver.find_element(By.ID, f"one-column-item-{monitoring_data['data_index']}")
        tiktok_length = tiktok_element.find_element(By.XPATH, ".//p[contains(@class, 'StyledTUXText')]").get_attribute("innerText")
    tiktok_length = tiktok_length.split("/")[1].strip()
    tiktok_length = (int(tiktok_length.split(":")[0]) * 60) + int(tiktok_length.split(":")[1])
    end_time = datetime.datetime.now() + datetime.timedelta(seconds=tiktok_length)

    # take screenshots of the ad until timer is over
    screenshot_count = 0
    while datetime.datetime.now() < end_time:
        tiktok_element.screenshot(f"{ad_folder}/{screenshot_count}.png")
        screenshot_count += 1
    fps = screenshot_count / tiktok_length
    with data_lock:
        monitoring_data["ads_this_log"] += 1
        monitoring_data["ads_found"] += 1
        job_queue.put({
            "folder_name": folder_name,
            "page_dom": BeautifulSoup(driver.find_element(By.XPATH, f"//article[@data-index='{monitoring_data['data_index']}']").get_attribute("outerHTML"), "html.parser"),
            "fps": fps,
            "found_at_index": monitoring_data["data_index"],
            "demographic": monitoring_data["demographic"],
            "location": monitoring_data["location"],
            "ad_length": tiktok_length
        })
    next_video(driver)

def stitch_video(folder_name, fps, ad_id):
    images = [img for img in os.listdir(folder_name) if img.endswith(".png")]
    images.sort(key=lambda x: int(x.split(".")[0]))
    video_name = f"{folder_name}/{ad_id}.mp4"
    try:
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        frame = cv2.imread(os.path.join(folder_name, images[0]))
        height, width, layers = frame.shape
        video = cv2.VideoWriter(video_name, fourcc, fps, (width, height))
        for image in images:
            video.write(cv2.imread(os.path.join(folder_name, image)))
        cv2.destroyAllWindows()
        video.release()
        return(video_name)
    except Exception as e:
        print(e)
        print("Error creating video. Please check the images in the folder.")
        return
    
# Function to save ad data in csv key, upload video to S3, and clean up output folder after upload
def finish_processing(stop_event, error_event, job_queue, output_dir, s3_client):
    while not stop_event.is_set() and not error_event.is_set():
        try:
            ad_info = job_queue.get(block=True, timeout=30)
            ad_id = f"{datetime.datetime.now().strftime('%Y%m%dat%H%M%S')}-{ad_info['demographic']}-{ad_info['location']}"
            cta_link = ad_info["page_dom"].find("a").get("href")
            caption = ad_info["page_dom"].find("h1", {"data-e2e":"video-desc"}).get_text()
            posting_account = ad_info["page_dom"].find("h3", {"data-e2e":"video-author-uniqueid"}).get_text()
            pandas.DataFrame({
                "Ad ID": [ad_id],
                "Found at Index": [ad_info["found_at_index"]],
                "Demographic": [ad_info["demographic"]],
                "Location": [ad_info["location"]],
                "Ad Length": [ad_info["ad_length"]],
                "CTA Link": [cta_link],
                "Caption": [caption],
                "Posting Account": [posting_account]
            }).to_csv(os.path.join(output_dir, "Data Key.csv"), mode='a', header=False, index=False)
            video_path = stitch_video(os.path.join(output_dir, ad_info["folder_name"]), ad_info["fps"], ad_id)
            s3_client = boto3.client("s3")
            s3_client.upload_file(video_path, os.environ.get("S3_BUCKET"), f"{ad_id}.mp4")
            shutil.rmtree(os.path.join(output_dir, ad_info["folder_name"]))
            print(f"Ad {ad_id} processed, uploaded to S3, and removed from disk.")
        except Exception as e:
            print(e)
            continue

# Test code below
if __name__ == '__main__':
    pass
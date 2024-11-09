# Python Standard Library Dependencies
import datetime
import time
import os
import io

# External Dependency Imports
from selenium.webdriver.common.by import By
import cv2

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data
from tiktok_scraper.Core.selenium_utils import next_video

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def process_ad(driver, output_dir):
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
    print(f"Ad length: {tiktok_length} seconds")

    # take screenshots of the ad (10x per second) until timer is over
    screenshot_count = 0
    while datetime.datetime.now() < end_time:
        tiktok_element.screenshot(f"{ad_folder}/{screenshot_count}.png")
        screenshot_count += 1
    print(f"Total screenshots taken: {screenshot_count} for an ad length of {tiktok_length} seconds.")
    fps = screenshot_count / tiktok_length
    stitch_video(ad_folder, fps)
    # Piece together screenshots into a video   
    # Save the video to disk
    # If video is too big, push to cloud storage (S3? Google Drive?)
        # TODO - Check to see how big these videos are,
        # TODO - separate the putting together of video and saving to disk and pushing it elsewhere into separate threads
    next_video(driver)

def stitch_video(folder_name, fps):
    images = [img for img in os.listdir(folder_name) if img.endswith(".png")]
    images.sort(key=lambda x: int(x.split(".")[0]))
    video_name = f"{folder_name}/{folder_name.split('/')[-1]}.mp4"
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    frame = cv2.imread(os.path.join(folder_name, images[0]))
    height, width, layers = frame.shape
    video = cv2.VideoWriter(video_name, fourcc, fps, (width, height))
    for image in images:
        video.write(cv2.imread(os.path.join(folder_name, image)))
    cv2.destroyAllWindows()
    video.release()

# Test code below
if __name__ == '__main__':
    stitch_video("/home/spotonspenny/Desktop/TikTok_Scraper/src/Output/1_18m_calgary", 3)
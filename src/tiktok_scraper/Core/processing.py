# Python Standard Library Dependencies
import time

# External Dependency Imports
from selenium.webdriver.common.by import By

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data
from tiktok_scraper.Core.selenium_utils import next_video

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def process_ad(driver):
    # Check the length of the ad, and start timer
    tiktok_element = driver.find_element(By.ID, f"one-column-item-{monitoring_data['data_index']}")
    print(monitoring_data['data_index'])
    tiktok_length = "00:00 / 00:00"
    while tiktok_length == "00:00 / 00:00":
        tiktok_element = driver.find_element(By.ID, f"one-column-item-{monitoring_data['data_index']}")
        tiktok_length = tiktok_element.find_element(By.XPATH, "//p[contains(@class, 'StyledTUXText')]")
        print(tiktok_length.get_attribute("outerHTML"))
        tiktok_length = tiktok_length.get_attribute("innerText")
    print("..." + tiktok_length + "...")
    # Save screenshots of the ad (10x per second) until timer is over
    # Piece together screenshots into a video   
    # Save the video to disk
    # If video is too big, push to cloud storage (S3? Google Drive?)
        # TODO - Check to see how big these videos are,
        # TODO - separate the putting together of video and saving to disk and pushing it elsewhere into separate thread
    #
    time.sleep(5) # Placeholder, remove once we have the ad length and wait that long instead
    next_video(driver)


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
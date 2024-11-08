# Python Standard Library Dependencies
import datetime

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
    tiktok_length = "00:00 / 00:00"
    while tiktok_length == "00:00 / 00:00":
        tiktok_element = driver.find_element(By.ID, f"one-column-item-{monitoring_data['data_index']}")
        tiktok_length = tiktok_element.find_element(By.XPATH, ".//p[contains(@class, 'StyledTUXText')]").get_attribute("innerText")
        print(tiktok_length)
    tiktok_length = tiktok_length.split("/")[1].strip()
    print(tiktok_length)
    tiktok_length = (int(tiktok_length.split(":")[0]) * 60) + int(tiktok_length.split(":")[1])
    end_time = datetime.datetime.now() + datetime.timedelta(seconds=tiktok_length)
    print(f"Ad length: {tiktok_length} seconds")
    while datetime.datetime.now() < end_time:
        pass
        # Save screenshots of the ad (10x per second) until timer is over
    # Piece together screenshots into a video   
    # Save the video to disk
    # If video is too big, push to cloud storage (S3? Google Drive?)
        # TODO - Check to see how big these videos are,
        # TODO - separate the putting together of video and saving to disk and pushing it elsewhere into separate threads
    next_video(driver)


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
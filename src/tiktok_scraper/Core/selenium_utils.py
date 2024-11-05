# Python Standard Library Dependencies
import os
import platform
import time

# External Dependency Imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from dotenv import load_dotenv

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data

#######################################################################################
#                                        Notes:                                       #
#                                                                                     #
#######################################################################################

# Open Chrome and navigate to TikTok
def start_webdriver():
    load_dotenv()
    # Find the chromedriver according to the OS in use
    this_file_path = os.path.abspath(__file__)
    os_in_use = platform.system().lower()
    chromedrivers = os.listdir(os.path.join(os.path.dirname(os.path.dirname(this_file_path)), "Chromedrivers"))
    chromedriver_path = None
    for file in chromedrivers:
        if os_in_use in file:
            chromedriver_path = os.path.join(os.path.dirname(os.path.dirname(this_file_path)), "Chromedrivers", file)
    if chromedriver_path is None:
        raise Exception("Sorry! We currently do not support {os_in_use}.")
    # Start the webdriver
    profile_dir = os.environ.get("PROFILE_PATH", None)
    if not profile_dir:
        raise Exception("No profile path found in the environment variables, please set one in the .env file and try again.")
    service = Service(executable_path=chromedriver_path)
    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument(r"--profile-directory=Default")
    options.add_argument("--log-level=3")
    driver = webdriver.Chrome(service=service, options=options)
    return driver

def search_for_ad(driver):
    global monitoring_data
    ad_found = False
    while ad_found is False:
        if check_for_ad(driver, monitoring_data["data_index"]):
            print("Ad found at index: " + str(monitoring_data["data_index"]))
            ad_found = True
        else:
            print("No ad found at index: " + str(monitoring_data["data_index"]))
            monitoring_data["data_index"] += 1
            time.sleep(5)
            ActionChains(driver).key_down(Keys.ARROW_DOWN).key_up(Keys.ARROW_DOWN).perform()

def check_for_ad(driver, data_index):
    tiktok = driver.find_element(By.ID, f"one-column-item-{data_index}")
    try:
        tiktok.find_element(By.XPATH, ".//*[contains(text(), 'ponsored')]")
        return True
    except:
        return False

# Test code below
if __name__ == '__main__':
    start_webdriver()
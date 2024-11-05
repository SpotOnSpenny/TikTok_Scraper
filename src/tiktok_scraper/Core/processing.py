# Python Standard Library Dependencies
import time

# External Dependency Imports
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def process_ad(driver):
    global monitoring_data
    time.sleep(5)
    ActionChains(driver).key_down(Keys.ARROW_DOWN).key_up(Keys.ARROW_DOWN).perform()
    monitoring_data["data_index"] += 1


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
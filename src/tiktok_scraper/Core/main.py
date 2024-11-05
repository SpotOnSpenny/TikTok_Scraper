# Python Standard Library Dependencies
import datetime

# External Dependency Imports


# Internal Dependency Imports
from tiktok_scraper.Core.selenium_utils import start_webdriver, search_for_ad
from tiktok_scraper.Core.processing import process_ad

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def scrape_tiktok():
    # Initialize the scraper with user input
    valid_time = False
    while not valid_time:
        input_time = input("Please enter the time in hours you would like to scrape TikTok for ads: ")
        if input_time.isdigit() and int(input_time) > 0:
            valid_time = True
            input_time = int(input_time)
        else:
            print("Invalid time entered, please enter a valid number of hours as a digit.")
    end_time = datetime.datetime.now() + datetime.timedelta(hours=input_time)

    # Start the webdriver and allow the user to login
    try:
        driver = start_webdriver()
    except Exception as e:
        print(e)
        quit(1)
    vpn_inted = False
    while vpn_inted is False:
        vpn = input("Please ensure proper connection to the VPN, enter the location, and hit enter to continue.")
        if vpn.lower() in ["halifax", "calgary", "toronto", "vancouver"]:
            vpn_inted = True
        else:
            print("Location is not a valid Canadian city to be scraped, please enter a valid location.")
    driver.get("https://www.tiktok.com/")
    demographic_logged_in = False
    while demographic_logged_in is False:
        demographic = input("Please log in with a TikTok account, enter the demographic of the account, and hit enter to continue.")
        if demographic.upper() in ["4M", "4F", "7F", "8M", "9F", "10M", "18M", "18F"]:
            print("Scraping TikTok under the demographic: " + demographic)
            demographic_logged_in = True
        else:
            print("Invalid demographic entered, please enter a relevant demographic.")
    
    # Recursively scroll TikTok looking for ads until the designated time is up
    while datetime.datetime.now() < end_time:
        search_for_ad(driver)
        process_ad(driver)



# Test code below
if __name__ == '__main__':
    scrape_tiktok()
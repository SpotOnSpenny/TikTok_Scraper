# Python Standard Library Dependencies
import datetime
import os
import threading
import queue

# External Dependency Imports
import pandas
import boto3
from dotenv import load_dotenv

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data
from tiktok_scraper.Core.selenium_utils import start_webdriver, search_for_ad
from tiktok_scraper.Core.processing import process_ad, finish_processing
from tiktok_scraper.Core.logger import start_logging
from tiktok_scraper.Core.bluestacks import process_bluestacks_ad, find_bluestacks_ad, find_bluestacks_window, capture_bluestacks_ad

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def scrape_tiktok(interface="web"):
    # TODO - add support for custom output directory via environment variable
    # TODO - make console portion of CLI prettier and more user friendly
    # TODO - add better error handling for critical errors to log properly
    # TODO - catch the KeyboardInterrupt exception and close the webdriver and threads properly

    # Load the environment variables
    load_dotenv()

    # Check for output dirs and create them if non_existent
    src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    output_dir = os.path.join(src_dir, "Output")
    if not os.path.exists(output_dir):
        os.mkdir(output_dir)
    else:
        print("Output directory already exists! Please clear the directory before running the scraper.")
        quit(1)

    # Initialize the queue and spreadsheet for processing ads, and s3 client for uploading videos
    processing_queue = queue.Queue()
    data_key = pandas.DataFrame(columns=["Ad ID", "Found at Index", "Demographic", "Location", "Ad Length", "CTA Link", "Caption", "Posting Account"])
    data_key.to_csv(os.path.join(output_dir, "Data Key.csv"), index=False)
    s3_client = boto3.client("s3")

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
    if interface == "web":
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
    if interface == "web":
        driver.get("https://www.tiktok.com/")
    elif interface == "mobile":
        print("Please open your emulator of choice and open the TikTok app.")
        hwnd = None
        while hwnd is None:
            window_name = input("Please enter the name of the window that contains the TikTok app: ")
            hwnd = find_bluestacks_window(window_name)
    demographic_logged_in = False
    while demographic_logged_in is False:
        demographic = input("Please log in with a TikTok account, enter the demographic of the account, and hit enter to continue.")
        if demographic.upper() in ["4M", "4F", "7F", "6M", "9F", "10M", "18M", "18F"]:
            print("Scraping TikTok under the demographic: " + demographic)
            demographic_logged_in = True
        else:
            print("Invalid demographic entered, please enter a relevant demographic.")
    
    # Update the global variables with the demographic and location
    monitoring_data["demographic"] = demographic
    monitoring_data["location"] = vpn
    data_lock = threading.Lock()
    stop_event = threading.Event()
    error_event = threading.Event()
    keystroke_event = threading.Event()

    # Define and start the logging and scraping threads
    try:
        logging_thread = threading.Thread(target=start_logging, args = (f"{demographic}_{vpn}", stop_event, error_event, keystroke_event, data_lock))
        logging_thread.start()
    except Exception as e:
        print(e)
        print("An error occurred while starting the logger. Exiting...")
        quit(1)

    # Define and start the processing thread
    try:
        if interface == "web":
            processing_thread = threading.Thread(target=finish_processing, args = (stop_event, error_event, processing_queue, output_dir, s3_client))
        elif interface == "mobile":
            processing_thread = threading.Thread(target=process_bluestacks_ad, args = (stop_event, error_event, keystroke_event, processing_queue, output_dir, s3_client))
        processing_thread.start()
    except Exception as e:
        print(e)
        print("An error occurred while starting the processing thread. Exiting...")
        error_event.set()
        logging_thread.join()
        quit(1)

    # Recursively scroll TikTok looking for ads until the designated time is up
    try:
        while datetime.datetime.now() < end_time:
            if interface == "web":
                search_for_ad(driver)
                process_ad(driver, output_dir, data_lock, processing_queue)
            elif interface == "mobile":
                rect = find_bluestacks_ad(hwnd)
                capture_bluestacks_ad(hwnd, output_dir, rect, data_lock, processing_queue)
    except KeyboardInterrupt:
        print("Keyboard interrupt detected. Exiting...")
        keystroke_event.set()
        processing_thread.join()
        logging_thread.join()
        quit(0)
    except Exception as e:
        print(e)
        print("An error occurred while scraping TikTok. Exiting...")
        error_event.set()
        logging_thread.join()
        processing_thread.join()
        quit(1)

    # Stop the logging thread and close the webdriver
    stop_event.set()
    logging_thread.join()
    if interface == "web":
        driver.quit()
    processing_thread.join()
    print("Scraping complete. Please check the Output directory for the scraped ads.")



# Test code below
if __name__ == '__main__':
    interface = input("Please enter the interface you would like to use (web/mobile): ")
    scrape_tiktok(interface)
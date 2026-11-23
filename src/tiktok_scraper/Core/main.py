# Python Standard Library Dependencies
import datetime
import json
import os
import re
import subprocess
import urllib.request
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
from tiktok_scraper.Core import collector
from tiktok_scraper.Core.collector import find_ad, capture_ad, process_ads
from tiktok_scraper.Core.impression import ImpressionLog
from tiktok_scraper.Core.phone import choose_phone

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def clean_location(text):
    # "Red Deer" -> "red-deer": lower case, hyphens for anything that is not a letter or number,
    # since the location goes into folder and file names where underscores separate the parts
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")

def public_network():
    # Where the internet sees this connection: {"ip", "city", "region", "country", "org", ...} or None
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=10) as response:
            details = json.loads(response.read().decode())
        return {key: details.get(key) for key in ["ip", "city", "region", "country", "org", "timezone"]}
    except Exception:
        return None

def current_commit(repo_dir):
    # The commit of the scraper that is running, for the session record
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, timeout=10)
        return result.stdout.decode().strip() or None
    except Exception:
        return None

def scrape_tiktok(interface="web"):
    # interface is "web" (browser), "mobile" (BlueStacks window) or "phone" (Android phone over adb)
    if interface not in ["web", "mobile", "phone"]:
        print("Interface must be web, mobile or phone.")
        quit(1)
    app_interface = interface in ["mobile", "phone"]

    # TODO - add support for custom output directory via environment variable
    # TODO - make console portion of CLI prettier and more user friendly
    # TODO - add better error handling for critical errors to log properly
    # TODO - catch the KeyboardInterrupt exception and close the webdriver and threads properly

    # Load the environment variables
    load_dotenv()
    collector.configure_from_environment()

    # Check for output dirs and create them if non_existent
    src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    output_dir = os.path.join(src_dir, "Output")
    if app_interface:
        # Each app session keeps everything in its own folder inside Output, so earlier sessions can stay
        # where they are and several phones can run at once
        os.makedirs(output_dir, exist_ok=True)
    elif not os.path.exists(output_dir):
        os.mkdir(output_dir)
    else:
        print("Output directory already exists! Please clear the directory before running the scraper.")
        quit(1)

    # Initialize the queue and spreadsheet for processing ads, and s3 client for uploading videos
    processing_queue = queue.Queue()
    if interface == "web":
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
    # Collection runs on the local connection: through a VPN TikTok serves no ads. The location is where
    # the collector physically is, and the public IP address is looked up so the record does not rest on typing.
    network = public_network()
    if network:
        print(f"This connection's public address is in {network.get('city')}, {network.get('region')}, {network.get('country')} ({network.get('org')}).")
        print("If that is a VPN or not where you are, stop and switch the VPN off: TikTok serves no ads through one.")
    else:
        print("Could not look up this connection's public address; the session will record the typed location only.")
    vpn_inted = False
    while vpn_inted is False:
        vpn = clean_location(input("Please enter the city this collection is running from, and hit enter to continue."))
        if vpn != "":
            vpn_inted = True
        else:
            print("Please enter a location, using letters and numbers.")
    if interface == "web":
        driver.get("https://www.tiktok.com/")
    elif interface == "mobile":
        # Imported here because it needs Windows and the BlueStacks window
        from tiktok_scraper.Core.bluestacks import find_bluestacks_window, BlueStacksDevice
        print("Please open your emulator of choice and open the TikTok app.")
        hwnd = None
        while hwnd is None:
            window_name = input("Please enter the name of the window that contains the TikTok app: ")
            hwnd = find_bluestacks_window(window_name)
        device = BlueStacksDevice(hwnd)
    elif interface == "phone":
        print("Please plug in the phone with USB debugging switched on, unlock it and open the TikTok app.")
        device = None
        while device is None:
            input("Hit enter when the phone is ready.")
            device = choose_phone()
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

    # Start the session folder and the per-impression log: one row and one stored frame per video
    impression_log = None
    if app_interface:
        device.prepare()
        device_details = device.describe()
        session_id = f"{datetime.datetime.now():%Y%m%dat%H%M%S}_{demographic}_{vpn}"
        if device_details.get("serial"):
            session_id += "_" + device_details["serial"][-4:]
        session_dir = os.path.join(output_dir, session_id)
        os.mkdir(session_dir)
        pandas.DataFrame(columns=["Ad ID", "Demographic", "Location", "FPS", "Found At Index"]).to_csv(os.path.join(session_dir, "Data Key.csv"), index=False)
        with open(os.path.join(session_dir, "session.json"), "w", encoding="utf-8") as file:
            json.dump({
                "session_id": session_id,
                "started_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "planned_hours": input_time,
                "interface": interface,
                "demographic": demographic,
                "location": vpn,
                "network": network,
                "collector_commit": current_commit(src_dir),
                "screen_reader": collector.SCREEN_READER,
                "ocr_engine": collector.engine_name(),
                "reads_per_video": collector.READS_PER_VIDEO,
                "dwell_seconds": collector.DWELL_SECONDS,
                "device": device_details
            }, file, indent=1)
        impression_log = ImpressionLog(session_dir, session_id, {"demographic": demographic, "location": vpn})
        print("Session folder: " + session_dir)
        if collector.SCREEN_READER == "ui":
            print("Taking the on-screen text from the phone first (slow), and reading a screenshot where it gives none.")
        print(f"Reading the screen with {collector.engine_name()}: at least {collector.READS_PER_VIDEO} reads and {collector.DWELL_SECONDS} seconds per video.")

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
        else:
            processing_thread = threading.Thread(target=process_ads, args = (stop_event, error_event, keystroke_event, processing_queue, session_dir))
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
            else:
                find_ad(device, impression_log)
                capture_ad(device, session_dir, data_lock, processing_queue, impression_log)
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
    finally:
        # Write the row for the video still on screen, however the session ended
        if impression_log is not None:
            impression_log.close("session_end")
        if app_interface:
            try:
                device.close()
            except Exception as e:
                print(e)

    # Stop the logging thread and close the webdriver
    stop_event.set()
    logging_thread.join()
    if interface == "web":
        driver.quit()
    processing_thread.join()
    print("Scraping complete. Please check the Output directory for the scraped ads.")



# Test code below
if __name__ == '__main__':
    interface = input("Please enter the interface you would like to use (web/mobile/phone): ")
    scrape_tiktok(interface)
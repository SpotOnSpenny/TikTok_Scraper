# Python Standard Library Dependencies
import time

# External Dependency Imports
import logging
from logging.handlers import SysLogHandler
import os
from datetime import datetime

# Internal Dependency Imports
from tiktok_scraper.Core.global_vars import monitoring_data

#######################################################################################
#                                        Notes:                                       #
#######################################################################################

def start_logging(profile, stop_event, error_event, data_lock):
    # ----- Load env variables and use to instantiate remote logging -----
    try:
        papertrail_port = os.environ["PAPERTRAIL_PORT"]
        logging_level = os.environ["LOGGING_LEVEL"]
    except:
        raise Exception("Missing environment variables. Please ensure PAPERTRAIL_PORT and LOGGING_LEVEL are set.")
    logger = logging.getLogger()
    try:
        syslog = SysLogHandler(address=("logs.papertrailapp.com", int(papertrail_port)))
        format = "%(asctime)s | {} | %(levelname)s - %(message)s".format(
            profile
        )
        formatter = logging.Formatter(format, datefmt="%Y-%m-%d %H:%M:%S")
        syslog.setFormatter(formatter)
        logger.addHandler(syslog)
    except Exception as e:
        print(e)
    match logging_level.lower():
        case "debug":
            logger.setLevel(logging.DEBUG)
        case "info":
            logger.setLevel(logging.INFO)
        case "warn":
            logger.setLevel(logging.WARN)
        case "error":
            logger.setLevel(logging.ERROR)
        case "critical":
            logger.setLevel(logging.CRITICAL)
        case _:
            print(
                f"The specified log level of {logging_level} was invalid. Please use 'debug', 'info', 'warn', 'error' or 'critical'."
            )
    logger.info(f"Logging started for {profile} at {datetime.now()}")
    
    # Start logging and chekc if the stop event has been set every 5 seconds
    while not stop_event.is_set() and not error_event.is_set():
        for _ in range(180):
            if stop_event.is_set():
                break
            time.sleep(5)
        with data_lock:
            logger.info(f"{monitoring_data['ads_this_log']} ads found in the last 15 minutes. Total ads found: {monitoring_data['ads_found']} in {monitoring_data['videos_watched']} videos.")
            monitoring_data["ads_this_log"] = 0
    if error_event.is_set():
        logger.error("An error occurred during the scraping process. Exiting program, check console output for error.")
        logger.info(f"Prior to error, we found {monitoring_data['ads_found']} ads in {monitoring_data['videos_watched']} videos.")
    else:
        logger.info(f"Scraping process completed, found {monitoring_data['ads_found']} ads in {monitoring_data['videos_watched']} videos. Exiting program.")


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code
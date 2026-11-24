# Python Standard Library Dependencies
import _thread
import argparse
import builtins
import collections
import glob
import json
import os
import statistics
import sys
import threading

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# A short, hands-off test run of the collector on a connected phone, followed by a summary
# of what it logged. It answers the collector's questions itself and stops after the given
# number of minutes, the same way Ctrl+C would. Recorded ads are kept on this computer and
# NOT uploaded to S3 unless --upload is given.
#
#   python tools/phone_test.py                      (3 minutes)
#   python tools/phone_test.py --minutes 10 --reads 1 --dwell 0
#   python tools/phone_test.py --summary-only       (summarise the latest session again)
#
# Run it from the repository root, with TikTok open on the phone's For You feed.

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR = os.path.join(REPO_DIR, "src")
OUTPUT_DIR = os.path.join(SOURCE_DIR, "Output")


def latest_session():
    sessions = sorted(glob.glob(os.path.join(OUTPUT_DIR, "*", "session.json")), key=os.path.getmtime)
    return os.path.dirname(sessions[-1]) if sessions else None


def summarise(session_dir):
    with open(os.path.join(session_dir, "session.json"), encoding="utf-8") as file:
        session = json.load(file)
    log_path = os.path.join(session_dir, "impressions.jsonl")
    rows = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as file:
            rows = [json.loads(line) for line in file]
    device = session.get("device", {})
    print("\n" + "=" * 78)
    print(f"Session {session['session_id']}")
    print(f"  {device.get('model')} (Android {device.get('android_version')}), TikTok {device.get('tiktok_version')}")
    print(f"  reader {session.get('screen_reader')} / {session.get('ocr_engine')}, {session.get('reads_per_video')} reads, {session.get('dwell_seconds')} s dwell, uploads {'on' if session.get('uploads', True) else 'off'}")
    if not rows:
        print("  No videos were logged.")
        return
    ads = [row for row in rows if row["is_ad"]]
    others = [row for row in rows if not row["is_ad"] and row["close_reason"] == "advanced"]
    print(f"  {len(rows)} videos, {len(ads)} ads ({', '.join(sorted(collections.Counter(row['matched_label'] for row in ads)))})" if ads else f"  {len(rows)} videos, 0 ads")
    print(f"  failed swipes retried: {sum(max(0, row['swipe_attempts'] - 1) for row in rows)}; unconfirmed videos: {sum(not row['advance_confirmed'] for row in rows)}; stuck-feed refreshes: {sum(row['stuck_refreshes'] for row in rows)}")
    print(f"  read time: median {statistics.median(row['ocr_ms'] for row in rows) / 1000:.2f} s; text from {dict(collections.Counter(row['text_source'] for row in rows))}")
    if others:
        print(f"  ordinary video on screen: median {statistics.median(row['dwell_s'] for row in others):.1f} s, {statistics.median(row['reads'] for row in others):.0f} reads")
    late = [row for row in ads if row["label_read"] and row["label_read"] > 1]
    if late:
        print(f"  ads caught on a later read: {[row['index'] for row in late]}")
    print("  ads:")
    for row in ads:
        print(f"    #{row['index']:<4} {row['matched_label']:<15} {(row['account'] or '')[:28]:<28} {(row['caption'] or '')[:50]}")
    recordings = glob.glob(os.path.join(session_dir, "*", "ad.mp4"))
    print(f"  recordings kept in the session folder: {len(recordings)}")
    print(f"  folder: {session_dir}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Short test run of the collector on a connected phone")
    parser.add_argument("--minutes", type=float, default=3)
    parser.add_argument("--city", default="calgary")
    parser.add_argument("--demographic", default="18M")
    parser.add_argument("--reads", type=int, default=None, help="reads per video (default: the setting in src/.env, else 2)")
    parser.add_argument("--dwell", type=float, default=None, help="seconds each video is watched (default: the setting in src/.env, else 5)")
    parser.add_argument("--engine", choices=["rapidocr", "easyocr"], default=None)
    parser.add_argument("--reader", choices=["ocr", "ui"], default=None)
    parser.add_argument("--upload", action="store_true", help="upload recorded ads to S3 (off by default for tests)")
    parser.add_argument("--summary-only", action="store_true", help="only summarise the latest session")
    args = parser.parse_args()

    if not args.summary_only:
        os.chdir(SOURCE_DIR)
        sys.path.insert(0, SOURCE_DIR)
        from dotenv import load_dotenv
        load_dotenv(os.path.join(SOURCE_DIR, ".env"))
        # Settings given here win over src/.env for this run only
        for name, value in [("READS_PER_VIDEO", args.reads), ("DWELL_SECONDS", args.dwell), ("OCR_ENGINE", args.engine), ("SCREEN_READER", args.reader)]:
            if value is not None:
                os.environ[name] = str(value)
        if not args.upload:
            os.environ["UPLOAD_ADS"] = "0"

        # Check there is a phone to drive before starting, so a missing phone is a plain message
        from tiktok_scraper.Core import phone
        ready = [serial for serial, state in phone.list_phones() if state == "device"]
        if not ready:
            waiting = [f"{serial} ({state})" for serial, state in phone.list_phones()]
            print("No phone is ready. Plug it in with USB debugging on and accept the 'Allow USB debugging' prompt.")
            if waiting:
                print("Seen but not ready: " + ", ".join(waiting))
            sys.exit(1)

        # Answer the collector's questions: hours, city, "is the phone ready", demographic.
        # A question about which phone to use (when several are plugged in) is passed to the person.
        answers = collections.deque(["1", args.city, "", args.demographic])
        ask = builtins.input

        def answer(prompt=""):
            if "Which phone" in prompt:
                return ask(prompt)
            reply = answers.popleft() if answers else ""
            print(f"{prompt}{reply}")
            return reply
        builtins.input = answer

        # Stop the run the way Ctrl+C would, so the last row is written and everything shuts down cleanly
        timer = threading.Timer(args.minutes * 60, _thread.interrupt_main)
        timer.daemon = True
        timer.start()
        from tiktok_scraper.Core.main import scrape_tiktok
        try:
            scrape_tiktok("phone")
        except SystemExit:
            pass
        finally:
            timer.cancel()
            builtins.input = ask

    session = latest_session()
    if session is None:
        print("No session found in " + OUTPUT_DIR)
    else:
        summarise(session)

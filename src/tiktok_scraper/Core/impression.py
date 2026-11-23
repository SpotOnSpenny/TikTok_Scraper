# Python Standard Library Dependencies
import datetime
import json
import os
import time

# External Dependency Imports
from fuzzywuzzy import fuzz

# Internal Dependency Imports


#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# Everything here takes plain data (EasyOCR results as a list of (bbox, text, confidence))
# and must stay importable without win32, EasyOCR or BlueStacks, so it can be tested on
# any machine. Window capture and the swipe live in bluestacks.py.

# Ad-system labels only. Creator "Paid partnership" is deliberately not in the default list.
SPONSORED_FLAGS = ["Sponsored", "Paid Promotion", "Promoted Music"]
# A label matches when fuzz.ratio is strictly above this. Set on 240 frames of 80 app-captured ads
# (2026-11-23): every line scoring above 74 was a real label or a piece of one, such as a badge read
# together with the text beside it ("Sponsored CK my", 75), and nothing else scored above 69.
LABEL_CUTOFF = 74
# OCR detections below this confidence are left out of the account and caption text
TEXT_MIN_CONFIDENCE = 0.3
# The part of the window that is read: the lower half, without the right-hand rail of
# like/comment/share counts (the rightmost 15% of the width)
READ_WIDTH_SHARE = 0.85
# App furniture that is on screen for every video and so says nothing about which video it is.
# An exact match is dropped anywhere. A near match (OCR slips such as "Homes") is dropped only in
# the bottom strip of the read region, where the navigation bar sits, so that an account called
# "Homer" higher up is kept.
SCREEN_CHROME = ["Home", "Friends", "Inbox", "Profile", "See more"]
CHROME_CUTOFF = 80
NAV_BAR_START = 0.85
# Two reads are the same video when their whole text is at least this similar...
SAME_VIDEO_THRESHOLD = 65
# ...or when the lines they share (each at least this long and this similar) add up to at least
# SHARED_LINES_MIN_TOTAL characters. Shared caption lines survive text inside the video changing
# between reads; the total is there so that an account name alone is not enough, since two different
# videos from one account share it. Set on the app-captured S3 ad frames, 2026-11-23, and checked on
# a second set of 40 ads that was not used to choose the values.
SHARED_LINE_MIN_LENGTH = 15
SHARED_LINE_CUTOFF = 85
SHARED_LINES_MIN_TOTAL = 30


def read_region(image):
    # The slice of a full-window image that is given to the OCR. Used by the scraper and by
    # tools/make_fixtures.py so that fixtures always show what the scraper reads.
    height, width = image.shape[:2]
    return image[height // 2:(height // 2) * 2, 0:int(width * READ_WIDTH_SHARE)]


def match_label(results, flags=SPONSORED_FLAGS, cutoff=LABEL_CUTOFF):
    # Returns (flag, score, matched_text) for the best label match on screen.
    # flag is None when the best score is not above the cutoff.
    best_flag, best_score, best_text = None, 0, None
    for detection in results:
        for flag in flags:
            score = fuzz.ratio(detection[1], flag)
            if score > best_score:
                best_flag, best_score, best_text = flag, score, detection[1]
    if best_score > cutoff:
        return best_flag, best_score, best_text
    return None, best_score, best_text


def is_chrome(text, top, height):
    cleaned = text.lower().strip(" .:;,_-|")
    if any(cleaned == chrome.lower() for chrome in SCREEN_CHROME):
        return True
    if height and top >= height * NAV_BAR_START:
        return any(fuzz.ratio(cleaned, chrome.lower()) > CHROME_CUTOFF for chrome in SCREEN_CHROME)
    return False


def screen_text(results, height=None, flags=SPONSORED_FLAGS, cutoff=LABEL_CUTOFF, min_confidence=TEXT_MIN_CONFIDENCE):
    # Splits what was read into a best-effort account (top line) and caption (the rest).
    # Label matches, app furniture and low-confidence detections are dropped. height is the
    # height of the read region, used to find the navigation bar. Provisional until the
    # caption reader moves into this repository.
    kept = []
    for bbox, text, confidence in results:
        text = str(text).strip()
        if text == "" or confidence < min_confidence:
            continue
        if any(fuzz.ratio(text, flag) > cutoff for flag in flags):
            continue
        top = min(point[1] for point in bbox)
        left = min(point[0] for point in bbox)
        if is_chrome(text, top, height):
            continue
        kept.append((top, left, text))
    kept.sort()
    lines = [text for _, _, text in kept]
    return {
        "account": lines[0] if lines else "",
        "caption": " ".join(lines[1:]),
        "lines": lines
    }


def same_video(previous, current, threshold=SAME_VIDEO_THRESHOLD):
    # Compares the text of two reads. True: same video (the swipe did not advance).
    # False: a different video. None: neither read has any text, so it cannot be told.
    previous_text = " ".join(previous["lines"]).lower()
    current_text = " ".join(current["lines"]).lower()
    if previous_text == "" and current_text == "":
        return None
    if previous_text == "" or current_text == "":
        return False
    if fuzz.token_sort_ratio(previous_text, current_text) >= threshold:
        return True
    previous_lines = [line.lower() for line in previous["lines"] if len(line) >= SHARED_LINE_MIN_LENGTH]
    current_lines = [line.lower() for line in current["lines"] if len(line) >= SHARED_LINE_MIN_LENGTH]
    shared_characters = sum(
        len(previous_line) for previous_line in previous_lines
        if any(fuzz.ratio(previous_line, current_line) >= SHARED_LINE_CUTOFF for current_line in current_lines)
    )
    return shared_characters >= SHARED_LINES_MIN_TOTAL


def iso_time(timestamp):
    # Local time with UTC offset, to the millisecond
    if timestamp is None:
        return None
    return datetime.datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="milliseconds")


class ImpressionLog:
    # One JSON line per video in <session_dir>/impressions.jsonl. The video on screen is
    # held in memory and its row is written when the next read confirms it was left, or
    # when the session ends. Each row is flushed to disk as it is written.

    def __init__(self, session_dir, session_id, meta=None):
        self.session_dir = session_dir
        self.session_id = session_id
        self.meta = dict(meta or {})
        self.path = os.path.join(session_dir, "impressions.jsonl")
        self.frames_dir = os.path.join(session_dir, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.index = 0
        self.current = None

    def has_open(self):
        return self.current is not None

    def current_text(self):
        # The account/caption text of the video on screen, or None before the first video
        return self.current["text"] if self.current else None

    def next_frame_path(self):
        # Where the stored frame for the next impression should be saved
        return os.path.join(self.frames_dir, f"{self.index + 1:06d}.jpg")

    def last_swipe_time(self):
        # When the swipe that should bring the next video on screen was sent, or None
        if self.current is None or not self.current["swipes"]:
            return None
        return self.current["swipes"][-1]["at"]

    def open(self, shown_at, text, flag, score, matched_text, ocr_ms, scoring_ms, frame_path, advance_confirmed=True, first_read_at=None):
        # shown_at is when the video came on screen; first_read_at is when it was first read
        self.index += 1
        self.current = {
            "index": self.index,
            "shown_at": shown_at,
            "first_read_at": first_read_at if first_read_at is not None else shown_at,
            "text": text,
            "flag": flag,
            "score": score,
            "matched_text": matched_text,
            "ocr_ms": ocr_ms,
            "scoring_ms": scoring_ms,
            "frame_path": frame_path,
            "advance_confirmed": advance_confirmed,
            "reads": 1,
            "label_read": 1 if flag is not None else None,
            "stuck_refreshes": 0,
            "swipes": []
        }
        return self.index

    def is_ad(self):
        return self.current is not None and self.current["flag"] is not None

    def swipe_count(self):
        return len(self.current["swipes"]) if self.current else 0

    def note_read(self):
        # The video on screen was read again and found to be the same one
        if self.current is not None:
            self.current["reads"] += 1

    def mark_ad(self, flag, score, matched_text):
        # The label was not there on the first read of this video but is there now
        if self.current is not None:
            self.current["flag"] = flag
            self.current["score"] = score
            self.current["matched_text"] = matched_text
            self.current["label_read"] = self.current["reads"]

    def note_refresh(self):
        # The feed was refreshed because swipes kept failing on this video
        if self.current is not None:
            self.current["stuck_refreshes"] += 1

    def note_swipe(self, at, params):
        # Record a swipe command sent while this video was on screen
        if self.current is not None:
            self.current["swipes"].append({"at": at, "params": params})

    def close(self, reason="advanced", at=None):
        # Write the row for the video on screen. Returns the row, or None if nothing was open.
        if self.current is None:
            return None
        current = self.current
        self.current = None
        swipes = current["swipes"]
        # The video is left at the last swipe; with no swipe (session end) it is left now
        left_at = swipes[-1]["at"] if swipes else (at if at is not None else time.time())
        frame_path = current["frame_path"]
        if frame_path:
            frame_path = os.path.relpath(frame_path, self.session_dir).replace(os.sep, "/")
        row = {
            "session_id": self.session_id,
            "index": current["index"],
            "shown_at": iso_time(current["shown_at"]),
            "first_read_at": iso_time(current["first_read_at"]),
            "left_at": iso_time(left_at),
            "dwell_s": round(left_at - current["shown_at"], 3),
            "account": current["text"]["account"],
            "caption": current["text"]["caption"],
            "ocr_lines": current["text"]["lines"],
            "is_ad": current["flag"] is not None,
            "matched_label": current["flag"],
            "match_score": current["score"],
            "matched_text": current["matched_text"],
            "label_read": current["label_read"],
            "reads": current["reads"],
            "stuck_refreshes": current["stuck_refreshes"],
            "swipe": swipes[-1]["params"] if swipes else None,
            "swipe_attempts": len(swipes),
            "advance_confirmed": current["advance_confirmed"],
            "ocr_ms": current["ocr_ms"],
            "scoring_ms": current["scoring_ms"],
            "frame_path": frame_path,
            "close_reason": reason
        }
        row.update(self.meta)
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        return row


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code

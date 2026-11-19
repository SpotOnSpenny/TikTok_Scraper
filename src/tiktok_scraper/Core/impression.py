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
# A label matches when fuzz.ratio is strictly above this
LABEL_CUTOFF = 78
# OCR detections below this confidence are left out of the account and caption text
TEXT_MIN_CONFIDENCE = 0.3
# App furniture that is on screen for every video and so says nothing about which video it is
SCREEN_CHROME = ["Home", "Friends", "Inbox", "Profile", "See more"]
CHROME_CUTOFF = 80
# Detections starting right of this share of the width are the like/comment/share counts
RIGHT_RAIL_START = 0.85
# Two reads are the same video when their whole text is at least this similar...
SAME_VIDEO_THRESHOLD = 70
# ...or when they share a line this long that is this similar (a caption line survives
# text inside the video changing between reads). Set on the S3 ad frames, 2026-11-19.
SHARED_LINE_MIN_LENGTH = 15
SHARED_LINE_CUTOFF = 85


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


def screen_text(results, width=None, flags=SPONSORED_FLAGS, cutoff=LABEL_CUTOFF, min_confidence=TEXT_MIN_CONFIDENCE):
    # Splits what was read into a best-effort account (top line) and caption (the rest).
    # Label matches, app furniture, the right-hand count rail (when the width of the read
    # region is given) and low-confidence detections are dropped. Provisional until the
    # caption reader moves into this repository.
    kept = []
    for bbox, text, confidence in results:
        text = str(text).strip()
        if text == "" or confidence < min_confidence:
            continue
        if any(fuzz.ratio(text, flag) > cutoff for flag in flags):
            continue
        if any(fuzz.ratio(text.lower(), chrome.lower()) > CHROME_CUTOFF for chrome in SCREEN_CHROME):
            continue
        top = min(point[1] for point in bbox)
        left = min(point[0] for point in bbox)
        if width and left >= width * RIGHT_RAIL_START:
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
    return any(
        fuzz.ratio(previous_line, current_line) >= SHARED_LINE_CUTOFF
        for previous_line in previous_lines for current_line in current_lines
    )


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

    def open(self, shown_at, text, flag, score, matched_text, ocr_ms, scoring_ms, frame_path, advance_confirmed=True):
        self.index += 1
        self.current = {
            "index": self.index,
            "shown_at": shown_at,
            "text": text,
            "flag": flag,
            "score": score,
            "matched_text": matched_text,
            "ocr_ms": ocr_ms,
            "scoring_ms": scoring_ms,
            "frame_path": frame_path,
            "advance_confirmed": advance_confirmed,
            "swipes": []
        }
        return self.index

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
            "left_at": iso_time(left_at),
            "dwell_s": round(left_at - current["shown_at"], 3),
            "account": current["text"]["account"],
            "caption": current["text"]["caption"],
            "ocr_lines": current["text"]["lines"],
            "is_ad": current["flag"] is not None,
            "matched_label": current["flag"],
            "match_score": current["score"],
            "matched_text": current["matched_text"],
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

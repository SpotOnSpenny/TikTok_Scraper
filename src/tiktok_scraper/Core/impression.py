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
# The current app (seen on a phone, 2026-11-23) marks ads with a small grey "Ad" badge at the left
# edge, under the caption, where the older app wrote "Sponsored". Two letters are too few to match
# loosely, so a badge must read exactly as one of these (ignoring case and stray punctuation) and,
# when the width of the read region is known, sit at the left edge and be no wider than a badge.
BADGE_LABELS = ["Ad"]
BADGE_MAX_LEFT = 0.15
BADGE_MAX_WIDTH = 0.15
# OCR detections below this confidence are left out of the account and caption text
TEXT_MIN_CONFIDENCE = 0.3
# The part of the window that is read: the lower half, without the right-hand rail of
# like/comment/share counts (the rightmost 15% of the width)
READ_WIDTH_SHARE = 0.85
# App furniture that is on screen for every video and so says nothing about which video it is.
# An exact match is dropped anywhere. A near match (OCR slips such as "Homes") is dropped only in
# the bottom strip of the read region, where the navigation bar sits, so that an account called
# "Homer" higher up is kept.
SCREEN_CHROME = ["Home", "Friends", "Inbox", "Profile", "See more", "+"]
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
# The sound credit under the caption, which is the same for every video from one account
SOUND_CREDIT = "original sound"
SOUND_CREDIT_CUTOFF = 85
# The sound credit scrolls sideways, so each read catches a different piece of it ("und - kjlyrics -
# KJLYRICS", then "riginal sound - kjlyrics - K"). It is the lowest line of text above the navigation
# bar, so the lowest line is also left out of the comparison when it sits below this share of the
# read region's height. On an ad with a button, that lowest line is the button, which is no loss.
LOWEST_LINE_START = 0.6
# ...or, for a video whose text is many short pieces (text drawn on the video itself), when at least
# this many lines of 4 or more characters, and this share of them on both sides, are the same.
SHARED_LINES_MIN_COUNT = 3
SHARED_LINES_MIN_SHARE = 0.6


def read_region(image):
    # The slice of a full-window image that is given to the OCR. Used by the scraper and by
    # tools/make_fixtures.py so that fixtures always show what the scraper reads.
    height, width = image.shape[:2]
    return image[height // 2:(height // 2) * 2, 0:int(width * READ_WIDTH_SHARE)]


def badge_label(bbox, text, width=None):
    # Returns the badge this detection is ("Ad"), or None
    cleaned = str(text).strip(" .:;,_-|[]()'\"").lower()
    for badge in BADGE_LABELS:
        if cleaned != badge.lower():
            continue
        if width:
            left = min(point[0] for point in bbox)
            right = max(point[0] for point in bbox)
            if left > width * BADGE_MAX_LEFT or right - left > width * BADGE_MAX_WIDTH:
                continue
        return badge
    return None


def match_label(results, flags=SPONSORED_FLAGS, cutoff=LABEL_CUTOFF, width=None):
    # Returns (flag, score, matched_text) for the best label match on screen.
    # flag is None when no badge is found and the best score is not above the cutoff.
    # width is the width of the read region, used to check where a badge sits.
    for bbox, text, _ in results:
        badge = badge_label(bbox, text, width)
        if badge is not None:
            return badge, 100, text
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


def screen_text(results, height=None, flags=SPONSORED_FLAGS, cutoff=LABEL_CUTOFF, min_confidence=TEXT_MIN_CONFIDENCE, width=None):
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
        if badge_label(bbox, text, width) is not None:
            continue
        top = min(point[1] for point in bbox)
        left = min(point[0] for point in bbox)
        if is_chrome(text, top, height):
            continue
        kept.append((top, left, text))
    kept.sort()
    lines = [text for _, _, text in kept]
    # The lines that say which video this is: everything but the sound credit, which is the same for
    # every video from one account and reads differently each time because it scrolls
    identifying = [
        text.lower() for position, (top, _, text) in enumerate(kept)
        if fuzz.partial_ratio(text.lower(), SOUND_CREDIT) < SOUND_CREDIT_CUTOFF
        and not (height and len(kept) > 1 and position == len(kept) - 1 and top >= height * LOWEST_LINE_START)
    ]
    return {
        "account": lines[0] if lines else "",
        "caption": " ".join(lines[1:]),
        "lines": lines,
        "identifying": identifying
    }


def identifying_lines(text):
    # As worked out by screen_text; for text put together some other way, everything but a line
    # that reads as a sound credit
    if "identifying" in text:
        return text["identifying"]
    return [
        line.lower() for line in text["lines"]
        if fuzz.partial_ratio(line.lower(), SOUND_CREDIT) < SOUND_CREDIT_CUTOFF
    ]


def same_video(previous, current, threshold=SAME_VIDEO_THRESHOLD):
    # Compares the text of two reads. True: same video (the swipe did not advance).
    # False: a different video. None: neither read has any text, so it cannot be told.
    if not previous["lines"] and not current["lines"]:
        return None
    previous_lines = identifying_lines(previous)
    current_lines = identifying_lines(current)
    previous_text = " ".join(previous_lines)
    current_text = " ".join(current_lines)
    if previous_text == "" or current_text == "":
        if previous_text != current_text:
            # One read has a caption or account and the other has none
            return False
        # Both reads are nothing but a sound credit, so the credits are all there is to compare
        return fuzz.ratio(" ".join(previous["lines"]).lower(), " ".join(current["lines"]).lower()) >= threshold
    if fuzz.token_sort_ratio(previous_text, current_text) >= threshold:
        return True
    previous_long = [line for line in previous_lines if len(line) >= SHARED_LINE_MIN_LENGTH]
    current_long = [line for line in current_lines if len(line) >= SHARED_LINE_MIN_LENGTH]
    shared_characters = sum(
        len(previous_line) for previous_line in previous_long
        if any(fuzz.ratio(previous_line, current_line) >= SHARED_LINE_CUTOFF for current_line in current_long)
    )
    if shared_characters >= SHARED_LINES_MIN_TOTAL:
        return True
    previous_pieces = [line for line in previous_lines if len(line) >= 4]
    current_pieces = [line for line in current_lines if len(line) >= 4]
    if not previous_pieces or not current_pieces:
        return False
    shared_previous = sum(any(fuzz.ratio(a, b) >= SHARED_LINE_CUTOFF for b in current_pieces) for a in previous_pieces)
    shared_current = sum(any(fuzz.ratio(a, b) >= SHARED_LINE_CUTOFF for b in previous_pieces) for a in current_pieces)
    return (
        min(shared_previous, shared_current) >= SHARED_LINES_MIN_COUNT
        and shared_previous / len(previous_pieces) >= SHARED_LINES_MIN_SHARE
        and shared_current / len(current_pieces) >= SHARED_LINES_MIN_SHARE
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

    def last_swipe_time(self):
        # When the swipe that should bring the next video on screen was sent, or None
        if self.current is None or not self.current["swipes"]:
            return None
        return self.current["swipes"][-1]["at"]

    def open(self, shown_at, text, flag, score, matched_text, ocr_ms, scoring_ms, frame_path, advance_confirmed=True, first_read_at=None, text_source=None):
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
            "text_source": text_source,
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

    def read_count(self):
        return self.current["reads"] if self.current else 0

    def shown_time(self):
        # When the video on screen came on screen
        return self.current["shown_at"] if self.current else None

    def note_read(self, text=None):
        # The video on screen was read again. If the first read found no text (the video was still
        # loading) and this one did, keep this one's account and caption.
        if self.current is not None:
            self.current["reads"] += 1
            if text is not None and text["lines"] and not self.current["text"]["lines"]:
                self.current["text"] = text

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
            "text_source": current["text_source"],
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

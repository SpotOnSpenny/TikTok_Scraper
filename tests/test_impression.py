# Python Standard Library Dependencies
import json
import os
import re

# External Dependency Imports
import numpy
import pandas
import pytest
from fuzzywuzzy import fuzz

# Internal Dependency Imports
from conftest import load_results, fixture_names, FIXTURE_DIR
from tiktok_scraper.Core.impression import (
    SPONSORED_FLAGS, match_label, screen_text, same_video, read_region, ImpressionLog
)


def synthetic(name):
    return load_results("synthetic", name)


def text_of(name):
    return screen_text(synthetic(name))


# ---------------------------------- match_label ----------------------------------

def test_flags_are_three_separate_labels():
    # Guards against the old missing comma that merged two labels into one string
    assert SPONSORED_FLAGS == ["Sponsored", "Paid Promotion", "Promoted Music"]


def test_sponsored_label_matches():
    assert match_label(synthetic("ad_sponsored")) == ("Sponsored", 100, "Sponsored")


def test_noisy_label_still_matches():
    flag, score, matched_text = match_label(synthetic("ad_sponsored_noisy"))
    assert flag == "Sponsored"
    assert 78 < score < 100
    assert matched_text == "Sponsorcd"


def test_promoted_music_matches():
    assert match_label(synthetic("ad_promoted_music"))[0] == "Promoted Music"


def test_paid_partnership_is_off_by_default():
    assert match_label(synthetic("paid_partnership"))[0] is None


def test_paid_partnership_can_be_switched_on():
    flag, _, _ = match_label(synthetic("paid_partnership"), flags=SPONSORED_FLAGS + ["Paid partnership"])
    assert flag == "Paid partnership"


def test_ordinary_video_is_not_an_ad():
    flag, score, _ = match_label(synthetic("ordinary_a"))
    assert flag is None
    assert score <= 78


def test_caption_mentioning_sponsored_is_not_an_ad():
    # Whole-line matching: a long caption containing the word does not reach the cutoff
    assert match_label(synthetic("caption_mentions_sponsored"))[0] is None


def test_no_text_is_not_an_ad():
    assert match_label([]) == (None, 0, None)


# ---------------------------------- screen_text ----------------------------------

def test_account_is_top_line_and_label_is_dropped():
    text = text_of("ad_sponsored")
    assert text["account"] == "Brightside Toys"
    assert text["caption"] == "The holiday set is back in stock Shop now"
    assert "Sponsored" not in text["lines"]


def test_lines_are_ordered_top_to_bottom():
    results = list(reversed(synthetic("ordinary_a")))
    assert screen_text(results)["account"] == "gardenwithmia"


def test_low_confidence_text_is_dropped():
    assert text_of("low_confidence_only") == {"account": "", "caption": "", "lines": []}


def test_nav_bar_is_dropped_but_a_lookalike_account_is_kept():
    text = screen_text(synthetic("nav_lookalike_account"), height=480)
    assert text["lines"] == ["Homer", "Donut review number forty seven"]
    assert text["account"] == "Homer"


def test_read_region_is_lower_half_without_the_count_rail():
    image = numpy.zeros((960, 540, 3), dtype=numpy.uint8)
    assert read_region(image).shape == (480, 459, 3)


# ---------------------------------- same_video -----------------------------------

def test_reread_of_same_video_is_same():
    assert same_video(text_of("ordinary_a"), text_of("ordinary_a_reread")) is True


def test_different_videos_are_different():
    assert same_video(text_of("ordinary_a"), text_of("ordinary_b")) is False


def test_label_appearing_late_does_not_make_a_new_video():
    # The badge is not part of the compared text
    assert same_video(text_of("ad_sponsored"), text_of("ad_sponsored_noisy")) is True


def test_no_text_twice_cannot_be_told():
    assert same_video(text_of("no_text"), text_of("no_text")) is None
    assert same_video(text_of("no_text"), text_of("low_confidence_only")) is None


def test_text_then_no_text_is_different():
    assert same_video(text_of("ordinary_a"), text_of("no_text")) is False
    assert same_video(text_of("no_text"), text_of("ordinary_a")) is False


# -------------------------------- S3 ad fixtures ---------------------------------
# Real frames of ads the old detector captured. Present once tools/make_fixtures.py s3 has run.

S3_NAMES = fixture_names("s3")
needs_s3 = pytest.mark.skipif(not S3_NAMES, reason="no S3 fixtures yet")


def s3_text(name):
    with open(os.path.join(FIXTURE_DIR, "s3", f"{name}.json"), encoding="utf-8") as file:
        height = json.load(file)["crop_size"][1]
    return screen_text(load_results("s3", name), height=height)


def s3_groups():
    # Frames of the same ad share everything before the _tNN suffix
    groups = {}
    for name in S3_NAMES:
        groups.setdefault(name.rsplit("_t", 1)[0], []).append(name)
    return groups


@needs_s3
def test_s3_fixtures_are_app_captures_only():
    # Web-interface captures have hyphenated ids (20250205at183137-8M-vancouver) and a different layout
    assert all("-" not in name for name in S3_NAMES)


@needs_s3
def test_s3_every_ad_shows_a_label_in_some_frame():
    missed = [
        ad for ad, names in s3_groups().items()
        if not any(match_label(load_results("s3", name))[0] for name in names)
    ]
    assert missed == []


@needs_s3
def test_s3_label_is_read_in_most_frames():
    # The misses are end cards with no label on screen; 115 of 120 matched when this was set
    hits = sum(match_label(load_results("s3", name))[0] is not None for name in S3_NAMES)
    assert hits / len(S3_NAMES) >= 0.93


@needs_s3
def test_s3_only_labels_score_near_the_cutoff():
    # Any line within reach of the cutoff must be a label or a piece of one, not caption text
    near = {
        text for name in S3_NAMES for _, text, _ in load_results("s3", name)
        if max(fuzz.ratio(text, flag) for flag in SPONSORED_FLAGS) >= 60
    }
    not_label = [text for text in near if not re.search(r"spo|sor|pro|mot|ted|music", text.lower())]
    assert not_label == []


@needs_s3
def test_s3_frames_of_one_ad_are_mostly_the_same_video():
    # The misses are ads that switch to an end card; 76 of 80 when set
    pairs = [(first, second) for names in s3_groups().values() for first, second in zip(names, names[1:])]
    same = sum(same_video(s3_text(first), s3_text(second)) is True for first, second in pairs)
    assert same / len(pairs) >= 0.93


# Two captures of one campaign with near-identical captions; judging them the same video is expected
SAME_CAMPAIGN = [{"20250305at193913_18F_calgary", "20250313at125315_9F_calgary"}]


@needs_s3
def test_s3_a_swipe_to_a_different_ad_is_never_the_same_video():
    # What a swipe compares: any frame of one ad (on screen before) with the first frame of another (after)
    groups = s3_groups()
    texts = {name: s3_text(name) for name in S3_NAMES}
    wrong = set()
    for ad, names in groups.items():
        for other, other_names in groups.items():
            if ad == other or {ad, other} in SAME_CAMPAIGN:
                continue
            if any(same_video(texts[name], texts[other_names[0]]) is True for name in names):
                wrong.add((ad, other))
    assert wrong == set()


def test_two_videos_from_one_account_are_different():
    # The account name is shared, but it is not enough on its own
    first = screen_text(synthetic("same_account_video_1"))
    second = screen_text(synthetic("same_account_video_2"))
    assert first["account"] == second["account"] == "SharkHomeCanada"
    assert same_video(first, second) is False


@needs_s3
def test_s3_app_furniture_is_not_in_the_text():
    for name in S3_NAMES:
        assert not {"Home", "Friends", "Inbox", "Profile"} & set(s3_text(name)["lines"])


# --------------------------------- ImpressionLog ---------------------------------

def make_log(tmp_path):
    return ImpressionLog(str(tmp_path), "20261119at120000_6M_calgary", {"demographic": "6M", "location": "calgary"})


def open_video(log, name, shown_at, advance_confirmed=True):
    results = synthetic(name)
    flag, score, matched_text = match_label(results)
    return log.open(shown_at, screen_text(results), flag, score, matched_text, 812.5, 0.4, log.next_frame_path(), advance_confirmed)


def read_rows(log):
    with open(log.path, encoding="utf-8") as file:
        return [json.loads(line) for line in file]


def test_nothing_is_written_until_a_video_is_closed(tmp_path):
    log = make_log(tmp_path)
    assert log.close() is None
    open_video(log, "ordinary_a", 1000.0)
    assert not os.path.exists(log.path)


def test_row_contents(tmp_path):
    log = make_log(tmp_path)
    open_video(log, "ad_sponsored", 1000.0)
    log.note_swipe(1026.5, {"x": 270, "y_start": 720, "y_end": 240})
    log.close()
    row = read_rows(log)[0]
    assert row["session_id"] == "20261119at120000_6M_calgary"
    assert row["index"] == 1
    assert row["dwell_s"] == 26.5
    assert row["account"] == "Brightside Toys"
    assert row["is_ad"] is True
    assert row["matched_label"] == "Sponsored"
    assert row["match_score"] == 100
    assert row["matched_text"] == "Sponsored"
    assert row["swipe"] == {"x": 270, "y_start": 720, "y_end": 240}
    assert row["swipe_attempts"] == 1
    assert row["label_read"] == 1
    assert row["reads"] == 1
    assert row["stuck_refreshes"] == 0
    assert row["advance_confirmed"] is True
    assert row["ocr_ms"] == 812.5
    assert row["frame_path"] == "frames/000001.jpg"
    assert row["demographic"] == "6M"
    assert row["location"] == "calgary"
    assert row["close_reason"] == "advanced"
    assert row["shown_at"] < row["left_at"]


def test_failed_swipe_gives_one_row_with_two_attempts(tmp_path):
    log = make_log(tmp_path)
    open_video(log, "ordinary_a", 1000.0)
    log.note_swipe(1005.0, {"x": 1})
    # The next read shows the same video, so nothing is closed and the swipe is repeated
    assert same_video(log.current_text(), text_of("ordinary_a_reread")) is True
    log.note_swipe(1011.0, {"x": 2})
    assert same_video(log.current_text(), text_of("ordinary_b")) is False
    log.close()
    rows = read_rows(log)
    assert len(rows) == 1
    assert rows[0]["swipe_attempts"] == 2
    assert rows[0]["dwell_s"] == 11.0
    assert rows[0]["swipe"] == {"x": 2}


def test_late_label_turns_the_open_video_into_an_ad(tmp_path):
    log = make_log(tmp_path)
    open_video(log, "ordinary_a", 1000.0)
    assert log.is_ad() is False
    log.note_swipe(1005.0, {"x": 1})
    log.note_read()
    log.mark_ad("Sponsored", 100, "Sponsored")
    assert log.is_ad() is True
    log.close()
    row = read_rows(log)[0]
    assert row["is_ad"] is True
    assert row["matched_label"] == "Sponsored"
    assert row["label_read"] == 2
    assert row["reads"] == 2


def test_session_end_closes_the_open_video(tmp_path):
    log = make_log(tmp_path)
    open_video(log, "ordinary_a", 1000.0)
    log.close("session_end", at=1003.0)
    row = read_rows(log)[0]
    assert row["close_reason"] == "session_end"
    assert row["swipe"] is None
    assert row["swipe_attempts"] == 0
    assert row["dwell_s"] == 3.0
    # Closing again writes nothing more
    assert log.close("session_end") is None
    assert len(read_rows(log)) == 1


def test_rows_load_with_pandas(tmp_path):
    log = make_log(tmp_path)
    for number, name in enumerate(["ordinary_a", "ad_sponsored", "no_text", "ordinary_b"]):
        open_video(log, name, 1000.0 + number * 6, advance_confirmed=name != "no_text")
        log.note_swipe(1005.0 + number * 6, {"x": 270, "y_start": 720, "y_end": 240})
        log.close()
    frame = pandas.read_json(log.path, lines=True)
    assert list(frame["index"]) == [1, 2, 3, 4]
    assert list(frame["is_ad"]) == [False, True, False, False]
    assert list(frame["advance_confirmed"]) == [True, True, False, True]
    assert list(frame["frame_path"]) == [f"frames/{n:06d}.jpg" for n in range(1, 5)]

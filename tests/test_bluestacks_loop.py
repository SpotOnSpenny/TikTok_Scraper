# Python Standard Library Dependencies
import ctypes
import json
import sys
import types
from unittest import mock

# External Dependency Imports
import numpy
import pytest

# Internal Dependency Imports
from conftest import load_results
from tiktok_scraper.Core.impression import ImpressionLog

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# Runs the real find_bluestacks_ad / capture loop logic from bluestacks.py with the
# Windows, EasyOCR and S3 modules replaced by stand-ins, the screen replaced by a script
# of fixture reads, and sleeping switched off. It checks counting and logging, not capture.


@pytest.fixture
def bluestacks(monkeypatch):
    for name in ["win32gui", "win32con", "win32api", "win32ui", "easyocr", "boto3", "PIL", "PIL.Image"]:
        monkeypatch.setitem(sys.modules, name, mock.MagicMock())
    processing = types.ModuleType("tiktok_scraper.Core.processing")
    processing.stitch_video = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "tiktok_scraper.Core.processing", processing)
    monkeypatch.setattr(ctypes, "windll", mock.MagicMock(), raising=False)
    monkeypatch.delitem(sys.modules, "tiktok_scraper.Core.bluestacks", raising=False)
    import tiktok_scraper.Core.bluestacks as module
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module.win32gui, "GetWindowRect", lambda hwnd: (0, 0, 540, 960))
    monkeypatch.setattr(module, "trim_mem", mock.MagicMock())
    monkeypatch.setattr(module, "refresh_feed", mock.MagicMock())
    monkeypatch.setitem(module.monitoring_data, "data_index", 0)
    monkeypatch.setitem(module.monitoring_data, "videos_watched", 0)
    monkeypatch.setitem(module.monitoring_data, "ads_found", 0)
    monkeypatch.setitem(module.monitoring_data, "ads_this_log", 0)
    monkeypatch.setitem(module.monitoring_data, "demographic", "6M")
    monkeypatch.setitem(module.monitoring_data, "location", "calgary")
    monkeypatch.setitem(module.housekeeping, "done_at", None)
    yield module
    sys.modules.pop("tiktok_scraper.Core.bluestacks", None)


def play(module, monkeypatch, names):
    # Each read of the screen returns the next fixture in the script
    script = iter(names)
    image = numpy.zeros((960, 540, 3), dtype=numpy.uint8)
    monkeypatch.setattr(module, "read_screen", lambda hwnd, rect: (image, load_results("synthetic", next(script)), 800.0))


def rows(log):
    with open(log.path, encoding="utf-8") as file:
        return [json.loads(line) for line in file]


def test_counts_videos_not_swipes(bluestacks, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session", {"demographic": "6M", "location": "calgary"})
    # Video A, a failed swipe (A read again), video B, then an ad
    play(bluestacks, monkeypatch, ["ordinary_a", "ordinary_a_reread", "ordinary_b", "ad_sponsored"])
    bluestacks.find_bluestacks_ad(1234, log)

    assert bluestacks.monitoring_data["videos_watched"] == 3
    assert bluestacks.monitoring_data["data_index"] == 2
    written = rows(log)
    assert [row["account"] for row in written] == ["gardenwithmia", "coachdan"]
    assert [row["swipe_attempts"] for row in written] == [2, 1]
    assert written[0]["swipe"] == {"x": 270, "y_start": 720, "y_end": 240}
    # The ad is on screen and still open; one frame is stored per counted video
    assert log.current["flag"] == "Sponsored"
    assert sorted(path.name for path in (tmp_path / "frames").iterdir()) == ["000001.jpg", "000002.jpg", "000003.jpg"]


def test_ad_is_not_captured_twice_when_the_swipe_after_it_fails(bluestacks, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(bluestacks, "take_screencaps", lambda hwnd, rect, file_name: None)
    job_queue = mock.MagicMock()
    clock = iter(range(0, 100000, 7))
    monkeypatch.setattr(bluestacks.time, "time", lambda: float(next(clock)))

    play(bluestacks, monkeypatch, ["ad_sponsored", "ad_sponsored_noisy", "ordinary_b", "ad_promoted_music"])
    rect = bluestacks.find_bluestacks_ad(1234, log)
    bluestacks.capture_bluestacks_ad(1234, str(output_dir), rect, mock.MagicMock(), job_queue, log)
    # The swipe after the capture fails once: the same ad is read again, then B, then a second ad
    bluestacks.find_bluestacks_ad(1234, log)

    assert job_queue.put.call_count == 1
    assert bluestacks.monitoring_data["ads_found"] == 1
    assert bluestacks.monitoring_data["videos_watched"] == 3
    written = rows(log)
    assert [row["is_ad"] for row in written] == [True, False]
    assert written[0]["swipe_attempts"] == 2
    assert log.current["flag"] == "Promoted Music"


def test_no_text_twice_is_counted_and_flagged(bluestacks, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    play(bluestacks, monkeypatch, ["no_text", "no_text", "ad_sponsored"])
    bluestacks.find_bluestacks_ad(1234, log)
    log.close("session_end")

    assert bluestacks.monitoring_data["videos_watched"] == 3
    assert [row["advance_confirmed"] for row in rows(log)] == [True, False, True]
    assert rows(log)[-1]["close_reason"] == "session_end"


def test_housekeeping_runs_once_per_counted_video(bluestacks, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    monkeypatch.setitem(bluestacks.monitoring_data, "videos_watched", 24)
    # The 25th video's swipe fails twice before the feed moves on
    play(bluestacks, monkeypatch, ["ordinary_a", "ordinary_a", "ordinary_a_reread", "ordinary_b", "ad_sponsored"])
    bluestacks.find_bluestacks_ad(1234, log)

    assert bluestacks.monitoring_data["videos_watched"] == 27
    assert bluestacks.trim_mem.call_count == 2
    assert bluestacks.refresh_feed.call_count == 0

# Python Standard Library Dependencies
import json
import os
from unittest import mock

# External Dependency Imports
import numpy
import pytest

# Internal Dependency Imports
from conftest import load_results
from tiktok_scraper.Core import collector
from tiktok_scraper.Core.impression import ImpressionLog

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# Runs the real collection loop from collector.py against a stand-in device, with the OCR
# replaced by a script of fixture reads and sleeping switched off. It checks counting,
# logging and what the loop asks the device to do, not the devices themselves.


class FakeDevice:

    def __init__(self, size=(540, 960)):
        self.size = size
        self.swipes = 0
        self.trims = 0
        self.refreshes = 0
        self.recordings = []

    def read_frame(self):
        return numpy.zeros((self.size[1], self.size[0], 3), dtype=numpy.uint8)

    def swipe_up(self):
        self.swipes += 1
        return {"x": 270, "y_start": 720, "y_end": 240}

    def trim(self):
        self.trims += 1

    def refresh(self):
        self.refreshes += 1

    def record_ad(self, folder, seconds):
        self.recordings.append((folder, seconds))
        return {"video_path": os.path.join(folder, "ad.mp4")}


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda seconds: None)
    for key, value in [("data_index", 0), ("videos_watched", 0), ("ads_found", 0), ("ads_this_log", 0), ("demographic", "6M"), ("location", "calgary")]:
        monkeypatch.setitem(collector.monitoring_data, key, value)
    return FakeDevice()


def play(monkeypatch, names):
    # Each read of the screen returns the OCR results of the next fixture in the script
    script = iter(names)
    monkeypatch.setattr(collector, "ocr", lambda region: load_results("synthetic", next(script)))


def rows(log):
    with open(log.path, encoding="utf-8") as file:
        return [json.loads(line) for line in file]


def test_counts_videos_not_swipes(device, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session", {"demographic": "6M", "location": "calgary"})
    # Video A, a failed swipe (A read again), video B, then an ad
    play(monkeypatch, ["ordinary_a", "ordinary_a_reread", "ordinary_b", "ad_sponsored"])
    collector.find_ad(device, log)

    assert collector.monitoring_data["videos_watched"] == 3
    assert collector.monitoring_data["data_index"] == 2
    written = rows(log)
    assert [row["account"] for row in written] == ["gardenwithmia", "coachdan"]
    assert [row["swipe_attempts"] for row in written] == [2, 1]
    assert written[0]["swipe"] == {"x": 270, "y_start": 720, "y_end": 240}
    # The ad is on screen and still open; one frame is stored per counted video
    assert log.current["flag"] == "Sponsored"
    assert sorted(path.name for path in (tmp_path / "frames").iterdir()) == ["000001.jpg", "000002.jpg", "000003.jpg"]


def test_ad_is_not_captured_twice_when_the_swipe_after_it_fails(device, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    job_queue = mock.MagicMock()
    clock = iter(range(0, 100000, 7))
    monkeypatch.setattr(collector.time, "time", lambda: float(next(clock)))

    play(monkeypatch, ["ad_sponsored", "ad_sponsored_noisy", "ordinary_b", "ad_promoted_music"])
    collector.find_ad(device, log)
    collector.capture_ad(device, str(output_dir), mock.MagicMock(), job_queue, log)
    # The swipe after the capture fails once: the same ad is read again, then B, then a second ad
    collector.find_ad(device, log)

    assert job_queue.put.call_count == 1
    job = job_queue.put.call_args[0][0]
    assert job["video_path"].endswith("ad.mp4")
    assert job["found_at_index"] == 0
    assert len(device.recordings) == 1
    assert device.recordings[0][1] == 20
    assert collector.monitoring_data["ads_found"] == 1
    assert collector.monitoring_data["videos_watched"] == 3
    written = rows(log)
    assert [row["is_ad"] for row in written] == [True, False]
    assert written[0]["swipe_attempts"] == 2
    assert log.current["flag"] == "Promoted Music"


def test_reads_with_no_text_are_counted_and_flagged(device, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    # A video, then two reads with no text at all (for example a loading screen), then an ad
    play(monkeypatch, ["ordinary_a", "no_text", "no_text", "ad_sponsored"])
    collector.find_ad(device, log)
    log.close("session_end")

    assert collector.monitoring_data["videos_watched"] == 4
    assert [row["advance_confirmed"] for row in rows(log)] == [True, False, False, True]
    assert rows(log)[-1]["close_reason"] == "session_end"


def test_label_appearing_after_a_failed_swipe_is_still_captured(device, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    # Read as an ordinary video, the swipe fails, and on the next read the badge is there
    play(monkeypatch, ["ordinary_a", "ordinary_a_late_label"])
    collector.find_ad(device, log)

    assert collector.monitoring_data["videos_watched"] == 1
    assert log.is_ad()
    log.close("session_end")
    written = rows(log)
    assert len(written) == 1
    assert written[0]["is_ad"] is True
    assert written[0]["label_read"] == 2
    assert written[0]["swipe_attempts"] == 1


def test_stuck_feed_is_refreshed_after_repeated_failed_swipes(device, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    # The same screen for eleven reads, then the feed moves on to an ad
    play(monkeypatch, ["ordinary_a"] * 11 + ["ad_sponsored"])
    collector.find_ad(device, log)

    assert collector.monitoring_data["videos_watched"] == 2
    assert device.refreshes == 2
    written = rows(log)
    assert written[0]["swipe_attempts"] == 11
    assert written[0]["stuck_refreshes"] == 2
    assert written[0]["reads"] == 11


def test_housekeeping_runs_once_per_counted_video(device, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    monkeypatch.setitem(collector.monitoring_data, "videos_watched", 24)
    # The 25th video's swipe fails twice before the feed moves on
    play(monkeypatch, ["ordinary_a", "ordinary_a", "ordinary_a_reread", "ordinary_b", "ad_sponsored"])
    collector.find_ad(device, log)

    assert collector.monitoring_data["videos_watched"] == 27
    assert device.trims == 1
    assert device.refreshes == 0


def test_a_phone_sized_frame_is_stored_at_emulator_width(device, monkeypatch, tmp_path):
    import cv2
    device.size = (1080, 2400)
    log = ImpressionLog(str(tmp_path), "session")
    regions = []
    monkeypatch.setattr(collector, "ocr", lambda region: regions.append(region.shape) or load_results("synthetic", "ad_sponsored"))
    collector.find_ad(device, log)

    # 1080x2400 becomes 540x1200; the read region is its lower half without the count rail
    assert regions == [(600, 459, 3)]
    assert cv2.imread(str(tmp_path / "frames" / "000001.jpg")).shape == (1200, 540, 3)


def test_dwell_runs_from_the_previous_swipe_to_this_videos_swipe(device, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    clock = iter(range(1000, 100000, 3))
    monkeypatch.setattr(collector.time, "time", lambda: float(next(clock)))
    play(monkeypatch, ["ordinary_a", "ordinary_b", "ad_sponsored"])
    collector.find_ad(device, log)

    first, second = rows(log)
    # The first video has no swipe before it, so it is timed from its first read
    assert first["shown_at"] == first["first_read_at"]
    # The second came on screen at the swipe that left the first
    assert second["shown_at"] == first["left_at"]
    assert second["first_read_at"] > second["shown_at"]
    assert second["dwell_s"] > 0

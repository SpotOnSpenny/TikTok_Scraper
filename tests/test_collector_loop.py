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


class FakeClock:
    # Stands in for the time module inside collector: sleeping and reading move the clock on

    def __init__(self, read_seconds=1.0):
        self.now = 1000.0
        self.read_seconds = read_seconds

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def perf_counter(self):
        return self.now

    def strftime(self, pattern):
        return "20261123at120000"


@pytest.fixture
def device(monkeypatch):
    # One read per video and no minimum dwell, so each test's script is one read per step
    monkeypatch.setattr(collector, "READS_PER_VIDEO", 1)
    monkeypatch.setattr(collector, "DWELL_SECONDS", 0)
    monkeypatch.setattr(collector.time, "sleep", lambda seconds: None)
    for key, value in [("data_index", 0), ("videos_watched", 0), ("ads_found", 0), ("ads_this_log", 0), ("demographic", "6M"), ("location", "calgary")]:
        monkeypatch.setitem(collector.monitoring_data, key, value)
    return FakeDevice()


def play(monkeypatch, names):
    # Each read of the screen returns the OCR results of the next fixture in the script
    script = iter(names)
    monkeypatch.setattr(collector, "_engine", ("test", lambda region: load_results("synthetic", next(script))))


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
    monkeypatch.setattr(collector, "_engine", ("test", lambda region: regions.append(region.shape) or load_results("synthetic", "ad_sponsored")))
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


# ------------------------------ several reads per video ------------------------------

@pytest.fixture
def clock(device, monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(collector, "time", fake)
    monkeypatch.setattr(collector, "READS_PER_VIDEO", 2)
    monkeypatch.setattr(collector, "DWELL_SECONDS", 0)
    return fake


def play_timed(monkeypatch, clock, names):
    script = iter(names)

    def read(region):
        clock.sleep(clock.read_seconds)
        return load_results("synthetic", next(script))
    monkeypatch.setattr(collector, "_engine", ("test", read))


def test_each_video_is_read_twice_before_it_is_swiped(device, clock, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    play_timed(monkeypatch, clock, ["ordinary_a", "ordinary_a_reread", "ordinary_b", "ordinary_b", "ad_sponsored"])
    collector.find_ad(device, log)

    assert collector.monitoring_data["videos_watched"] == 3
    assert device.swipes == 2
    written = rows(log)
    assert [row["reads"] for row in written] == [2, 2]
    assert [row["swipe_attempts"] for row in written] == [1, 1]


def test_label_that_appears_on_the_second_read_is_caught_without_a_swipe(device, clock, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    play_timed(monkeypatch, clock, ["ordinary_a", "ordinary_a_late_label"])
    collector.find_ad(device, log)

    assert device.swipes == 0
    assert collector.monitoring_data["videos_watched"] == 1
    log.close("session_end")
    row = rows(log)[0]
    assert row["is_ad"] is True
    assert row["label_read"] == 2
    assert row["swipe_attempts"] == 0


def test_a_video_is_watched_for_the_dwell_time_however_fast_the_reads_are(device, clock, monkeypatch, tmp_path):
    monkeypatch.setattr(collector, "DWELL_SECONDS", 5)
    clock.read_seconds = 0.5
    log = ImpressionLog(str(tmp_path), "session")
    play_timed(monkeypatch, clock, ["ordinary_a"] * 20 + ["ad_sponsored"])
    # The feed never moves on in this script, so stop at the first swipe
    device.swipe_up = mock.Mock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        collector.find_ad(device, log)

    assert log.read_count() >= 5
    assert clock.now - log.shown_time() >= 5


def test_with_no_dwell_a_video_is_left_as_soon_as_its_reads_are_done(device, clock, monkeypatch, tmp_path):
    clock.read_seconds = 1.0
    log = ImpressionLog(str(tmp_path), "session")
    play_timed(monkeypatch, clock, ["ordinary_a", "ordinary_a", "ordinary_b", "ordinary_b", "ad_sponsored"])
    collector.find_ad(device, log)

    # Second video: 1 s to settle, two 1 s reads and the 0.3 s gap between them
    assert rows(log)[1]["reads"] == 2
    assert rows(log)[1]["dwell_s"] == pytest.approx(3.3)


def test_watch_time_can_depend_on_the_video(device, clock, monkeypatch, tmp_path):
    # Stand-in for the interest scorer: linger on gardening, skip everything else at once
    monkeypatch.setattr(collector, "watch_seconds", lambda text: 12 if "monstera" in text["caption"] else 0)
    clock.read_seconds = 1.0
    log = ImpressionLog(str(tmp_path), "session")
    play_timed(monkeypatch, clock, ["ordinary_b", "ordinary_b"] + ["ordinary_a"] * 12 + ["ad_sponsored"])
    collector.find_ad(device, log)

    skipped, watched = rows(log)
    assert skipped["account"] == "coachdan" and skipped["dwell_s"] < 4
    assert watched["account"] == "gardenwithmia" and watched["dwell_s"] >= 12


def test_a_slow_reader_still_gets_its_two_reads(device, clock, monkeypatch, tmp_path):
    monkeypatch.setattr(collector, "DWELL_SECONDS", 5)
    clock.read_seconds = 4.0
    log = ImpressionLog(str(tmp_path), "session")
    play_timed(monkeypatch, clock, ["ordinary_a", "ordinary_a", "ad_sponsored"])
    collector.find_ad(device, log)

    assert rows(log)[0]["reads"] == 2
    assert rows(log)[0]["dwell_s"] >= 8


def test_text_from_a_later_read_replaces_an_empty_first_read(device, clock, monkeypatch, tmp_path):
    log = ImpressionLog(str(tmp_path), "session")
    # The first look catches the video still loading; the second reads it properly
    play_timed(monkeypatch, clock, ["no_text", "ordinary_a", "ad_sponsored"])
    collector.find_ad(device, log)

    row = rows(log)[0]
    assert row["account"] == "gardenwithmia"
    assert row["advance_confirmed"] is False


# ----------------------------------- OCR engine size -----------------------------------

def test_fast_engine_is_given_a_double_size_region_and_positions_are_scaled_back(device, monkeypatch):
    seen = []

    def read(region):
        seen.append(region.shape)
        return [([[40, 200], [240, 200], [240, 260], [40, 260]], "Sponsored", 0.99)]
    monkeypatch.setattr(collector, "_engine", ("rapidocr", read))

    # A phone screenshot is used at its own resolution
    device.size = (1080, 2400)
    reading = collector.read_screen(device)
    assert seen[-1] == (1200, 918, 3)
    assert reading["image"].shape == (1200, 540, 3)
    assert (reading["region_width"], reading["region_height"]) == (459, 600)
    assert reading["results"][0][0] == [[20, 100], [120, 100], [120, 130], [20, 130]]
    assert reading["source"] == "rapidocr"

    # An emulator frame is enlarged for the engine
    device.size = (540, 960)
    reading = collector.read_screen(device)
    assert seen[-1] == (960, 918, 3)
    assert reading["image"].shape == (960, 540, 3)
    assert reading["region_height"] == 480


# ------------------------------ text straight from the phone ------------------------------

SCREEN = (1080, 2280)


def ui_video(account, caption, sound, badge=False, shift=0):
    # The text elements a phone reports for one video, shaped like a real dump of the For You feed
    nodes = [
        ((292, 101, 567, 296), "Following"), ((567, 101, 788, 297), "For You"),
        ((41, 1710 - shift, 536, 1778 - shift), account),
        ((41, 1805 - shift, 742, 1939 - shift), caption),
        ((108, 1952, 648, 2013), sound),
        ((912, 1084, 1027, 1168), "69.5K"), ((920, 1303, 1020, 1387), "3567"),
        ((0, 2163, 216, 2207), "Home"), ((216, 2163, 432, 2207), "Friends"),
        ((648, 2163, 864, 2207), "Inbox"), ((864, 2163, 1080, 2207), "Profile"),
    ]
    if badge:
        nodes.append(((58, 1883, 99, 1927), "Ad"))
    return nodes, SCREEN


class UiDevice(FakeDevice):
    # A phone that reports its on-screen text; None in the script means the phone gave nothing

    def __init__(self, script):
        super().__init__(size=SCREEN)
        self.script = iter(script)
        self.frames_read = 0

    def read_text(self):
        return next(self.script)

    def read_frame(self):
        self.frames_read += 1
        return super().read_frame()


@pytest.fixture
def ui_reader(device, monkeypatch):
    monkeypatch.setattr(collector, "SCREEN_READER", "ui")
    # Any use of OCR in these tests is a fallback and is scripted per test
    monkeypatch.setattr(collector, "_engine", ("test", mock.Mock(side_effect=AssertionError("OCR was not expected"))))


def test_ui_text_is_put_in_the_read_region_on_the_stored_scale():
    results, width, height = collector.ui_results(*ui_video("Shayne Vibes Truth", "#greenscreenvideo #greenscreen", "original sound - shayne", badge=True))
    assert (width, height) == (459, 570)
    # The tabs at the top and the counts on the right are outside the region
    assert [text for _, text, _ in results] == ["Shayne Vibes Truth", "#greenscreenvideo #greenscreen", "original sound - shayne", "Home", "Friends", "Inbox", "Ad"]
    box = results[-1][0]
    assert box[0] == [29.0, 371.5] and box[2] == [49.5, 393.5]


def test_ui_reader_finds_the_ad_and_logs_exact_text(ui_reader, monkeypatch, tmp_path):
    phone = UiDevice([
        ui_video("Shayne Vibes Truth", "#greenscreenvideo #greenscreen", "original sound - shayne.vibes31"),
        ui_video("visitqatar", "#doha #visitqatar #traveltiktok", "original sound - zeinab.zuhair", badge=True, shift=74),
    ])
    log = ImpressionLog(str(tmp_path), "session")
    collector.find_ad(phone, log)

    assert collector.monitoring_data["videos_watched"] == 2
    assert log.is_ad() and log.current["flag"] == "Ad"
    row = rows(log)[0]
    assert row["account"] == "Shayne Vibes Truth"
    assert row["caption"] == "#greenscreenvideo #greenscreen original sound - shayne.vibes31"
    assert row["is_ad"] is False
    assert row["text_source"] == "ui"
    # One screenshot per video, for the stored frame only
    assert phone.frames_read == 2


def test_ui_reader_falls_back_to_ocr_when_the_phone_gives_nothing(ui_reader, monkeypatch, tmp_path):
    phone = UiDevice([None, ui_video("coachdan", "Three drills every winger should know", "original sound - coachdan", badge=True)])
    monkeypatch.setattr(collector, "_engine", ("test", lambda region: load_results("synthetic", "ordinary_a")))
    log = ImpressionLog(str(tmp_path), "session")
    collector.find_ad(phone, log)

    row = rows(log)[0]
    assert row["account"] == "gardenwithmia"
    assert row["text_source"] == "test"
    assert log.current["text_source"] == "ui"


def test_ui_reader_sees_a_failed_swipe(ui_reader, monkeypatch, tmp_path):
    same = ui_video("Shayne Vibes Truth", "#greenscreenvideo #greenscreen", "original sound - shayne.vibes31")
    phone = UiDevice([same, same, ui_video("coachdan", "Three drills every winger should know", "original sound - coachdan", badge=True)])
    log = ImpressionLog(str(tmp_path), "session")
    collector.find_ad(phone, log)

    assert collector.monitoring_data["videos_watched"] == 2
    assert rows(log)[0]["swipe_attempts"] == 2


def test_phone_text_is_the_backup_when_ocr_reads_nothing(device, monkeypatch, tmp_path):
    # Default reader: OCR. The first video is in a script the engine cannot read, so OCR returns nothing
    assert collector.SCREEN_READER == "ocr"
    phone = UiDevice([ui_video("visitqatar", "#doha #visitqatar #traveltiktok", "original sound - zeinab", badge=True)])
    monkeypatch.setattr(collector, "_engine", ("test", lambda region: []))
    log = ImpressionLog(str(tmp_path), "session")
    collector.find_ad(phone, log)

    assert log.is_ad() and log.current["flag"] == "Ad"
    assert log.current["text"]["account"] == "visitqatar"
    assert log.current["text_source"] == "ui-backup"
    assert log.current["advance_confirmed"] is True


def test_phone_is_not_asked_when_ocr_reads_something(device, monkeypatch, tmp_path):
    phone = UiDevice([])
    phone.read_text = mock.Mock(side_effect=AssertionError("the phone should not be asked"))
    script = iter(["ordinary_a", "ad_sponsored"])
    monkeypatch.setattr(collector, "_engine", ("test", lambda region: load_results("synthetic", next(script))))
    log = ImpressionLog(str(tmp_path), "session")
    collector.find_ad(phone, log)

    assert rows(log)[0]["text_source"] == "test"


def test_a_blank_screen_with_no_backup_text_is_still_counted_and_flagged(device, monkeypatch, tmp_path):
    phone = UiDevice([None, ui_video("coachdan", "Three drills", "original sound - coachdan", badge=True)])
    script = iter([[], load_results("synthetic", "ad_sponsored")])
    monkeypatch.setattr(collector, "_engine", ("test", lambda region: next(script)))
    log = ImpressionLog(str(tmp_path), "session")
    collector.find_ad(phone, log)

    assert rows(log)[0]["advance_confirmed"] is False
    assert rows(log)[0]["text_source"] == "test"

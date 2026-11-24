# Python Standard Library Dependencies
import subprocess
from unittest import mock

# External Dependency Imports
import cv2
import numpy
import pytest

# Internal Dependency Imports
from tiktok_scraper.Core import phone

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# Checks the adb commands the phone device sends, with adb itself replaced by a stand-in.


class FakeAdb:
    # Records every adb call and answers from a table of {words in the command: output}

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, command, capture_output=True, timeout=None):
        arguments = command[1:]
        self.calls.append(arguments)
        text = " ".join(arguments)
        for key, output in self.answers.items():
            if key in text:
                if isinstance(output, Exception):
                    raise output
                return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")


class FakeStream:
    # Stands in for the live picture: remembers being started and stopped, and gives a scripted image

    def __init__(self, command):
        self.command = command
        self.events = []
        self.image = None

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")

    def latest(self, wait=3.0):
        return self.image


@pytest.fixture
def adb(monkeypatch):
    fake = FakeAdb()
    monkeypatch.setattr(phone.subprocess, "run", fake)
    monkeypatch.setattr(phone, "ScreenStream", FakeStream)
    monkeypatch.delenv("ADB_PATH", raising=False)
    monkeypatch.delenv("PHONE_STREAM", raising=False)
    return fake


def screenshot_png(width, height):
    ok, png = cv2.imencode(".png", numpy.full((height, width, 3), 90, dtype=numpy.uint8))
    return png.tobytes()


def test_list_phones(adb):
    adb.answers["devices"] = b"List of devices attached\nR58M12ABCDE\tdevice\nZY22XYZ987\tunauthorized\n\n"
    assert phone.list_phones() == [("R58M12ABCDE", "device"), ("ZY22XYZ987", "unauthorized")]


def test_choose_phone_with_one_ready_phone(adb):
    adb.answers["devices"] = b"List of devices attached\nR58M12ABCDE\tdevice\n"
    assert phone.choose_phone().serial == "R58M12ABCDE"


def test_choose_phone_refuses_an_unauthorised_phone(adb):
    adb.answers["devices"] = b"List of devices attached\nR58M12ABCDE\tunauthorized\n"
    assert phone.choose_phone() is None


def test_choose_phone_with_none_connected(adb):
    adb.answers["devices"] = b"List of devices attached\n\n"
    assert phone.choose_phone() is None


def test_choose_between_two_phones(adb, monkeypatch):
    adb.answers["devices"] = b"List of devices attached\nAAAA1111\tdevice\nBBBB2222\tdevice\n"
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    assert phone.choose_phone().serial == "BBBB2222"


def raw_screenshot(width, height, header_bytes=16, pixel_format=1):
    # A screen that is pure red, in the form "screencap" sends without -p
    import struct
    pixels = numpy.zeros((height, width, 4), dtype=numpy.uint8)
    pixels[:, :, 0] = 255
    pixels[:, :, 3] = 255
    header = struct.pack("<III", width, height, pixel_format) + b"\x00" * (header_bytes - 12)
    return header + pixels.tobytes()


@pytest.mark.parametrize("header_bytes", [12, 16])
def test_read_frame_uses_the_uncompressed_screenshot(adb, header_bytes):
    adb.answers["screencap"] = raw_screenshot(108, 240, header_bytes)
    device = phone.PhoneDevice("R58M12ABCDE")
    image = device.read_frame()
    assert image.shape == (240, 108, 3)
    # Red on the phone is red in the image (OpenCV keeps blue first)
    assert image[0, 0].tolist() == [0, 0, 255]
    assert device.size == (108, 240)
    assert adb.calls == [["-s", "R58M12ABCDE", "exec-out", "screencap"]]


def test_read_frame_falls_back_to_png_and_stays_there(adb):
    # This phone answers the uncompressed request with something else; the PNG request works
    adb.answers["screencap -p"] = screenshot_png(1080, 2400)
    adb.answers["screencap"] = raw_screenshot(108, 240, pixel_format=4)
    device = phone.PhoneDevice("R58M12ABCDE")
    assert device.read_frame().shape == (2400, 1080, 3)
    assert device.read_frame().shape == (2400, 1080, 3)
    assert [call[2:] for call in adb.calls] == [["exec-out", "screencap"], ["exec-out", "screencap", "-p"], ["exec-out", "screencap", "-p"]]


@pytest.mark.parametrize("raw", [b"", b"short", b"\x00" * 40])
def test_decode_raw_screenshot_is_none_for_anything_else(raw):
    assert phone.decode_raw_screenshot(raw) is None


def test_read_frame_raises_on_rubbish(adb):
    adb.answers["screencap"] = b"not a screenshot of either kind"
    with pytest.raises(RuntimeError):
        phone.PhoneDevice("R58M12ABCDE").read_frame()


def test_swipe_goes_to_this_phone_in_the_middle_of_its_screen(adb):
    device = phone.PhoneDevice("R58M12ABCDE")
    device.size = (1080, 2400)
    assert device.swipe_up() == {"x": 540, "y_start": 1800, "y_end": 600, "duration_ms": 300}
    assert adb.calls[-1] == ["-s", "R58M12ABCDE", "shell", "input swipe 540 1800 540 600 300"]


def test_swipe_reads_the_screen_size_first_if_it_is_not_known(adb):
    adb.answers["screencap"] = raw_screenshot(720, 1600)
    device = phone.PhoneDevice("R58M12ABCDE")
    assert device.swipe_up()["x"] == 360
    assert adb.calls[-1][-1] == "input swipe 360 1200 360 400 300"


def test_refresh_presses_back_and_trim_does_nothing(adb):
    device = phone.PhoneDevice("R58M12ABCDE")
    device.trim()
    assert adb.calls == []
    device.refresh()
    assert adb.calls[-1][-1] == "input keyevent KEYCODE_BACK"


def test_record_ad_records_copies_and_cleans_up(adb, tmp_path):
    adb.answers["cat /sdcard/tiktok_scraper_ad.mp4"] = b"video bytes"
    device = phone.PhoneDevice("R58M12ABCDE")
    result = device.record_ad(str(tmp_path), 20)
    assert result == {"video_path": str(tmp_path / "ad.mp4")}
    assert (tmp_path / "ad.mp4").read_bytes() == b"video bytes"
    commands = [" ".join(call[2:]) for call in adb.calls]
    assert commands == [
        "shell screenrecord --time-limit 20 --bit-rate 4000000 /sdcard/tiktok_scraper_ad.mp4",
        "exec-out cat /sdcard/tiktok_scraper_ad.mp4",
        "shell rm /sdcard/tiktok_scraper_ad.mp4"
    ]


def test_prepare_and_close_set_stay_awake(adb):
    adb.answers["wm size"] = b"Physical size: 1080x2280\n"
    device = phone.PhoneDevice("R58M12ABCDE")
    device.prepare()
    device.close()
    assert [call[-1] for call in adb.calls] == ["input keyevent KEYCODE_WAKEUP", "svc power stayon true", "wm dismiss-keyguard", "wm size", "svc power stayon false"]
    assert device.size == (1080, 2280)


def test_read_text_reports_the_full_screen_size_once_known(adb):
    adb.answers["uiautomator dump /dev/tty"] = UI_DUMP.replace(b"[0,0][1080,2280]", b"[0,0][1080,2226]")
    device = phone.PhoneDevice("R58M12ABCDE")
    assert device.read_text()[1] == (1080, 2226)
    device.size = (1080, 2280)
    assert device.read_text()[1] == (1080, 2280)


def test_describe_reads_model_and_tiktok_version(adb):
    adb.answers.update({
        "ro.product.model": b"SM-A536W\n",
        "ro.product.manufacturer": b"samsung\n",
        "ro.build.version.release": b"13\n",
        "wm size": b"Physical size: 1080x2400\n",
        "dumpsys package com.zhiliaoapp.musically": b"  Packages:\n    versionCode=2023700030\n    versionName=37.0.3\n"
    })
    details = phone.PhoneDevice("R58M12ABCDE").describe()
    assert details == {
        "device_kind": "phone", "serial": "R58M12ABCDE", "model": "SM-A536W", "manufacturer": "samsung",
        "android_version": "13", "screen_size": "Physical size: 1080x2400",
        "tiktok_package": "com.zhiliaoapp.musically", "tiktok_version": "37.0.3"
    }


def test_describe_without_tiktok_installed(adb):
    details = phone.PhoneDevice("R58M12ABCDE").describe()
    assert details["tiktok_version"] is None
    assert details["model"] is None


def test_adb_failure_raises_with_the_message(monkeypatch):
    failed = subprocess.CompletedProcess(["adb"], 1, stdout=b"", stderr=b"error: device offline")
    monkeypatch.setattr(phone.subprocess, "run", lambda *args, **kwargs: failed)
    with pytest.raises(RuntimeError, match="device offline"):
        phone.PhoneDevice("R58M12ABCDE").refresh()


def test_missing_adb_gives_a_plain_message(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(phone.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="adb was not found"):
        phone.list_phones()


# ------------------------------ on-screen text from the phone ------------------------------

UI_DUMP = (
    b"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
    b"<node index=\"0\" text=\"\" class=\"android.widget.FrameLayout\" bounds=\"[0,0][1080,2280]\">"
    b"<node text=\"For You\" resource-id=\"x:id/text1\" class=\"android.widget.TextView\" bounds=\"[567,101][788,297]\" />"
    b"<node text=\"Shayne Vibes Truth\" resource-id=\"x:id/title\" class=\"android.widget.TextView\" bounds=\"[41,1710][536,1778]\" />"
    b"<node text=\"\" content-desc=\"Like\" class=\"android.widget.ImageView\" bounds=\"[894,949][1046,1101]\" />"
    b"<node text=\"Ad\" resource-id=\"x:id/fux\" class=\"android.widget.TextView\" bounds=\"[58,1883][99,1927]\" />"
    b"<node text=\"hidden\" class=\"android.widget.TextView\" bounds=\"[0,0][0,0]\" />"
    b"</node></hierarchy>"
)


def test_parse_ui_dump_keeps_visible_text_and_the_screen_size():
    nodes, size = phone.parse_ui_dump(UI_DUMP + b"UI hierchary dumped to: /dev/tty\n")
    assert size == (1080, 2280)
    assert nodes == [((567, 101, 788, 297), "For You"), ((41, 1710, 536, 1778), "Shayne Vibes Truth"), ((58, 1883, 99, 1927), "Ad")]


@pytest.mark.parametrize("raw", [b"", b"ERROR: could not get idle state.\n", b"<?xml version='1.0'?><hierarchy><node bounds=", b"<?xml?><hierarchy></hierarchy>"])
def test_parse_ui_dump_is_none_without_a_usable_dump(raw):
    assert phone.parse_ui_dump(raw) is None


def test_read_text_takes_the_dump_straight_from_adb(adb):
    adb.answers["uiautomator dump /dev/tty"] = UI_DUMP
    device = phone.PhoneDevice("R58M12ABCDE")
    nodes, size = device.read_text()
    assert size == (1080, 2280) and len(nodes) == 3
    assert adb.calls == [["-s", "R58M12ABCDE", "exec-out", "uiautomator", "dump", "/dev/tty"]]


def test_read_text_switches_to_a_file_on_phones_that_do_not_print_the_dump(adb):
    adb.answers["cat /sdcard/tiktok_scraper_ui.xml"] = UI_DUMP
    device = phone.PhoneDevice("R58M12ABCDE")
    assert len(device.read_text()[0]) == 3
    assert len(device.read_text()[0]) == 3
    commands = [" ".join(call[2:]) for call in adb.calls]
    # The direct way is tried once, then the file way is used from then on
    assert commands == [
        "exec-out uiautomator dump /dev/tty",
        "shell uiautomator dump /sdcard/tiktok_scraper_ui.xml", "exec-out cat /sdcard/tiktok_scraper_ui.xml",
        "shell uiautomator dump /sdcard/tiktok_scraper_ui.xml", "exec-out cat /sdcard/tiktok_scraper_ui.xml",
    ]


def test_read_text_is_none_when_the_phone_cannot_dump(adb):
    adb.answers["uiautomator dump /dev/tty"] = UI_DUMP
    device = phone.PhoneDevice("R58M12ABCDE")
    assert device.read_text() is not None
    adb.answers["uiautomator dump /dev/tty"] = b"ERROR: could not get idle state.\n"
    assert device.read_text() is None


def test_an_adb_command_that_hangs_is_reported_plainly(monkeypatch):
    def hang(command, capture_output=True, timeout=None):
        raise subprocess.TimeoutExpired(command, timeout)
    monkeypatch.setattr(phone.subprocess, "run", hang)
    with pytest.raises(RuntimeError, match="did not answer within 30 seconds"):
        phone.PhoneDevice("R58M12ABCDE").read_frame()


# ----------------------------------- live picture -----------------------------------

def test_prepare_starts_the_live_picture_and_close_stops_it(adb):
    device = phone.PhoneDevice("R58M12ABCDE")
    device.prepare()
    assert device.stream.command == ["adb", "-s", "R58M12ABCDE"]
    device.close()
    assert device.stream.events == ["start", "stop"]


def test_live_picture_can_be_switched_off(adb, monkeypatch):
    monkeypatch.setenv("PHONE_STREAM", "0")
    device = phone.PhoneDevice("R58M12ABCDE")
    device.prepare()
    assert device.stream is None


def test_read_frame_uses_the_live_picture_without_asking_the_phone(adb):
    device = phone.PhoneDevice("R58M12ABCDE")
    device.prepare()
    device.stream.image = numpy.zeros((2280, 1080, 3), dtype=numpy.uint8)
    asked_before = len(adb.calls)
    assert device.read_frame().shape == (2280, 1080, 3)
    assert len(adb.calls) == asked_before


def test_read_frame_takes_a_screenshot_when_there_is_no_live_picture(adb):
    adb.answers["screencap"] = raw_screenshot(108, 240)
    device = phone.PhoneDevice("R58M12ABCDE")
    device.prepare()
    assert device.read_frame().shape == (240, 108, 3)


def test_the_live_picture_is_stopped_while_an_ad_is_recorded(adb, tmp_path):
    device = phone.PhoneDevice("R58M12ABCDE")
    device.prepare()
    device.record_ad(str(tmp_path), 20)
    assert device.stream.events == ["start", "stop", "start"]


def test_the_live_picture_comes_back_even_if_the_recording_fails(adb, tmp_path):
    device = phone.PhoneDevice("R58M12ABCDE")
    device.prepare()
    adb.answers["screenrecord"] = RuntimeError("device offline")
    with pytest.raises(RuntimeError):
        device.record_ad(str(tmp_path), 20)
    assert device.stream.events == ["start", "stop", "start"]

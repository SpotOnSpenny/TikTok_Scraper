# Python Standard Library Dependencies
import threading
import time
from unittest import mock

# External Dependency Imports
import numpy
import pytest

# Internal Dependency Imports
from tiktok_scraper.Core import stream

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# The live-picture stream with the phone's recorder and the video decoder replaced by
# stand-ins: each "chunk" the recorder sends is one byte whose value becomes one frame.


class FakeFrame:

    def __init__(self, value):
        self.value = value

    def to_ndarray(self, format):
        return numpy.full((4, 2, 3), self.value, dtype=numpy.uint8)


class FakeRecorder:
    # Stands in for the adb process: stdout.read() hands out the scripted chunks, then waits to be stopped

    def __init__(self, chunks, ends=False):
        self.chunks = list(chunks)
        self.ends = ends
        self.stopped = threading.Event()
        self.stdout = self

    def read(self, size):
        if self.chunks:
            return self.chunks.pop(0)
        if self.ends:
            return b""
        self.stopped.wait(timeout=10)
        return b""

    def terminate(self):
        self.stopped.set()

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def recorders(monkeypatch):
    started = []
    script = []

    def popen(command, **kwargs):
        recorder = script.pop(0) if script else FakeRecorder([])
        started.append((command, recorder))
        return recorder
    monkeypatch.setattr(stream.subprocess, "Popen", popen)
    monkeypatch.setattr(stream, "make_decoder", lambda: (lambda chunk: [FakeFrame(chunk[0])]))
    return script, started


def test_latest_gives_the_newest_frame(recorders):
    script, started = recorders
    script.append(FakeRecorder([b"\x05", b"\x09"]))
    live = stream.ScreenStream(["adb", "-s", "PHONE1"])
    live.start()
    try:
        time.sleep(0.2)
        assert live.latest().tolist() == numpy.full((4, 2, 3), 9).tolist()
        assert started[0][0][:5] == ["adb", "-s", "PHONE1", "exec-out", "screenrecord"]
        assert "--output-format=h264" in started[0][0] and started[0][0][-1] == "-"
    finally:
        live.stop()


def test_latest_waits_for_the_picture_and_gives_none_if_it_never_comes(recorders):
    live = stream.ScreenStream(["adb"])
    live.start()
    try:
        began = time.time()
        assert live.latest(wait=0.3) is None
        assert time.time() - began >= 0.3
    finally:
        live.stop()


def test_recorder_is_started_again_when_it_ends(recorders):
    script, started = recorders
    script.extend([FakeRecorder([b"\x01"], ends=True), FakeRecorder([b"\x02"])])
    live = stream.ScreenStream(["adb"])
    live.start()
    try:
        time.sleep(0.3)
        assert len(started) == 2
        assert live.latest()[0, 0, 0] == 2
    finally:
        live.stop()


def test_stop_ends_the_recorder_and_start_brings_it_back(recorders):
    script, started = recorders
    first, second = FakeRecorder([b"\x01"]), FakeRecorder([b"\x07"])
    script.extend([first, second])
    live = stream.ScreenStream(["adb"])
    live.start()
    time.sleep(0.2)
    live.stop()
    assert first.stopped.is_set()
    # While stopped there is no live picture, and no waiting for one
    began = time.time()
    assert live.latest(wait=2) is None
    assert time.time() - began < 0.5
    live.start()
    try:
        time.sleep(0.2)
        assert live.latest()[0, 0, 0] == 7
    finally:
        live.stop()


def test_a_recorder_that_keeps_ending_at_once_is_given_up(recorders, capsys):
    script, started = recorders
    script.extend([FakeRecorder([], ends=True) for _ in range(5)])
    live = stream.ScreenStream(["adb"])
    live.start()
    time.sleep(0.5)
    assert live.failed is True
    assert len(started) == stream.QUICK_ENDS_ALLOWED
    assert live.latest() is None
    assert "Using screenshots instead" in capsys.readouterr().out
    # Once given up it is not started again
    live.start()
    time.sleep(0.1)
    assert len(started) == stream.QUICK_ENDS_ALLOWED


def test_a_missing_decoder_means_screenshots(monkeypatch, capsys):
    def no_decoder():
        raise ImportError("No module named 'av'")
    monkeypatch.setattr(stream, "make_decoder", no_decoder)
    live = stream.ScreenStream(["adb"])
    live.start()
    time.sleep(0.2)
    assert live.failed is True
    assert live.latest() is None
    assert "Using screenshots instead" in capsys.readouterr().out

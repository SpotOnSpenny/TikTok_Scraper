# Python Standard Library Dependencies
import os
import re
import subprocess
import xml.etree.ElementTree as ElementTree

# External Dependency Imports
import cv2
import numpy

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# A real Android phone, driven over USB with adb (Android platform-tools). The phone needs
# USB debugging switched on and this computer authorised on it. Nothing here uses the
# computer's mouse, keyboard or screen, so several phones can run at once, each from its
# own copy of the scraper.
#
# adb is found on the PATH, or at the location given in the ADB_PATH environment variable.

TIKTOK_PACKAGES = ["com.zhiliaoapp.musically", "com.ss.android.ugc.trill"]
RECORDING_ON_PHONE = "/sdcard/tiktok_scraper_ad.mp4"
UI_DUMP_ON_PHONE = "/sdcard/tiktok_scraper_ui.xml"
SWIPE_MILLISECONDS = 300


def parse_ui_dump(raw):
    # Turns the output of "uiautomator dump" into ([((left, top, right, bottom), text)], (width, height)):
    # every element on screen that carries text, and the size of the screen. Returns None if raw holds no dump.
    start = raw.find(b"<?xml")
    end = raw.rfind(b"</hierarchy>")
    if start < 0 or end < 0:
        return None
    try:
        root = ElementTree.fromstring(raw[start:end + len(b"</hierarchy>")])
    except ElementTree.ParseError:
        return None
    nodes = []
    width, height = 0, 0
    for node in root.iter("node"):
        match = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.get("bounds") or "")
        if not match:
            continue
        left, top, right, bottom = [int(value) for value in match.groups()]
        width, height = max(width, right), max(height, bottom)
        text = (node.get("text") or "").strip()
        if text and right > left and bottom > top:
            nodes.append(((left, top, right, bottom), text))
    if width == 0 or height == 0:
        return None
    return nodes, (width, height)


def adb_path():
    return os.environ.get("ADB_PATH") or "adb"


def run_adb(arguments, timeout=30):
    # Runs adb and returns what it printed, as bytes. Raises if adb reports a failure.
    try:
        result = subprocess.run([adb_path()] + list(arguments), capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("adb was not found. Install Android platform-tools and add it to the PATH, or set ADB_PATH.")
    if result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(arguments)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def list_phones():
    # Returns [(serial, state)] for every phone adb can see. state is "device" when ready,
    # "unauthorized" when the phone has not yet allowed this computer.
    lines = run_adb(["devices"]).decode(errors="replace").splitlines()
    phones = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2:
            phones.append((parts[0], parts[1]))
    return phones


def choose_phone():
    # Asks which phone to use when more than one is connected. Returns a PhoneDevice, or None.
    phones = list_phones()
    if len(phones) == 0:
        print("No phone found. Plug it in with USB debugging switched on and try again.")
        return None
    for index, (serial, state) in enumerate(phones):
        print(f"{index} - Phone found: {serial} ({state})")
    index = 0
    if len(phones) > 1:
        answer = input("Which phone should be used? (Enter its index): \n")
        if not answer.isdigit() or int(answer) > len(phones) - 1:
            print("Phone not found. Please try again.")
            return None
        index = int(answer)
    serial, state = phones[index]
    if state != "device":
        print(f"Phone {serial} is '{state}'. Unlock it and accept the 'Allow USB debugging' prompt, then try again.")
        return None
    return PhoneDevice(serial)


class PhoneDevice:

    def __init__(self, serial):
        self.serial = serial
        self.size = None
        # None until the first dump shows whether this phone can print it straight to adb
        self.dump_to_stdout = None

    def adb(self, *arguments, timeout=30):
        return run_adb(["-s", self.serial] + list(arguments), timeout=timeout)

    def shell(self, command, timeout=30):
        return self.adb("shell", command, timeout=timeout).decode(errors="replace").strip()

    def prepare(self):
        # Wake the screen and keep it on while the phone is plugged in
        self.shell("input keyevent KEYCODE_WAKEUP")
        self.shell("svc power stayon true")
        # The full screen size, so positions mean the same whether they come from a screenshot or from the phone's text
        match = re.search(r"(\d+)x(\d+)", self.shell("wm size"))
        if match:
            self.size = (int(match.group(1)), int(match.group(2)))

    def close(self):
        # Let the screen time out normally again
        self.shell("svc power stayon false")

    def read_frame(self):
        png = self.adb("exec-out", "screencap", "-p")
        image = cv2.imdecode(numpy.frombuffer(png, dtype=numpy.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"Could not read a screenshot from phone {self.serial}")
        self.size = (image.shape[1], image.shape[0])
        return image

    def read_text(self):
        # The text Android reports for what is on screen, with where each piece sits: exact, and no
        # image to analyse. Returns (nodes, screen size) as parse_ui_dump does, or None when the phone
        # gives no dump (it can fail while the screen will not hold still), so the caller can fall
        # back to reading a screenshot.
        try:
            found = None
            if self.dump_to_stdout is not False:
                found = parse_ui_dump(self.adb("exec-out", "uiautomator", "dump", "/dev/tty", timeout=20))
                if found is not None:
                    self.dump_to_stdout = True
                elif self.dump_to_stdout is None:
                    # This phone does not print the dump directly; write it to a file and read that from now on
                    self.dump_to_stdout = False
            if self.dump_to_stdout is False:
                self.shell(f"uiautomator dump {UI_DUMP_ON_PHONE}", timeout=20)
                found = parse_ui_dump(self.adb("exec-out", "cat", UI_DUMP_ON_PHONE, timeout=20))
        except (RuntimeError, subprocess.TimeoutExpired):
            return None
        if found is None:
            return None
        nodes, dumped_size = found
        # The dump leaves out the system bar at the bottom, so use the full screen size when it is known
        return nodes, self.size or dumped_size

    def swipe_up(self):
        # Drag from three quarters of the way down the screen to one quarter, in the middle
        if self.size is None:
            self.read_frame()
        width, height = self.size
        x = width // 2
        y_start = (height * 3) // 4
        y_end = height // 4
        self.shell(f"input swipe {x} {y_start} {x} {y_end} {SWIPE_MILLISECONDS}")
        return {"x": x, "y_start": y_start, "y_end": y_end, "duration_ms": SWIPE_MILLISECONDS}

    def trim(self):
        # Memory trimming is an emulator feature; a phone looks after itself
        pass

    def refresh(self):
        print("Refreshing feed")
        self.shell("input keyevent KEYCODE_BACK")

    def record_ad(self, folder, seconds):
        # Record the phone's screen on the phone, then copy the video over. No sound is recorded.
        video_path = os.path.join(folder, "ad.mp4")
        self.shell(f"screenrecord --time-limit {int(seconds)} --bit-rate 4000000 {RECORDING_ON_PHONE}", timeout=seconds + 30)
        # Copied through adb's output, not "adb pull", so that no file path is handed to adb: the
        # Windows adb.exe can then be used from WSL, where it would not understand a Linux path
        with open(video_path, "wb") as file:
            file.write(self.adb("exec-out", "cat", RECORDING_ON_PHONE, timeout=180))
        self.shell(f"rm {RECORDING_ON_PHONE}")
        return {"video_path": video_path}

    def describe(self):
        # What the session record should say about this phone. Anything that cannot be read is None.
        details = {"device_kind": "phone", "serial": self.serial}
        for key, command in [
            ("model", "getprop ro.product.model"),
            ("manufacturer", "getprop ro.product.manufacturer"),
            ("android_version", "getprop ro.build.version.release"),
            ("screen_size", "wm size")
        ]:
            try:
                details[key] = self.shell(command) or None
            except RuntimeError:
                details[key] = None
        details["tiktok_package"] = None
        details["tiktok_version"] = None
        for package in TIKTOK_PACKAGES:
            try:
                match = re.search(r"versionName=(\S+)", self.shell(f"dumpsys package {package}"))
            except RuntimeError:
                match = None
            if match:
                details["tiktok_package"] = package
                details["tiktok_version"] = match.group(1)
                break
        return details


# Test code below
if __name__ == '__main__':
    # Quick check of a connected phone: prints its details, saves one screenshot and swipes once
    phone = choose_phone()
    if phone is not None:
        phone.prepare()
        print(phone.describe())
        cv2.imwrite("phone_check.jpg", phone.read_frame())
        print("Saved phone_check.jpg, now swiping once:", phone.swipe_up())

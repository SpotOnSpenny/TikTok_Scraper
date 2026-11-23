# Python Standard Library Dependencies
import time
import os
import ctypes
import contextlib

# External Dependency Imports
import win32gui
import win32con
import win32api
import win32ui
import cv2

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# The TikTok app in a BlueStacks window on this computer. BlueStacksDevice is the "device"
# the collection loop in collector.py drives; the swipe uses the real mouse cursor, so only
# one emulator can run per desktop and the mouse must be left alone.

ctypes.windll.user32.SetProcessDPIAware()

@contextlib.contextmanager
def gdi_context(dcObj, cDC, wDC, dataBitMap, hwnd):
    try:
        yield
    finally:
        dcObj.DeleteDC()
        cDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, wDC)
        win32gui.DeleteObject(dataBitMap.GetHandle())

def find_bluestacks_window(window_name):
    print("locating window")
    def callback(hwnd, results):
        window_title = win32gui.GetWindowText(hwnd)
        if f"{window_name}" in window_title:
            results.append((hwnd, window_title))
    results = []
    win32gui.EnumWindows(callback, results)
    if len(results) > 0:
        index = 0
        for hwnd, window_title in results:
            print(f"{index} - Window found: {window_title}. (HWND: {hwnd})")
            index += 1
        correct_index = input("Which item was the correct window? (Enter it's index, enter 'Not found' if no window is correct): \n")
        if correct_index.lower() == "not found" or int(correct_index) > len(results) - 1:
            print("Window not found. Please try again.")
            return None
    else:
        print("Window not found Please try again.")
        return None
    hwndChild = win32gui.GetWindow(results[int(correct_index)][0], win32con.GW_CHILD)
    return hwndChild

def scroll_down(hwnd, rect):
    # Sends the swipe and returns its parameters. Counting happens in collector.find_ad,
    # once the next read confirms the video changed.
    x = (rect[2] - rect[0]) // 2
    y = (rect[3] - rect[1]) // 4
    win32api.SetCursorPos((x, y*3))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, -(y*2), 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
    return {"x": x, "y_start": y*3, "y_end": y}

def take_screencaps(hwnd, rect, file_name):
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    
    wDC = win32gui.GetWindowDC(hwnd)
    dcObj=win32ui.CreateDCFromHandle(wDC)
    cDC=dcObj.CreateCompatibleDC()
    dataBitMap = win32ui.CreateBitmap()
    dataBitMap.CreateCompatibleBitmap(dcObj, w, h)
    cDC.SelectObject(dataBitMap)
    with gdi_context(dcObj, cDC, wDC, dataBitMap, hwnd):
        cDC.BitBlt((0,0),(w, h) , dcObj, (0,0), win32con.SRCCOPY)
        dataBitMap.SaveBitmapFile(cDC, file_name)

    return 

class BlueStacksDevice:

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.rect = win32gui.GetWindowRect(hwnd)

    def prepare(self):
        pass

    def close(self):
        pass

    def read_frame(self):
        self.rect = win32gui.GetWindowRect(self.hwnd)
        check_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad_check_screencap")
        screencap_path = os.path.join(check_folder, "screencap.png")
        take_screencaps(self.hwnd, self.rect, screencap_path)
        return cv2.imread(screencap_path)

    def swipe_up(self):
        return scroll_down(self.hwnd, self.rect)

    def trim(self):
        # We trim twice since the first time sometimes just registers the hotkey, the second removes the memory
        trim_mem()
        time.sleep(1)
        trim_mem()
        time.sleep(1)

    def refresh(self):
        # CTRL + SHIFT + 2 is the BlueStacks back button
        refresh_feed()

    def record_ad(self, folder, seconds):
        # Screenshots as fast as they come, at most 20 a second, numbered from 1; stitched into a video later
        end_time = time.time() + seconds
        screenshot_count = 0
        while time.time() < end_time:
            screenshot_count += 1
            screenshot_timer = time.time()
            take_screencaps(self.hwnd, self.rect, f"{folder}/{screenshot_count}.png")
            screenshot_time = time.time() - screenshot_timer
            if screenshot_time < 0.05:
                wait_time = 0.05 - screenshot_time
                time.sleep(wait_time)
        return {"fps": screenshot_count / seconds}

    def describe(self):
        return {
            "device_kind": "bluestacks",
            "window_title": win32gui.GetWindowText(win32gui.GetParent(self.hwnd) or self.hwnd),
            "window_size": [self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]]
        }

def trim_mem():
    print("Trimming memory")
    # Press and hold Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    # Press and hold Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
    # Press T
    win32api.keybd_event(0x54, 0, 0, 0)  # 0x54 is the virtual key code for 'T'
    # Release T
    win32api.keybd_event(0x54, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

def refresh_feed():
    print("Refreshing feed")
    # Press and hold Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    # Press and hold Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
    # Press 2
    win32api.keybd_event(0x32, 0, 0, 0)  # 0x32 is the virtual key code for '2'
    # Release 2
    win32api.keybd_event(0x32, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Shift
    win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Release Ctrl
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

# Test code below
if __name__ == '__main__':
    hwnd = find_bluestacks_window("BlueStacks App Player 13")
    rect = win32gui.GetWindowRect(hwnd)
    scroll_down(hwnd, rect)
    refresh_feed()
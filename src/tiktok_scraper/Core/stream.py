# Python Standard Library Dependencies
import subprocess
import threading
import time

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# A live picture of a phone's screen. The phone's own screen recorder sends its video
# down the adb connection as it is made; a thread here decodes it and keeps the newest
# frame, so the screen can be looked at in a few milliseconds without asking the phone
# for a screenshot (about 0.6 s).
#
# The recorder stops by itself after 3 minutes, so it is started again whenever it ends.
# It only sends a frame when the screen changes, so on a still screen the newest frame can
# be old and still be right.
#
# Needs the PyAV package (pip install av) to decode the video. Without it, or if the phone
# will not stream, the caller falls back to screenshots.

# Seconds each recorder run is asked to last (the phone allows at most 180)
RUN_SECONDS = 170
# High enough that the picture is sharp straight after a swipe: at 4 Mbit/s the frame 0.3 s after a
# swipe had about two thirds of its settled detail and was visibly blocky; at 12 it was indistinguishable
BIT_RATE = 12000000
# If the recorder ends this soon after starting, this many times in a row, the stream is given up
QUICK_END_SECONDS = 5
QUICK_ENDS_ALLOWED = 3


def make_decoder():
    # Returns a function that takes a chunk of H.264 bytes and returns the frames completed by it
    import av
    codec = av.CodecContext.create("h264", "r")

    def decode(chunk):
        frames = []
        for packet in codec.parse(chunk):
            frames.extend(codec.decode(packet))
        return frames
    return decode


class ScreenStream:

    def __init__(self, adb_command):
        # adb_command is the start of the command line, for example ["adb", "-s", serial]
        self.command = list(adb_command) + [
            "exec-out", "screenrecord", "--output-format=h264",
            "--bit-rate", str(BIT_RATE), "--time-limit", str(RUN_SECONDS), "-"
        ]
        self.frame = None
        self.frames_decoded = 0
        # Set while the frame on hand is from the recorder run that is going now
        self.live = threading.Event()
        self.failed = False
        self.wanted = False
        self.process = None
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        # Begins streaming (or begins again after stop). Returns straight away; latest() waits for the picture.
        with self.lock:
            if self.failed or self.wanted:
                return
            self.wanted = True
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def stop(self):
        # Ends the stream and waits for it to be gone, so that the phone's recorder is free for other use
        with self.lock:
            self.wanted = False
            self.live.clear()
            process, thread = self.process, self.thread
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def run(self):
        quick_ends = 0
        while self.wanted:
            started = time.time()
            try:
                decode = make_decoder()
                process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            except Exception as error:
                print(f"The phone's live picture could not be started ({type(error).__name__}: {error}). Using screenshots instead.")
                self.failed = True
                break
            self.process = process
            try:
                while self.wanted:
                    chunk = process.stdout.read(65536)
                    if not chunk:
                        break
                    for frame in decode(chunk):
                        self.frame = frame
                        self.frames_decoded += 1
                        self.live.set()
            except Exception as error:
                print(f"The phone's live picture stopped ({type(error).__name__}: {error}).")
            finally:
                self.live.clear()
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    pass
            if not self.wanted:
                break
            # The recorder ended by itself: normal after RUN_SECONDS, a fault if it keeps ending at once
            quick_ends = quick_ends + 1 if time.time() - started < QUICK_END_SECONDS else 0
            if quick_ends >= QUICK_ENDS_ALLOWED:
                print("The phone's live picture keeps stopping. Using screenshots instead.")
                self.failed = True
                break
        self.live.clear()
        self.wanted = False if self.failed else self.wanted

    def latest(self, wait=3.0):
        # The newest picture of the screen as a BGR image, or None if there is no live picture.
        # While the recorder is starting (or starting again) this waits up to `wait` seconds for it.
        if self.failed or not self.wanted:
            return None
        if not self.live.wait(timeout=wait):
            return None
        frame = self.frame
        if frame is None:
            return None
        return frame.to_ndarray(format="bgr24")


# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code

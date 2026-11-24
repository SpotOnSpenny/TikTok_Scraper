# TikTok Scraper

Scrolls the TikTok For You feed on an Android phone, logs one row and one screenshot for
every video it sees, and records and uploads the ads it finds.

The phone is plugged into a computer by USB and driven from there. Nothing is installed on the
phone apart from TikTok. A BlueStacks emulator window and the TikTok website can also be used
(see [Other interfaces](#other-interfaces)), but the phone is the route that is tested and
recommended.

- [What you need](#what-you-need)
- [One-time setup](#one-time-setup)
- [Running a collection](#running-a-collection)
- [What a session produces](#what-a-session-produces)
- [Quick test run](#quick-test-run)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)
- [Other interfaces](#other-interfaces)
- [For developers](#for-developers)

## What you need

- A computer running Windows, with internet. (Linux and WSL also work for the phone route.)
- An Android phone (tested on a Pixel 4 with Android 12), with a USB cable that carries data.
- A Wi-Fi network for the phone. The USB cable carries commands only, not internet.
- A TikTok account for the phone to be logged into.
- An AWS access key that can write to the S3 bucket the ads are uploaded to.
- No VPN, on the phone or the computer. TikTok serves no ads through one.

## One-time setup

### 1. Get the code

Install [Git](https://git-scm.com/downloads) if the computer does not have it, then in a terminal:

```
git clone https://github.com/SpotOnSpenny/TikTok_Scraper.git
cd TikTok_Scraper
```

Until the phone work is merged into `main`, switch to its branch:

```
git checkout per-impression-log
```

### 2. Create the Python environment

The collector is tested on Python 3.12, and one of its parts does not work on 3.13. The conda
environment in this repository pins the right version, whatever Python the computer has.

1. Install Miniforge (a small version of conda). On Windows:
   ```
   winget install -e --id CondaForge.Miniforge3
   ```
2. Open **Miniforge Prompt** from the Start menu and go to the `TikTok_Scraper` folder.
3. Create the environment. This downloads a few gigabytes and takes several minutes:
   ```
   conda env create -f environment.yml
   ```
4. Activate it. Do this every time you open a new terminal to run the collector:
   ```
   conda activate tiktok-scraper
   ```

After pulling new code later, bring the environment up to date with
`conda env update -f environment.yml --prune`.

### 3. Install adb

`adb` is Google's tool for controlling an Android phone over USB. On Windows:

```
winget install Google.PlatformTools
```

Close and reopen the terminal, then check it runs:

```
adb version
```

If the command is not found, see [Troubleshooting](#troubleshooting).

### 4. Fill in the settings file

Copy `src/.envexample` to a new file called `src/.env` and open it in a text editor. Fill in:

| Setting | What to put |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | The AWS key that can write to the bucket |
| `S3_BUCKET` | The name of the bucket recorded ads are uploaded to |
| `PAPERTRAIL_PORT` | The Papertrail port number, for the 15-minute progress messages. Leave empty to go without them |
| `LOGGING_LEVEL` | `info` |
| `ADB_PATH` | Leave empty unless `adb` is not on the PATH; then the full path to `adb.exe` |

Leave the other lines empty to use the defaults. They are explained under [Settings](#settings).
`src/.env` holds secrets and is never committed to git.

### 5. Prepare the phone

1. **Switch on USB debugging.** In the phone's Settings, open *About phone* and tap *Build
   number* seven times. Go back, open *System > Developer options*, and switch on *USB debugging*.
2. **Remove the screen lock** (Settings > Security > Screen lock > None), so the phone never
   needs unlocking during a run.
3. **Connect the phone to Wi-Fi**, with no VPN app running on it.
4. **Install TikTok** from the Play Store (the full app, not TikTok Lite) and log in with the
   account you are collecting under. Do not log in with "Continue with Google", and decline
   contact syncing.
5. **Plug the phone into the computer.** A prompt appears on the phone: *Allow USB debugging?*
   Tick *Always allow from this computer* and accept.
6. **Check the computer can see it:**
   ```
   adb devices
   ```
   The phone should be listed with the word `device` beside it. `unauthorized` means the prompt
   on the phone has not been accepted yet.

## Running a collection

1. Turn off any VPN on the computer and the phone.
2. Plug in the phone, unlock it, and open TikTok on the **For You** feed.
3. Open Miniforge Prompt, go to the repository, and activate the environment:
   ```
   conda activate tiktok-scraper
   cd src
   ```
4. **Check the phone first** (optional, takes a few seconds):
   ```
   python -m tiktok_scraper.Core.phone
   ```
   It prints the phone's model and TikTok version, saves `phone_check.jpg`, and swipes once.
   The video on the phone should change.
5. **Start the collector:**
   ```
   python -m tiktok_scraper.Core.main
   ```
6. **Answer its questions:**

   | Question | Answer |
   |---|---|
   | Interface | `phone` |
   | Time in hours | A whole number, 1 or more |
   | City this collection is running from | The city you are physically in, for example `calgary` or `red deer` |
   | Hit enter when the phone is ready | Press Enter. If several phones are plugged in, it then asks which one |
   | Demographic of the account | One of `4M`, `4F`, `7F`, `6M`, `9F`, `10M`, `18M`, `18F` |

   Before the city question, the collector prints where the internet connection appears to be.
   If that is not where you are, a VPN is on: stop and switch it off.

7. **Leave it running.** Do not touch the phone. The console prints one line per video:
   - `No ad found at index: 12 (2 reads, 5.3s)` for an ordinary video, which is then swiped away.
   - `Ad found at index: 13` for an ad, which is then recorded for 20 seconds and uploaded.
   - `Swipe not confirmed ..., swiping again` when a swipe did not change the video.

8. **Stopping.** The collector stops by itself when the hours are up, after finishing the video
   or ad that is on screen. To stop early, press **Ctrl+C** once and wait: it finishes the log
   and the uploads, which can take up to 30 seconds.

The computer's mouse and keyboard are free to use while the phone route runs. It is built so that
several phones can run at once, each from its own terminal, though that has not been tried yet.

## What a session produces

Each run creates one folder in `src/Output`, named with the start time, demographic, city and
the last four characters of the phone's serial number. It contains:

| File | Contents |
|---|---|
| `impressions.jsonl` | One line per video: when it appeared and was left, how long it was on screen, the account and caption that were read, whether it was an ad and which label showed it, the swipe, and how the screen was read |
| `frames/` | One screenshot per video, numbered to match the log |
| `Data Key.csv` | One line per ad that was recorded |
| `session.json` | The phone model, Android and TikTok versions, the location and public network, the settings used, and the version of the collector |

Recorded ads are uploaded to the S3 bucket as `<time>_<demographic>_<city>.mp4` and then removed
from the computer. If an upload fails, the recording stays in the session folder.

To read the log in Python:

```python
import pandas
log = pandas.read_json("src/Output/<session folder>/impressions.jsonl", lines=True)
```

## Quick test run

To check everything works without answering questions or uploading anything, run this from the
repository folder (not `src`), with the phone ready as above:

```
python tools/phone_test.py --minutes 3
```

It runs the collector for three minutes, stops it, and prints a summary: how many videos and
ads, any failed swipes, how long each read took, and where the session folder is. Recorded ads
stay in the session folder and are **not** uploaded unless `--upload` is added.
`python tools/phone_test.py --help` lists the other options.

## Settings

All of these go in `src/.env`. Empty means the default.

| Setting | Default | Meaning |
|---|---|---|
| `READS_PER_VIDEO` | `2` | How many times each video is read before it is swiped away. A second read catches an ad label that appeared late or was misread |
| `DWELL_SECONDS` | `5` | The least time each video stays on screen. `0` swipes as soon as the reads are done |
| `OCR_ENGINE` | automatic | The program that reads text from the screenshot. The collector uses the fast one (RapidOCR) when it works on the computer and the slower one (EasyOCR) when it does not. Set `rapidocr` or `easyocr` only to force one |
| `SCREEN_READER` | `ocr` | `ocr` reads a screenshot and asks the phone for its on-screen text only when the screenshot shows nothing. `ui` asks the phone first, which is exact but takes about 5 seconds a read |
| `UPLOAD_ADS` | on | `0` keeps recorded ads in the session folder and uploads nothing. For test runs |

## Troubleshooting

**`adb` is not recognised.** Close and reopen the terminal after installing it. If it is still
not found, find `adb.exe` (winget puts it under
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools...\platform-tools`) and put its
full path in `ADB_PATH` in `src/.env`.

**`adb devices` shows nothing.** Try another USB cable or port; some cables only charge. Check
USB debugging is still on.

**`adb devices` shows `unauthorized`.** Unlock the phone and accept the *Allow USB debugging*
prompt. If no prompt appears, unplug and replug the phone.

**"No phone found" when the collector starts.** The same causes as the two above.

**The phone has no internet.** Connect it to Wi-Fi. If it is connected but still reports no
internet, check its date and time are set automatically, and that the network does not need a
sign-in page. The computer can also share its connection: Settings > Network & internet >
Mobile hotspot.

**No ads appear at all.** Check for a VPN on the phone or the computer. A brand new account can
also see no ads for its first hours or days.

**"RapidOCR could not start, so EasyOCR is being used instead".** The collector carries on with
the slower reader. This usually means the environment is not the Python 3.12 one: run
`conda activate tiktok-scraper` and start again.

**"The specified log level of ... was invalid".** Set `LOGGING_LEVEL = "info"` in `src/.env`.
The run is not affected; only the Papertrail messages are.

**"invalid literal for int() with base 10: ''".** `PAPERTRAIL_PORT` is empty. The run is not
affected; no Papertrail messages are sent.

**"An error occurred while processing the ad".** An upload failed, usually because of a wrong
AWS key or bucket name in `src/.env`. The collector keeps logging videos, but uploads stop for
the rest of the session. The recordings are still in the session folder.

**`git pull` says there is no tracking information.** Run `git pull origin per-impression-log`.

**The collector keeps printing "Swipe not confirmed".** Something on the phone is blocking the
feed, such as a pop-up. After five failed swipes the collector presses back and refreshes the
feed by itself. If it does not recover, dismiss the pop-up on the phone by hand.

## Other interfaces

**BlueStacks (`mobile`).** Drives TikTok in a BlueStacks emulator window on Windows. It uses the
real mouse, so only one emulator can run per computer and the mouse must be left alone. In
recent tests TikTok did not play video properly in BlueStacks 5 and asked for extra verification
at sign-up, which is why the phone route replaced it.

**Website (`web`).** Drives tiktok.com in Chrome. It does not currently start: the Chrome driver
files it expects are no longer in the repository. The website also shows different ads from the
app.

## For developers

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests need no phone, emulator or OCR engine.

| Path | What it is |
|---|---|
| `src/tiktok_scraper/Core/main.py` | Start-up questions, session folder, threads |
| `src/tiktok_scraper/Core/collector.py` | The collection loop, the same for every device |
| `src/tiktok_scraper/Core/phone.py` | An Android phone over adb |
| `src/tiktok_scraper/Core/bluestacks.py` | A BlueStacks window on Windows |
| `src/tiktok_scraper/Core/impression.py` | Ad-label matching, the same-video check and the log writer |
| `tools/phone_test.py` | The quick test run |
| `tools/make_fixtures.py` | Builds test fixtures from the S3 ad videos or from session frames |
| `tools/tune_thresholds.py` | Scores the label and same-video settings on a fixture folder |

Fixtures made from session frames go in `tests/fixtures/local`, which git ignores, because the
frames show other people's videos. Do not commit session folders or frames.

On Windows with WSL, the collector can run in WSL and drive the Windows `adb.exe`: set
`ADB_PATH` to its `/mnt/c/...` path.

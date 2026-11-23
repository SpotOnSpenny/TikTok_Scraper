# TikTok Scraper

Scrolls the TikTok For You feed on an Android phone (or a BlueStacks window, or the web
interface), logs one row and one frame per video, and records and uploads the ads it finds.

## Setup

The collector is tested on Python 3.12. The conda environment pins that version:

```
conda env create -f environment.yml
conda activate tiktok-scraper
```

After pulling new code, bring the environment up to date with
`conda env update -f environment.yml --prune`.

Copy `src/.envexample` to `src/.env` and fill it in (AWS keys, `S3_BUCKET`, Papertrail port and
logging level). For the phone interface, install Android platform-tools and either put `adb` on
the PATH or set `ADB_PATH` in `src/.env`.

## Running

From the `src` folder:

```
python -m tiktok_scraper.Core.phone     # quick check of a connected phone: one screenshot, one swipe
python -m tiktok_scraper.Core.main      # the collector; choose web, mobile (BlueStacks) or phone
```

Collect on the local connection with no VPN: TikTok serves no ads through one. Each session
writes its own folder under `src/Output`: `impressions.jsonl` (one row per video), `frames/`,
`Data Key.csv` (uploaded ads) and `session.json` (device, app version, location, settings).

## Reading the screen

The collector prefers the fast OCR engine (RapidOCR). At start-up it tests that engine in a
separate process; if it cannot run on the machine, the collector says why and uses EasyOCR
instead, which is slower. Set `OCR_ENGINE` in `src/.env` only to force one or the other.
`READS_PER_VIDEO` and `DWELL_SECONDS` set how many times each video is read and how long it is
watched.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests need no phone, emulator or OCR engine. `tools/make_fixtures.py` builds test fixtures
from the S3 ad videos or from session frames, and `tools/tune_thresholds.py` scores the label
and same-video settings on them.

# Python Standard Library Dependencies
import io
import json
import sys
import types
from unittest import mock

# External Dependency Imports
import pytest

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# The small helpers in main.py. main imports the browser and upload modules, which are
# replaced with stand-ins so the helpers can be tested without them installed.


@pytest.fixture
def main(monkeypatch):
    for name in ["boto3", "tiktok_scraper.Core.selenium_utils", "tiktok_scraper.Core.processing", "tiktok_scraper.Core.logger"]:
        monkeypatch.setitem(sys.modules, name, mock.MagicMock())
    monkeypatch.delitem(sys.modules, "tiktok_scraper.Core.main", raising=False)
    import tiktok_scraper.Core.main as module
    yield module
    sys.modules.pop("tiktok_scraper.Core.main", None)


@pytest.mark.parametrize("typed, cleaned", [
    ("Calgary", "calgary"),
    ("  Red Deer ", "red-deer"),
    ("Guadalajara (resort)", "guadalajara-resort"),
    ("St. John's", "st-john-s"),
    ("red_deer", "red-deer"),
    ("   ", ""),
])
def test_clean_location(main, typed, cleaned):
    assert main.clean_location(typed) == cleaned


def test_public_network_keeps_the_useful_fields(main, monkeypatch):
    body = json.dumps({"ip": "203.0.113.7", "city": "Calgary", "region": "Alberta", "country": "CA", "org": "AS852 TELUS", "loc": "51.0,-114.0", "timezone": "America/Edmonton"}).encode()
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(body)
    monkeypatch.setattr(main.urllib.request, "urlopen", lambda url, timeout: response)
    assert main.public_network() == {"ip": "203.0.113.7", "city": "Calgary", "region": "Alberta", "country": "CA", "org": "AS852 TELUS", "timezone": "America/Edmonton"}


def test_public_network_is_none_when_the_lookup_fails(main, monkeypatch):
    def offline(url, timeout):
        raise OSError("no network")
    monkeypatch.setattr(main.urllib.request, "urlopen", offline)
    assert main.public_network() is None

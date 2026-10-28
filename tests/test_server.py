"""fetch() and the shaping around it, offline: urlopen is replaced with a
fake answering a synthetic tide, NOAA parsing reads a fixture, and the cache
in data_dir is exercised both ways."""

from __future__ import annotations

import json
import math
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import server

FIX = Path(__file__).resolve().parent / "fixtures"
MEL = ZoneInfo("Australia/Melbourne")
MELBOURNE = {"latitude": -37.81, "longitude": 144.96, "label": "Melbourne"}


class _Resp:
    def __init__(self, payload: bytes):
        self._b = payload

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _open_meteo_doc(now: datetime) -> dict:
    """A marine response around ``now``: hourly from yesterday for four days."""
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    times, vals = [], []
    for i in range(96):
        t = start + timedelta(hours=i)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        vals.append(
            round(
                0.4 * math.sin(2 * math.pi * (i - 3.0) / 12.42)
                + 0.05 * math.sin(i / 2.0),
                3,
            )
        )
    return {
        "timezone": "Australia/Melbourne",
        "utc_offset_seconds": 39600,
        "hourly": {"time": times, "sea_level_height_msl": vals},
    }


def _fake_urlopen(doc: dict):
    body = json.dumps(doc).encode("utf-8")

    def opener(req, timeout=0):
        assert timeout > 0
        assert "tesserae/tide_clock" in req.get_header("User-agent")
        return _Resp(body)

    return opener


# ----- NOAA parsing ----------------------------------------------------


def test_noaa_hilo_fixture_parses_to_alternating_events():
    doc = json.loads((FIX / "noaa_hilo.json").read_text(encoding="utf-8"))
    rows = server.parse_noaa_predictions(doc, ZoneInfo("America/Los_Angeles"))
    assert isinstance(rows, list) and len(rows) == 8
    kinds = [k for _, _, k in rows]
    assert kinds == ["high", "low"] * 4
    when, height, kind = rows[2]
    assert when.strftime("%Y-%m-%d %H:%M") == "2026-10-28 12:56"
    assert abs(height - 2.033) < 1e-9 and kind == "high"
    assert when.tzinfo is not None


def test_noaa_error_body_becomes_a_sentence():
    doc = {
        "error": {
            "message": " The station is not a valid station or there is system error."
        }
    }
    out = server.parse_noaa_predictions(doc, MEL)
    assert isinstance(out, str) and "tidesandcurrents.noaa.gov" in out


def test_noaa_empty_predictions_is_an_error():
    assert isinstance(server.parse_noaa_predictions({"predictions": []}, MEL), str)


# ----- shaping ---------------------------------------------------------


def test_shape_from_hourly_series_finds_events_marks_now_and_sets_the_hand():
    now = datetime(2026, 10, 28, 15, 10, tzinfo=MEL)
    doc = _open_meteo_doc(now)
    times = [
        datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=MEL)
        for t in doc["hourly"]["time"]
    ]
    out = server.shape(
        times=times,
        heights=doc["hourly"]["sea_level_height_msl"],
        events=None,
        now=now,
        lat=-37.81,
        lon=144.96,
        hours=24,
        units="m",
        clock="24",
    )
    assert out["window"]["minutes"] == 1440
    assert out["now"]["x"] == 240.0 and out["now"]["hhmm"] == "15:10"
    assert len(out["series"]) == 145
    kinds = [e["kind"] for e in out["events"]]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    assert out["next"]["minutes_away"] > 0
    assert (
        out["next_high"]["minutes_away"] > 0 and out["prev_high"]["minutes_away"] <= 0
    )
    assert 0 <= out["hand"]["angle"] < 360
    if out["state"] == "rising":
        assert out["hand"]["angle"] > 180
    else:
        assert out["hand"]["angle"] < 180
    assert [t["label"] for t in out["ticks"]] == ["12:00", "18:00", "Thu", "06:00"]
    assert {s["kind"] for s in out["suns"]} == {"sunrise", "sunset"}
    assert out["sun_today"]["sunrise"].startswith("06:")
    assert out["moon"]["flip"] is True
    assert all(e["date"] == "2026-10-28" for e in out["today"])


def test_shape_in_feet_and_twelve_hour_clock():
    now = datetime(2026, 10, 28, 9, 5, tzinfo=MEL)
    doc = _open_meteo_doc(now)
    times = [
        datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=MEL)
        for t in doc["hourly"]["time"]
    ]
    out = server.shape(
        times=times,
        heights=doc["hourly"]["sea_level_height_msl"],
        events=None,
        now=now,
        lat=-37.81,
        lon=144.96,
        hours=48,
        units="ft",
        clock="12",
    )
    assert out["now"]["hhmm"] == "9:05 am"
    assert out["window"]["hours"] == 48 and out["now"]["x"] == 480.0
    assert max(e["height"] for e in out["events"]) > 1.0  # 0.4 m is 1.3 ft


# ----- fetch -----------------------------------------------------------


def test_fetch_without_a_place_asks_for_one():
    out = server.fetch({}, {}, ctx={})
    assert "error" in out and "place" in out["error"].lower()


def test_fetch_open_meteo_through_the_fake_network(tmp_path):
    doc = _open_meteo_doc(datetime.now(MEL))
    with patch("urllib.request.urlopen", _fake_urlopen(doc)):
        out = server.fetch(
            {**MELBOURNE, "hours": "36"}, {}, ctx={"data_dir": str(tmp_path)}
        )
    assert out.get("error") is None, out
    assert out["label"] == "Melbourne" and out["units"] == "m"
    assert out["window"]["hours"] == 36 and len(out["series"]) == 217
    assert out["stale"] is False
    assert list(tmp_path.glob("req_*.json")), "the response is cached under data_dir"


def test_fetch_uses_the_cache_and_a_stale_copy_when_the_network_is_down(tmp_path):
    doc = _open_meteo_doc(datetime.now(MEL))
    with patch("urllib.request.urlopen", _fake_urlopen(doc)):
        first = server.fetch(MELBOURNE, {}, ctx={"data_dir": str(tmp_path)})
    assert first.get("error") is None

    def down(req, timeout=0):
        raise urllib.error.URLError("no route to host")

    with patch("urllib.request.urlopen", down):
        cached = server.fetch(MELBOURNE, {}, ctx={"data_dir": str(tmp_path)})
        assert cached.get("error") is None and cached["stale"] is False
        forced = server.fetch(
            MELBOURNE, {}, ctx={"data_dir": str(tmp_path), "fresh": True}
        )
        assert forced.get("error") is None and forced["stale"] is True


def test_fetch_with_no_network_and_no_cache_returns_an_error_dict(tmp_path):
    def down(req, timeout=0):
        raise urllib.error.URLError("no route to host")

    with patch("urllib.request.urlopen", down):
        out = server.fetch(MELBOURNE, {}, ctx={"data_dir": str(tmp_path)})
    assert isinstance(out, dict) and "error" in out and "Open-Meteo" in out["error"]


def test_fetch_inland_place_explains_the_model_covers_the_coast(tmp_path):
    doc = _open_meteo_doc(datetime.now(MEL))
    doc["hourly"]["sea_level_height_msl"] = [None] * 96
    with patch("urllib.request.urlopen", _fake_urlopen(doc)):
        out = server.fetch(MELBOURNE, {}, ctx={"data_dir": str(tmp_path)})
    assert "shore" in out["error"]


def test_fetch_noaa_needs_a_station_and_reads_both_products(tmp_path):
    out = server.fetch(
        {**MELBOURNE, "source": "noaa"}, {}, ctx={"data_dir": str(tmp_path)}
    )
    assert "station" in out["error"].lower()

    tz = ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hilo, curve = [], []
    for i in range(0, 96 * 2):
        t = start + timedelta(minutes=30 * i)
        v = 1.0 + 0.9 * math.sin(2 * math.pi * (i / 2 - 3.0) / 12.42)
        curve.append({"t": t.strftime("%Y-%m-%d %H:%M"), "v": f"{v:.3f}"})
    for k in range(8):
        t = start + timedelta(hours=3.0 + k * 6.21)
        hilo.append(
            {
                "t": t.strftime("%Y-%m-%d %H:%M"),
                "v": "1.9" if k % 2 == 0 else "0.1",
                "type": "H" if k % 2 == 0 else "L",
            }
        )

    def opener(req, timeout=0):
        url = req.full_url
        assert "api.tidesandcurrents.noaa.gov" in url and "station=9414290" in url
        doc = (
            {"predictions": hilo} if "interval=hilo" in url else {"predictions": curve}
        )
        return _Resp(json.dumps(doc).encode("utf-8"))

    opts = {
        "latitude": 37.8,
        "longitude": -122.47,
        "label": "San Francisco",
        "source": "noaa",
        "station": " 9414290 ",
        "units": "ft",
        "location": {"timezone": "America/Los_Angeles"},
    }
    with patch("urllib.request.urlopen", opener):
        out = server.fetch(opts, {}, ctx={"data_dir": str(tmp_path)})
    assert out.get("error") is None, out
    assert out["station"] == "9414290" and out["units"] == "ft"
    assert out["source_note"].startswith("NOAA station 9414290")
    assert out["next"]["kind"] in ("high", "low")
    assert len(list(tmp_path.glob("req_*.json"))) == 2


def test_fetch_never_raises_on_a_bad_document(tmp_path):
    def opener(req, timeout=0):
        return _Resp(b"not json at all")

    with patch("urllib.request.urlopen", opener):
        out = server.fetch(MELBOURNE, {}, ctx={"data_dir": str(tmp_path)})
    assert "error" in out


@pytest.mark.parametrize("clock,expect", [("24", "14:05"), ("12", "2:05 pm")])
def test_time_formats(clock, expect):
    assert server._hhmm(datetime(2026, 10, 28, 14, 5, tzinfo=MEL), clock) == expect


def test_in_text_reads_naturally():
    assert server._in_text(160) == "in 2 h 40 min"
    assert server._in_text(120) == "in 2 h"
    assert server._in_text(7) == "in 7 min"
    assert server._in_text(0) == "now"

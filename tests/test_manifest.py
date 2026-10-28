"""The options panel stays short: five options up front, the station under
its source, the rest under More options."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = json.loads(
    (Path(__file__).resolve().parent.parent / "plugin.json").read_text(encoding="utf-8")
)
OPTIONS = MANIFEST["cell_options"]

ORDER = ["location", "source", "station", "layout", "hours", "units", "clock", "title"]
UP_FRONT = ["location", "source", "layout", "hours", "units"]


def _up_front(o: dict) -> bool:
    return not o.get("show_if") and not o.get("advanced")


def test_manifest_basics():
    assert MANIFEST["name"] == "Tide Clock"
    assert MANIFEST["icon"] == "ph-waves"
    assert MANIFEST["category"] == "weather"
    assert MANIFEST["kind"] == "widget"
    assert MANIFEST["tesserae_compat"] == "1.x"
    assert MANIFEST["render"]["needs_network"] is True
    assert set(MANIFEST["requires"]) == {
        "network:marine-api.open-meteo.com",
        "network:api.tidesandcurrents.noaa.gov",
    }
    assert MANIFEST["supports"]["sizes"] == ["xs", "sm", "md", "lg"]


def test_option_names_and_order():
    assert [o["name"] for o in OPTIONS] == ORDER
    assert "label" not in ORDER


def test_few_options_up_front():
    assert [o["name"] for o in OPTIONS if _up_front(o)] == UP_FRONT
    assert len(UP_FRONT) <= 5


def test_station_only_shows_for_noaa():
    station = next(o for o in OPTIONS if o["name"] == "station")
    assert station["show_if"] == {"source": "noaa"}
    assert "tidesandcurrents.noaa.gov" in station["help"]


def test_source_help_says_the_model_is_modelled():
    source = next(o for o in OPTIONS if o["name"] == "source")
    assert "model" in source["help"].lower() and "hour" in source["help"].lower()


def test_advanced_options_come_last():
    seen = False
    for o in OPTIONS:
        if o.get("advanced"):
            seen = True
        else:
            assert not seen, f"{o['name']} follows an advanced option"


def test_title_is_advanced():
    title = next(o for o in OPTIONS if o["name"] == "title")
    assert title["advanced"] is True and title["type"] == "string"


def test_no_em_dashes_anywhere_in_the_manifest():
    assert chr(0x2014) not in json.dumps(MANIFEST, ensure_ascii=False)

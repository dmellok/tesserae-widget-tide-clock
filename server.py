"""tide_clock: the tide at a place, as a tide-clock dial and a curve.

Two sources. Open-Meteo's marine model gives hourly sea level height above
mean sea level anywhere on the coast with no key; the highs and lows are
found here as local extrema and refined to the minute with a parabola. A
NOAA station gives the exact harbour predictions for the United States.
Sunrise, sunset and the Moon's phase are computed locally so no third host
is needed.

Everything the client draws is shaped here in one frame: minutes from the
start of the curve's window, in the place's own time zone. The client does
no date arithmetic at all.

Returns a JSON-serialisable dict, or {"error": "friendly sentence"}; never
raises. Each upstream request is cached for an hour under data_dir, keyed
by the request, and a stale copy is used when the network is down because
a tide prediction does not go off.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name: str):
    """Import a module beside this file, whatever sys.path looks like."""
    spec = importlib.util.spec_from_file_location(
        f"_tesserae_plugins.tide_clock.{name}", os.path.join(_HERE, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tidemath = _sibling("tidemath")

USER_AGENT = (
    "tesserae/tide_clock/0.1.0 (+https://github.com/dmellok/tesserae-widget-tide-clock)"
)
HTTP_TIMEOUT_S = 8
CACHE_TTL_S = 3600
OPEN_METEO = "https://marine-api.open-meteo.com/v1/marine"
NOAA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
FT_PER_M = 3.280839895

# Minutes of curve shown before "now", per curve length, so the last turn of
# the tide stays in view.
LEAD_MIN = {24: 4 * 60, 36: 6 * 60, 48: 8 * 60}


# ----- time zones -------------------------------------------------------


def _tz_from_name(name: Any) -> tzinfo | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except (ValueError, KeyError, OSError):
        return None


def _location_tz(options: dict[str, Any], lon: float) -> tzinfo:
    """The zone the place keeps its clock in. The location pick carries the
    geocoder's IANA zone on newer servers; failing that, the server's own
    zone when it is plausible for the longitude, else the whole-hour solar
    offset, which is at most an hour or so from civil time."""
    for value in (options.get("timezone"), options.get("location")):
        if isinstance(value, dict):
            value = value.get("timezone")
        tz = _tz_from_name(value)
        if tz is not None:
            return tz
    server_now = datetime.now().astimezone()
    server_offset_h = (server_now.utcoffset() or timedelta(0)).total_seconds() / 3600.0
    solar_h = round(lon / 15.0)
    gap = ((server_offset_h - solar_h + 12.0) % 24.0) - 12.0
    if abs(gap) <= 2.5:
        return server_now.tzinfo or timezone.utc
    return timezone(timedelta(hours=max(-12, min(14, solar_h))))


# ----- HTTP with a cache ------------------------------------------------


def _cache_path(data_dir: Path | None, url: str) -> Path | None:
    if data_dir is None:
        return None
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return data_dir / f"req_{key}.json"


def _read_cache(path: Path | None, max_age: float | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    if max_age is not None and time.time() - path.stat().st_mtime >= max_age:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _get_json(
    url: str, data_dir: Path | None, fresh: bool
) -> tuple[dict[str, Any] | None, str]:
    """GET a JSON document, through the hour cache. Returns (doc, note); doc
    is None with a reason in note when nothing at all could be had. A stale
    cache file stands in for a dead network, noted as "stale"."""
    path = _cache_path(data_dir, url)
    if not fresh:
        hit = _read_cache(path, CACHE_TTL_S)
        if hit is not None:
            return hit, "cache"
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            raw = resp.read()
        doc = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        doc = None
        note = f"http {exc.code}"
        try:
            doc = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - an error body is optional
            pass
        if not isinstance(doc, dict) or "error" not in doc:
            stale = _read_cache(path, None)
            return (stale, "stale") if stale is not None else (None, note)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        stale = _read_cache(path, None)
        if stale is not None:
            return stale, "stale"
        return None, f"network: {exc}"
    if not isinstance(doc, dict):
        return None, "not a JSON object"
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc), encoding="utf-8")
        except OSError:
            pass
    return doc, "fresh"


# ----- sources ----------------------------------------------------------


def _parse_local(s: str, tz: tzinfo) -> datetime:
    """'2026-10-28T14:00' or '2026-10-28 14:00' in the place's zone."""
    return datetime.strptime(s.replace("T", " ")[:16], "%Y-%m-%d %H:%M").replace(
        tzinfo=tz
    )


def _series_from_open_meteo(
    doc: dict[str, Any],
) -> tuple[list[datetime], list[float], tzinfo] | str:
    """Hourly (times, heights, tz) from a marine response, or an error sentence."""
    if "error" in doc:
        return "Open-Meteo could not answer for this spot."
    tz = _tz_from_name(doc.get("timezone")) or timezone(
        timedelta(seconds=int(doc.get("utc_offset_seconds") or 0))
    )
    hourly = doc.get("hourly") or {}
    times = hourly.get("time") or []
    vals = hourly.get("sea_level_height_msl") or []
    pairs = [(t, v) for t, v in zip(times, vals) if isinstance(v, (int, float))]
    if len(pairs) < 6:
        return "Open-Meteo has no sea level for this spot. Its marine model covers the open coast, so move the place to the nearest shore."
    return [_parse_local(t, tz) for t, _ in pairs], [float(v) for _, v in pairs], tz


def parse_noaa_predictions(
    doc: dict[str, Any], tz: tzinfo
) -> list[tuple[datetime, float, str]] | str:
    """A NOAA predictions document to [(time, metres, kind)]. ``kind`` is
    "high", "low" or "" for a curve sample. An error sentence on failure."""
    if "error" in doc:
        msg = str((doc.get("error") or {}).get("message") or "").strip()
        if "not a valid station" in msg.lower():
            return "NOAA does not know that station id. Find yours at tidesandcurrents.noaa.gov."
        return "NOAA could not answer for that station right now."
    rows = doc.get("predictions")
    if not isinstance(rows, list) or not rows:
        return "NOAA returned no predictions for that station."
    out: list[tuple[datetime, float, str]] = []
    for row in rows:
        try:
            when = _parse_local(str(row["t"]), tz)
            height = float(row["v"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = {"H": "high", "L": "low"}.get(str(row.get("type") or "").upper(), "")
        out.append((when, height, kind))
    if not out:
        return "NOAA returned no readable predictions for that station."
    return out


# ----- shaping ----------------------------------------------------------


def _hhmm(dt: datetime, clock: str) -> str:
    if clock == "12":
        h = dt.hour % 12 or 12
        return f"{h}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"
    return dt.strftime("%H:%M")


def _in_text(minutes: float) -> str:
    m = int(round(minutes))
    if m <= 0:
        return "now"
    h, mm = divmod(m, 60)
    if h and mm:
        return f"in {h} h {mm} min"
    if h:
        return f"in {h} h"
    return f"in {mm} min"


def _height(m: float, units: str) -> float:
    return round(m * FT_PER_M, 1) if units == "ft" else round(m, 2)


def _date_line(dt: datetime) -> str:
    return f"{dt.strftime('%A')} {dt.day} {dt.strftime('%B')}"


def _event(
    when: datetime,
    height_m: float,
    kind: str,
    t0: datetime,
    now: datetime,
    units: str,
    clock: str,
) -> dict:
    offset = (when - t0).total_seconds() / 60.0
    return {
        "kind": kind,
        "x": round(offset, 1),
        "hhmm": _hhmm(when, clock),
        "iso": when.strftime("%Y-%m-%dT%H:%M"),
        "date": when.strftime("%Y-%m-%d"),
        "height": _height(height_m, units),
        "minutes_away": round((when - now).total_seconds() / 60.0),
        "in": _in_text((when - now).total_seconds() / 60.0),
    }


def shape(
    *,
    times: list[datetime],
    heights: list[float],
    events: list[tuple[datetime, float, str]] | None,
    now: datetime,
    lat: float,
    lon: float,
    hours: int,
    units: str,
    clock: str,
) -> dict[str, Any]:
    """Everything the client draws, from a sampled series in the place's zone
    (evenly spaced) and optionally a list of exact highs and lows. Without
    one, the extrema of the series are found and refined here."""
    tz = now.tzinfo
    step_min = (times[1] - times[0]).total_seconds() / 60.0 if len(times) > 1 else 60.0
    series_start = times[0]

    def at(dt: datetime) -> float:
        return tidemath.catmull_rom(
            heights, (dt - series_start).total_seconds() / 60.0 / step_min
        )

    if events is None:
        found = []
        for x, h, kind in tidemath.extrema(heights):
            found.append((series_start + timedelta(minutes=x * step_min), h, kind))
        events = found
    events = sorted(events, key=lambda e: e[0])

    lead = LEAD_MIN.get(hours, hours * 10)
    t0 = now - timedelta(minutes=lead)
    t1 = t0 + timedelta(hours=hours)
    t0 = max(t0, series_start)
    t1 = min(t1, times[-1])

    # The curve: resampled every ten minutes inside the window. An hourly
    # model series carries small wobbles that a cubic through the samples
    # faithfully reproduces as kinks, so for drawing only it is first
    # softened with a 1-2-1 kernel; the highs and lows above come from the
    # unsmoothed samples.
    span_min = (t1 - t0).total_seconds() / 60.0
    drawn = tidemath.smooth(heights) if step_min >= 60 else heights

    def drawn_at(dt: datetime) -> float:
        return tidemath.catmull_rom(
            drawn, (dt - series_start).total_seconds() / 60.0 / step_min
        )

    # Drawn samples keep more precision than the printed heights: rounded to
    # the display's decimal they step visibly on a small-range tide.
    scale = FT_PER_M if units == "ft" else 1.0
    samples = []
    m = 0.0
    while m <= span_min + 1e-6:
        samples.append(
            [round(m, 1), round(drawn_at(t0 + timedelta(minutes=m)) * scale, 4)]
        )
        m += 10.0

    prev_high = next(
        (e for e in reversed(events) if e[2] == "high" and e[0] <= now), None
    )
    next_high = next((e for e in events if e[2] == "high" and e[0] > now), None)
    prev_low = next(
        (e for e in reversed(events) if e[2] == "low" and e[0] <= now), None
    )
    next_low = next((e for e in events if e[2] == "low" and e[0] > now), None)
    nxt = next((e for e in events if e[0] > now), None)
    state = "rising" if nxt is not None and nxt[2] == "high" else "falling"

    def mins(e):
        return None if e is None else (e[0] - now).total_seconds() / 60.0

    fraction = tidemath.hand_fraction(
        0.0, mins(prev_high), mins(next_high), mins(prev_low), mins(next_low)
    )
    angle = tidemath.hand_angle(fraction)

    def ev(e):
        return None if e is None else _event(e[0], e[1], e[2], t0, now, units, clock)

    in_window = [ev(e) for e in events if t0 <= e[0] <= t1]
    today = now.strftime("%Y-%m-%d")
    todays = [ev(e) for e in events if e[0].strftime("%Y-%m-%d") == today]
    if not todays:
        todays = [ev(e) for e in events if e[0] > now][:4]

    # Clock ticks every six hours, named by the weekday at midnight.
    ticks = []
    first = t0.replace(minute=0, second=0, microsecond=0)
    while first < t0:
        first += timedelta(hours=1)
    t = first
    while t <= t1:
        if t.hour % 6 == 0:
            ticks.append(
                {
                    "x": round((t - t0).total_seconds() / 60.0, 1),
                    "label": t.strftime("%a") if t.hour == 0 else _hhmm(t, clock),
                    "midnight": t.hour == 0,
                    "major": t.hour % 12 == 0,
                }
            )
        t += timedelta(hours=1)

    # Sunrise and sunset marks on the curve, for every day the window touches.
    suns = []
    today_sun: dict[str, Any] = {}
    day = t0.date() - timedelta(days=1)
    while day <= t1.date():
        rise, sett = tidemath.sun_times(day, lat, lon, tz)
        for kind, when in (("sunrise", rise), ("sunset", sett)):
            if when is None:
                continue
            if day == now.date():
                today_sun[kind] = _hhmm(when, clock)
            if t0 <= when <= t1:
                suns.append(
                    {
                        "kind": kind,
                        "x": round((when - t0).total_seconds() / 60.0, 1),
                        "h": _height(at(when), units),
                        "hhmm": _hhmm(when, clock),
                    }
                )
        day += timedelta(days=1)

    moon = tidemath.moon_phase(now.astimezone(timezone.utc))
    all_heights = [s[1] for s in samples]
    for s in suns:
        s["h"] = round(drawn_at(t0 + timedelta(minutes=s["x"])) * scale, 4)
    height_now = _height(at(now), units)
    now_drawn = round(drawn_at(now) * scale, 4)

    return {
        "units": units,
        "clock": clock,
        "now": {
            "x": round((now - t0).total_seconds() / 60.0, 1),
            "h": now_drawn,
            "hhmm": _hhmm(now, clock),
            "iso": now.strftime("%Y-%m-%dT%H:%M"),
            "date_line": _date_line(now),
            "date_short": f"{now.strftime('%a')} {now.day} {now.strftime('%b')}",
            "weekday": now.strftime("%A"),
        },
        "window": {"minutes": round(span_min, 1), "hours": hours},
        "series": samples,
        "range": [min(all_heights), max(all_heights)] if all_heights else [0, 1],
        "events": in_window,
        "today": todays,
        "state": state,
        "height_now": height_now,
        "hand": {"fraction": round(fraction, 4), "angle": round(angle, 2)},
        "prev_high": ev(prev_high),
        "next_high": ev(next_high),
        "prev_low": ev(prev_low),
        "next_low": ev(next_low),
        "next": ev(nxt),
        "ticks": ticks,
        "suns": suns,
        "sun_today": today_sun,
        "moon": {
            "illum": round(moon["illum"], 3),
            "waxing": bool(moon["waxing"]),
            "name": moon["name"],
            "flip": lat < 0,
        },
    }


# ----- fetch ------------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    source = str(options.get("source") or "openmeteo").strip()
    units = "ft" if str(options.get("units") or "m") == "ft" else "m"
    clock = "12" if str(options.get("clock") or "24") == "12" else "24"
    layout = str(options.get("layout") or "dial_curve")
    title = str(options.get("title") or "").strip()
    label = str(options.get("label") or "").strip()
    try:
        hours = int(options.get("hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    if hours not in LEAD_MIN:
        hours = 24
    fresh = bool(ctx.get("fresh"))
    data_dir = Path(ctx["data_dir"]) if ctx.get("data_dir") else None

    base = {
        "layout": layout,
        "title": title,
        "label": label,
        "source": source,
        "units": units,
    }

    try:
        lat = float(options.get("latitude"))
        lon = float(options.get("longitude"))
    except (TypeError, ValueError):
        return {
            **base,
            "error": "Set a place in this cell's settings to show its tide.",
        }

    try:
        if source == "noaa":
            return {
                **base,
                **_fetch_noaa(options, lat, lon, hours, units, clock, data_dir, fresh),
            }
        return {
            **base,
            **_fetch_open_meteo(lat, lon, hours, units, clock, data_dir, fresh),
        }
    except Exception as exc:  # noqa: BLE001 - the cell must paint something
        return {
            **base,
            "error": f"Could not work out the tide right now ({type(exc).__name__}).",
        }


def _fetch_open_meteo(lat, lon, hours, units, clock, data_dir, fresh) -> dict[str, Any]:
    q = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "hourly": "sea_level_height_msl",
            "timezone": "auto",
            "forecast_days": 3,
            "past_days": 1,
        }
    )
    doc, note = _get_json(f"{OPEN_METEO}?{q}", data_dir, fresh)
    if doc is None:
        return {
            "error": "Could not reach Open-Meteo for the sea level. Check the network and try again."
        }
    parsed = _series_from_open_meteo(doc)
    if isinstance(parsed, str):
        return {"error": parsed}
    times, heights, tz = parsed
    now = datetime.now(tz).replace(second=0, microsecond=0)
    if not (times[0] <= now <= times[-1]):
        return {
            "error": "Open-Meteo's sea level does not cover the present moment. Try a fresh render."
        }
    out = shape(
        times=times,
        heights=heights,
        events=None,
        now=now,
        lat=lat,
        lon=lon,
        hours=hours,
        units=units,
        clock=clock,
    )
    out["source_note"] = "Open-Meteo marine model, modelled sea level"
    out["stale"] = note == "stale"
    return out


def _fetch_noaa(
    options, lat, lon, hours, units, clock, data_dir, fresh
) -> dict[str, Any]:
    station = "".join(ch for ch in str(options.get("station") or "") if ch.isdigit())
    if not station:
        return {
            "error": "Enter a NOAA station id. Find one at tidesandcurrents.noaa.gov."
        }
    tz = _location_tz(options, lon)
    now = datetime.now(tz).replace(second=0, microsecond=0)
    begin = (now - timedelta(days=1)).strftime("%Y%m%d")

    def url(interval: str) -> str:
        q = urllib.parse.urlencode(
            {
                "product": "predictions",
                "datum": "MLLW",
                "station": station,
                "time_zone": "lst_ldt",
                "units": "metric",
                "interval": interval,
                "format": "json",
                "begin_date": begin,
                "range": 96,
            }
        )
        return f"{NOAA}?{q}"

    hilo_doc, note1 = _get_json(url("hilo"), data_dir, fresh)
    if hilo_doc is None:
        return {
            "error": "Could not reach NOAA for the tide predictions. Check the network and try again."
        }
    hilo = parse_noaa_predictions(hilo_doc, tz)
    if isinstance(hilo, str):
        return {"error": hilo}
    curve_doc, note2 = _get_json(url("30"), data_dir, fresh)
    if curve_doc is None:
        return {
            "error": "Could not reach NOAA for the tide curve. Check the network and try again."
        }
    curve = parse_noaa_predictions(curve_doc, tz)
    if isinstance(curve, str):
        return {"error": curve}
    times = [c[0] for c in curve]
    heights = [c[1] for c in curve]
    if not (times[0] <= now <= times[-1]):
        return {
            "error": "NOAA's predictions do not cover the present moment. Try a fresh render."
        }
    events = [(e[0], e[1], e[2]) for e in hilo if e[2]]
    out = shape(
        times=times,
        heights=heights,
        events=events,
        now=now,
        lat=lat,
        lon=lon,
        hours=hours,
        units=units,
        clock=clock,
    )
    out["source_note"] = f"NOAA station {station}, predictions above MLLW"
    out["station"] = station
    out["stale"] = "stale" in (note1, note2)
    return out

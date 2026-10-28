"""Pure maths for the Tide Clock widget: extrema of a sampled series, the
tide-clock hand, sunrise and sunset, and the Moon's phase.

Nothing here touches the network or the clock, so every function is
deterministic and tested on synthetic input. Loaded by server.py under a
widget-unique module name; see _sibling() there.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

SYNODIC_MONTH_DAYS = 29.530588853
# A reference new Moon: 2000-01-06 18:14 UTC.
NEW_MOON_REF = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
# Mean interval between successive high waters for a semi-diurnal tide, used
# only where the series runs out before the previous high.
MEAN_TIDAL_CYCLE_MIN = 12 * 60 + 25

MOON_PHASES = [
    (0.03, "New moon"),
    (0.22, "Waxing crescent"),
    (0.28, "First quarter"),
    (0.47, "Waxing gibbous"),
    (0.53, "Full moon"),
    (0.72, "Waning gibbous"),
    (0.78, "Last quarter"),
    (0.97, "Waning crescent"),
    (1.01, "New moon"),
]


# ----- extrema ---------------------------------------------------------


def find_extrema(
    values: list[float], min_prominence: float = 0.1
) -> list[tuple[int, str]]:
    """Indices of the local highs and lows of a sampled series.

    A high is a sample at least as big as the one before and strictly bigger
    than the one after, so a flat top counts once at its right edge; lows
    mirror that. Highs and lows then alternate: two highs in a row keep only
    the higher, two lows only the lower. Finally a low and high next to each
    other that differ by less than ``min_prominence`` of the series' range
    are a wobble on the way up or down, not a turn of the tide, and the pair
    is dropped, smallest first. Returned as (index, "high"|"low") in time
    order.
    """
    cands: list[tuple[int, str]] = []
    for i in range(1, len(values) - 1):
        a, b, c = values[i - 1], values[i], values[i + 1]
        if b >= a and b > c:
            cands.append((i, "high"))
        elif b <= a and b < c:
            cands.append((i, "low"))
    out: list[tuple[int, str]] = []
    for idx, kind in cands:
        if out and out[-1][1] == kind:
            prev_idx = out[-1][0]
            better = (
                values[idx] > values[prev_idx]
                if kind == "high"
                else values[idx] < values[prev_idx]
            )
            if better:
                out[-1] = (idx, kind)
            continue
        out.append((idx, kind))
    if len(values) > 1:
        floor = min_prominence * (max(values) - min(values))
        while len(out) >= 3:
            gaps = [
                abs(values[out[i][0]] - values[out[i + 1][0]])
                for i in range(len(out) - 1)
            ]
            k = min(range(len(gaps)), key=gaps.__getitem__)
            if gaps[k] >= floor:
                break
            del out[k : k + 2]
    return out


def refine_extremum(values: list[float], i: int) -> tuple[float, float]:
    """Fit a parabola through samples i-1, i, i+1 and return its vertex as
    (fractional index, height). Hourly samples put a high anywhere inside the
    hour; the parabola recovers the minute to within a few minutes on a tide
    curve, which is sine-shaped enough over three hours for that to hold."""
    a, b, c = values[i - 1], values[i], values[i + 1]
    denom = a - 2.0 * b + c
    if abs(denom) < 1e-12:
        return float(i), float(b)
    dx = 0.5 * (a - c) / denom
    dx = max(-1.0, min(1.0, dx))
    height = b - 0.25 * (a - c) * dx
    return i + dx, height


def extrema(values: list[float]) -> list[tuple[float, float, str]]:
    """find_extrema plus refine_extremum: (fractional index, height, kind)."""
    out = []
    for i, kind in find_extrema(values):
        x, h = refine_extremum(values, i)
        out.append((x, h, kind))
    return out


# ----- interpolation ---------------------------------------------------


def smooth(values: list[float]) -> list[float]:
    """A 1-2-1 pass over the series, ends kept. Takes the kinks out of an
    hourly model series for drawing; costs a few percent of amplitude on a
    semi-diurnal tide, which is why the extrema are not read from it."""
    n = len(values)
    if n < 3:
        return list(values)
    out = [values[0]]
    for i in range(1, n - 1):
        out.append(0.25 * values[i - 1] + 0.5 * values[i] + 0.25 * values[i + 1])
    out.append(values[-1])
    return out


def catmull_rom(values: list[float], x: float) -> float:
    """Value of the series at fractional index x, cubic through the samples.
    Catmull-Rom passes through every sample and gives the rounded crest a
    tide curve should have instead of a polyline's corners."""
    n = len(values)
    if n == 0:
        return 0.0
    if x <= 0:
        return values[0]
    if x >= n - 1:
        return values[-1]
    i = int(math.floor(x))
    t = x - i
    p0 = values[max(i - 1, 0)]
    p1 = values[i]
    p2 = values[min(i + 1, n - 1)]
    p3 = values[min(i + 2, n - 1)]
    return 0.5 * (
        2.0 * p1
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t * t
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t * t * t
    )


# ----- the hand --------------------------------------------------------


def hand_fraction(
    now_min: float,
    prev_high_min: float | None,
    next_high_min: float | None,
    prev_low_min: float | None = None,
    next_low_min: float | None = None,
) -> float:
    """How far round the dial the hand sits: 0 at the previous high, 0.5 at
    low water, 1 at the next high.

    Each half of the cycle is scaled on its own when the low between the
    two highs is known, so the hand points straight down at the moment of
    low water even on a mixed tide whose ebb and flood run unequal. With
    only the highs it is the plain fraction of the way from one to the
    next, and missing an end of the cycle it assumes the mean semi-diurnal
    length. For a symmetric tide the two readings agree."""
    if prev_high_min is None and next_high_min is None:
        return 0.0
    if prev_high_min is None:
        prev_high_min = next_high_min - MEAN_TIDAL_CYCLE_MIN
    if next_high_min is None:
        next_high_min = prev_high_min + MEAN_TIDAL_CYCLE_MIN
    span = next_high_min - prev_high_min
    if span <= 0:
        return 0.0
    low = None
    if next_low_min is not None and prev_high_min < next_low_min < next_high_min:
        low = next_low_min
    elif prev_low_min is not None and prev_high_min < prev_low_min < next_high_min:
        low = prev_low_min
    if low is None:
        return max(0.0, min(1.0, (now_min - prev_high_min) / span))
    if now_min <= low:
        return max(0.0, 0.5 * (now_min - prev_high_min) / (low - prev_high_min))
    return min(1.0, 0.5 + 0.5 * (now_min - low) / (next_high_min - low))


def hand_angle(fraction: float) -> float:
    """Degrees clockwise from the top: 0 is high tide, 180 is low."""
    return (fraction * 360.0) % 360.0


# ----- sun -------------------------------------------------------------


def _julian_day(dt_utc: datetime) -> float:
    y, m, d = dt_utc.year, dt_utc.month, dt_utc.day
    frac = (dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (
        math.floor(365.25 * (y + 4716))
        + math.floor(30.6001 * (m + 1))
        + d
        + b
        - 1524.5
        + frac
    )


def _solar_terms(jd: float) -> tuple[float, float]:
    """Solar declination (degrees) and the equation of time (minutes) for a
    Julian day, from the NOAA solar calculator's formulae."""
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (
        math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
        + math.sin(3 * mr) * 0.000289
    )
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = (
        23.0
        + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    )
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(
        math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(app_long)))
    )
    y = math.tan(math.radians(eps) / 2.0) ** 2
    l0r = math.radians(l0)
    eot = 4.0 * math.degrees(
        y * math.sin(2 * l0r)
        - 2 * e * math.sin(mr)
        + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r)
        - 1.25 * e * e * math.sin(2 * mr)
    )
    return decl, eot


def sun_times(
    date, lat: float, lon: float, tz
) -> tuple[datetime | None, datetime | None]:
    """Sunrise and sunset on a calendar date at a place, as aware datetimes in
    tz, from the NOAA sunrise equations (zenith 90.833 degrees, so refraction
    and the solar disc are allowed for). Either is None where the Sun does
    not rise or set that day (polar summer and winter)."""
    local_noon = datetime(date.year, date.month, date.day, 12, 0, tzinfo=tz)
    offset_min = (local_noon.utcoffset() or timedelta(0)).total_seconds() / 60.0
    decl, eot = _solar_terms(_julian_day(local_noon.astimezone(timezone.utc)))
    lat_r, decl_r = math.radians(lat), math.radians(decl)
    cos_ha = math.cos(math.radians(90.833)) / (
        math.cos(lat_r) * math.cos(decl_r)
    ) - math.tan(lat_r) * math.tan(decl_r)
    if cos_ha < -1.0 or cos_ha > 1.0:
        return None, None
    ha = math.degrees(math.acos(cos_ha))
    noon_min = 720.0 - 4.0 * lon - eot + offset_min
    midnight = datetime(date.year, date.month, date.day, 0, 0, tzinfo=tz)
    rise = midnight + timedelta(minutes=noon_min - ha * 4.0)
    sett = midnight + timedelta(minutes=noon_min + ha * 4.0)
    return rise, sett


# ----- moon ------------------------------------------------------------


def moon_phase(dt_utc: datetime) -> dict:
    """The Moon's age as a fraction of the synodic month, its illuminated
    fraction and a phase name. A mean month is within half a day of the true
    one, which is as fine as a glyph can show."""
    days = (dt_utc - NEW_MOON_REF).total_seconds() / 86400.0
    phase = (days / SYNODIC_MONTH_DAYS) % 1.0
    illum = (1.0 - math.cos(2.0 * math.pi * phase)) / 2.0
    waxing = phase < 0.5
    name = "New moon"
    for edge, label in MOON_PHASES:
        if phase <= edge:
            name = label
            break
    return {"phase": phase, "illum": illum, "waxing": waxing, "name": name}

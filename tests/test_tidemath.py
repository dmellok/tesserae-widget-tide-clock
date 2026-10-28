"""The maths behind the dial: extrema of a sampled tide, the parabola that
refines them to the minute, the hand, sunrise and sunset, the Moon. All
synthetic or pinned to a known date, so fully deterministic and offline."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import tidemath

PERIOD_H = 12.42  # a semi-diurnal tide, hours between highs


def _sine(hours: int, phase_h: float = 2.3, amp: float = 1.0) -> list[float]:
    return [
        amp * math.sin(2 * math.pi * (i - phase_h) / PERIOD_H) for i in range(hours)
    ]


# ----- extrema ---------------------------------------------------------


def test_extrema_alternate_and_land_on_the_crests():
    vals = _sine(72)
    found = tidemath.find_extrema(vals)
    kinds = [k for _, k in found]
    assert kinds[0] in ("high", "low")
    assert all(a != b for a, b in zip(kinds, kinds[1:])), (
        "highs and lows must alternate"
    )
    # Five and a bit cycles in 72 hours: eleven or twelve turns.
    assert 10 <= len(found) <= 12
    for i, kind in found:
        if kind == "high":
            assert vals[i] >= vals[i - 1] and vals[i] > vals[i + 1]
        else:
            assert vals[i] <= vals[i - 1] and vals[i] < vals[i + 1]


def test_parabola_refines_an_hourly_sample_to_a_few_minutes():
    vals = _sine(48)
    first_high = next(i for i, k in tidemath.find_extrema(vals) if k == "high")
    x, h = tidemath.refine_extremum(vals, first_high)
    true_x = 2.3 + PERIOD_H / 4  # the sine's first crest, in hours
    assert abs(x - true_x) * 60 < 3.0, "crest should be placed within three minutes"
    assert abs(h - 1.0) < 0.01


def test_a_wobble_on_the_way_down_does_not_count_as_a_turn():
    # One clean cycle with a small bump on the ebb: two highs in a row,
    # and only the real one survives.
    vals = [
        0.0,
        0.5,
        0.9,
        1.0,
        0.9,
        0.5,
        0.55,
        0.4,
        0.0,
        -0.5,
        -0.9,
        -1.0,
        -0.9,
        -0.5,
        0.0,
        0.5,
        0.9,
        1.0,
        0.9,
    ]
    found = tidemath.find_extrema(vals)
    assert found == [(3, "high"), (11, "low"), (17, "high")]


def test_flat_crest_counts_once():
    vals = [0.0, 0.5, 1.0, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.0]
    found = tidemath.find_extrema(vals)
    assert [k for _, k in found] == ["high", "low"]


def test_parabola_through_three_equal_points_stays_put():
    assert tidemath.refine_extremum([1.0, 1.0, 1.0], 1) == (1.0, 1.0)


def test_smoothing_keeps_the_ends_and_most_of_the_amplitude():
    vals = _sine(48)
    out = tidemath.smooth(vals)
    assert out[0] == vals[0] and out[-1] == vals[-1]
    assert 0.9 < max(out) <= max(vals)
    assert min(vals) <= min(out) < -0.9


def test_catmull_rom_passes_through_the_samples():
    vals = _sine(24)
    for i, v in enumerate(vals):
        assert abs(tidemath.catmull_rom(vals, float(i)) - v) < 1e-9
    assert (
        abs(
            tidemath.catmull_rom(vals, 2.5)
            - math.sin(2 * math.pi * (2.5 - 2.3) / PERIOD_H)
        )
        < 0.01
    )


# ----- the hand --------------------------------------------------------


def test_hand_runs_from_high_at_the_top_through_low_at_the_bottom():
    assert tidemath.hand_angle(tidemath.hand_fraction(0, 0, 745)) == 0.0
    assert (
        tidemath.hand_angle(tidemath.hand_fraction(745, 0, 745)) == 0.0
    )  # 360 wraps to the top
    assert (
        abs(tidemath.hand_angle(tidemath.hand_fraction(372.5, 0, 745)) - 180.0) < 1e-9
    )
    assert (
        abs(tidemath.hand_angle(tidemath.hand_fraction(186.25, 0, 745)) - 90.0) < 1e-9
    )


def test_hand_points_straight_down_at_low_water_on_an_unequal_tide():
    # High at 0, low after 4 h 25 (265 min), next high at 11 h 02 (662 min),
    # as a mixed tide does: the hand still reads LOW at the low.
    assert (
        abs(tidemath.hand_angle(tidemath.hand_fraction(265, 0, 662, None, 265)) - 180.0)
        < 1e-9
    )
    # Half way through the flood it is half way back up the left side.
    assert (
        abs(
            tidemath.hand_angle(tidemath.hand_fraction(463.5, 0, 662, 265, None))
            - 270.0
        )
        < 1e-9
    )
    # Without the low it falls back to the plain fraction of the whole cycle.
    assert abs(tidemath.hand_angle(tidemath.hand_fraction(331, 0, 662)) - 180.0) < 1e-9


def test_hand_assumes_a_mean_cycle_when_an_end_is_missing():
    assert abs(tidemath.hand_fraction(0, None, 745) - (1 - 745 / 745)) < 1e-9
    assert abs(tidemath.hand_fraction(372.5, 0, None) - 0.5) < 1e-9
    assert tidemath.hand_fraction(10, None, None) == 0.0


# ----- sun -------------------------------------------------------------


def test_sunrise_and_sunset_for_melbourne_on_a_known_day():
    rise, sett = tidemath.sun_times(
        date(2026, 10, 28), -37.81, 144.96, ZoneInfo("Australia/Melbourne")
    )
    assert rise.strftime("%H:%M") in ("06:16", "06:17", "06:18")
    assert sett.strftime("%H:%M") in ("19:48", "19:49", "19:50")


def test_sun_never_sets_in_a_polar_summer():
    assert tidemath.sun_times(date(2026, 6, 21), 78.0, 15.0, timezone.utc) == (
        None,
        None,
    )


# ----- moon ------------------------------------------------------------


def test_moon_phase_cycles_through_the_month():
    new = tidemath.moon_phase(tidemath.NEW_MOON_REF)
    assert new["illum"] < 0.001 and new["name"] == "New moon"
    full = tidemath.moon_phase(datetime(2000, 1, 21, 4, 40, tzinfo=timezone.utc))
    assert full["illum"] > 0.99 and full["name"] == "Full moon"
    quarter = tidemath.moon_phase(datetime(2000, 1, 14, 1, 30, tzinfo=timezone.utc))
    assert abs(quarter["illum"] - 0.5) < 0.05 and quarter["waxing"]

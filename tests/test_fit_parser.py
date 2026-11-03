import pandas as pd
import pytest

from pacemaker.fit_parser import analyze_records


def records(cadences, speeds=None, fractional=None):
    n = len(cadences)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-11-03 06:00", periods=n, freq="1s", tz="UTC"),
        "cadence": cadences,
        "fractional_cadence": fractional if fractional is not None else [0.0] * n,
        "enhanced_speed": speeds if speeds is not None else [2.5] * n,
    })


def test_cadence_is_doubled_into_spm():
    """Garmin stores one foot, so 85 strides/min is 170 steps/min."""
    stats = analyze_records(records([85] * 60))
    assert stats.median == 170.0
    assert stats.mean == 170.0


def test_fractional_cadence_counts():
    stats = analyze_records(records([84] * 10, fractional=[0.5] * 10))
    assert stats.median == 169.0


def test_pauses_and_walking_are_excluded():
    # 50 samples running, 10 stopped (cadence 0), 10 walking (slow)
    cadences = [85] * 50 + [0] * 10 + [60] * 10
    speeds = [2.5] * 50 + [0.0] * 10 + [0.5] * 10
    stats = analyze_records(records(cadences, speeds))
    assert stats.samples == 50
    assert stats.median == 170.0


def test_histogram_and_blocks():
    stats = analyze_records(records([85] * 600))
    assert stats.histogram["170-174"] == 600
    assert stats.histogram["165-169"] == 0
    assert stats.blocks == {0: 170.0, 5: 170.0}


def test_session_fields_are_reported():
    stats = analyze_records(records([85] * 10),
                            {"sport": "running", "total_distance": 6324.58,
                             "total_timer_time": 2529.024})
    assert (stats.sport, stats.distance_km, stats.duration_min) == ("running", 6.32, 42.2)


def test_suggested_target_is_the_median():
    stats = analyze_records(records([70] * 10 + [85] * 90))
    assert stats.suggested_target == stats.median == 170.0
    assert stats.mean < stats.median  # the warm-up drags the mean down


def test_activity_without_cadence_is_rejected():
    with pytest.raises(ValueError):
        analyze_records(pd.DataFrame({"timestamp": [], "enhanced_speed": []}))

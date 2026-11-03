"""Garmin FIT activity file -> running cadence statistics in SPM.

Garmin stores running cadence as strides per minute (one foot), so the real
step frequency is ``(cadence + fractional_cadence) * 2``. An activity whose
Garmin Connect summary says 84 is actually 168 SPM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import fitdecode
import pandas as pd

HIST_BINS = [0, 155, 160, 165, 170, 175, 180, 1000]
HIST_LABELS = ["<155", "155-159", "160-164", "165-169", "170-174", "175-179", "180+"]


@dataclass
class CadenceStats:
    """Cadence summary for one activity. All cadence values are in SPM."""

    sport: str | None
    distance_km: float | None
    duration_min: float | None
    samples: int
    mean: float
    median: float
    mode: float
    p5: float
    p95: float
    minimum: float
    maximum: float
    histogram: dict[str, int] = field(default_factory=dict)
    blocks: dict[int, float] = field(default_factory=dict)
    """Median SPM per 5-minute block, for later phase-aware ordering."""

    @property
    def suggested_target(self) -> float:
        """Median beats a mean: warm-ups and pauses drag the mean down."""
        return self.median


def read_records(path: str) -> tuple[pd.DataFrame, dict]:
    """Return the per-second record messages and the session message."""
    rows: list[dict] = []
    session: dict = {}
    with fitdecode.FitReader(path) as reader:
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                rows.append({f.name: f.value for f in frame.fields})
            elif frame.name == "session" and not session:
                session = {f.name: f.value for f in frame.fields}
    return pd.DataFrame(rows), session


def analyze_records(df: pd.DataFrame, session: dict | None = None,
                    min_speed: float = 1.0) -> CadenceStats:
    """Compute cadence statistics from record rows.

    Samples with no cadence or a speed at or below ``min_speed`` m/s are
    dropped, so pauses and walk breaks do not skew the result.
    """
    session = session or {}
    if df.empty or "cadence" not in df:
        raise ValueError("no cadence data in this activity")

    frac = df["fractional_cadence"].fillna(0) if "fractional_cadence" in df else 0
    spm_all = (df["cadence"] + frac) * 2

    moving = df["cadence"] > 0
    speed = df["enhanced_speed"] if "enhanced_speed" in df else df.get("speed")
    if speed is not None:
        moving = moving & (speed.fillna(0) > min_speed)
    spm = spm_all[moving]
    if spm.empty:
        raise ValueError("no moving samples with cadence in this activity")

    hist = pd.cut(spm, HIST_BINS, right=False, labels=HIST_LABELS).value_counts(sort=False)

    blocks: dict[int, float] = {}
    if "timestamp" in df:
        minutes = (df.loc[moving, "timestamp"] - df["timestamp"].iloc[0]).dt.total_seconds() / 60
        blocks = spm.groupby((minutes // 5 * 5).astype(int)).median().round(1).to_dict()

    distance = session.get("total_distance")
    duration = session.get("total_timer_time")
    return CadenceStats(
        sport=session.get("sport"),
        distance_km=round(distance / 1000, 2) if distance else None,
        duration_min=round(duration / 60, 1) if duration else None,
        samples=int(len(spm)),
        mean=round(float(spm.mean()), 1),
        median=float(spm.median()),
        mode=float(spm.round().mode().iloc[0]),
        p5=float(spm.quantile(0.05)),
        p95=float(spm.quantile(0.95)),
        minimum=float(spm.min()),
        maximum=float(spm.max()),
        histogram={str(k): int(v) for k, v in hist.items()},
        blocks={int(k): float(v) for k, v in blocks.items()},
    )


def analyze(path: str, min_speed: float = 1.0) -> CadenceStats:
    df, session = read_records(path)
    return analyze_records(df, session, min_speed)

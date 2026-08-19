"""Oś czasu nagrania — WYKRYWANA, a nie zakładana.

Oś czasu ma w każdym pokoleniu pliku inne znaczenie. Widzieliśmy trzy:
`timestamp` jako bezwzględne nanosekundy (elapsedRealtimeNanos),
`timestamp` jako ODSTĘP od poprzedniej próbki w milisekundach oraz ten sam
odstęp w sekundach — obok kolumny `time` z czasem narastającym.
Interpretacja wprost, bez rozpoznania jednostki, myli się o trzy rzędy
wielkości i animacja leci 1000× za szybko albo za wolno.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .constants import DT_MAX, DT_MIN, FALLBACK_FS, GAP_DT, TIME_COLUMNS, TIME_UNITS


@dataclass(frozen=True)
class TimeAxis:
    """Czas w sekundach od początku nagrania, razem z jego pochodzeniem.

    `source` trafia wprost do interfejsu: bez niego nie da się odróżnić
    czasu odczytanego z pliku od zgadniętych 100 Hz. `gaps` liczy przerwy
    dłuższe niż GAP_DT, czyli te, które musieliśmy skrócić.
    """

    seconds: np.ndarray
    source: str
    gaps: int

    @classmethod
    def detect(cls, df: pd.DataFrame, columns: dict, rows: int) -> "TimeAxis":
        """Pierwsza kolumna czasu, którą da się zinterpretować sensownie."""
        for key in TIME_COLUMNS:
            if key not in columns:
                continue

            raw = cls._numeric_column(df, columns[key])
            if raw is None:
                continue

            axis = cls._from_column(raw, key)
            if axis is None:
                continue

            seconds, source = axis
            return cls._without_gaps(seconds, source)

        return cls(np.arange(rows) / FALLBACK_FS,
                   f"brak kolumny czasu — założono {FALLBACK_FS:g} Hz", 0)

    # ------------------------------------------------------------
    #  KROKI ROZPOZNANIA
    # ------------------------------------------------------------

    @staticmethod
    def _numeric_column(df: pd.DataFrame, name):
        """Kolumna jako liczby, z dziurami zalepionymi sąsiadami. None =
        po zalepieniu nadal zostały wartości nieliczbowe."""
        raw = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=np.float64)
        if not np.isfinite(raw).all():
            raw = pd.Series(raw).ffill().bfill().to_numpy()
        return raw if np.isfinite(raw).all() else None

    @classmethod
    def _from_column(cls, raw, name):
        """Zamienia surową kolumnę czasu na sekundy od początku nagrania.

        Kolumna bywa dwojaka i rozpoznajemy to po monotoniczności:
          • rosnąca  → czas bezwzględny, liczy się różnica względem
                       pierwszej próbki;
          • skacząca → ODSTĘP od poprzedniej próbki, liczy się suma.
        Jednostkę (s / ms / µs / ns) wybiera _unit_for po typowej wielkości.
        """
        steps = np.diff(raw)
        if len(steps) == 0:
            return None

        if np.mean(steps > 0) > 0.95:
            unit, scale = cls._unit_for(float(np.median(steps)))
            if unit:
                return (raw - raw[0]) * scale, f"{name} narastający [{unit}]"

        unit, scale = cls._unit_for(float(np.median(np.abs(raw))))
        if unit:
            intervals = np.abs(raw) * scale
            # Pierwszy wiersz bywa czasem liczonym od startu urządzenia
            # (widzieliśmy tam 15 sekund przy próbkowaniu 10 ms) — dla nas
            # nagranie zaczyna się w zerze.
            intervals[0] = 0.0
            return np.cumsum(intervals), f"{name} — odstęp próbek [{unit}]"

        return None

    @staticmethod
    def _unit_for(step):
        """Jednostka, przy której `step` jest sensownym odstępem próbkowania."""
        if not math.isfinite(step) or step <= 0:
            return None, None
        for name, scale in TIME_UNITS:
            if DT_MIN <= step * scale <= DT_MAX:
                return name, scale
        return None, None

    @classmethod
    def _without_gaps(cls, seconds, source):
        """Skraca przerwy w nagraniu do GAP_DT i liczy, ile ich było.

        Luka to pauza w nagraniu (uśpiony czujnik), a nie próbka trwająca
        minutę. Zostawiona w całości zatrzymywałaby animację w miejscu.
        """
        steps = np.diff(seconds)
        gaps = int((steps > GAP_DT).sum())
        steps = np.clip(steps, DT_MIN, GAP_DT)
        return cls(np.concatenate([[0.0], np.cumsum(steps)]), source, gaps)

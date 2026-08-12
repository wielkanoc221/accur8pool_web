"""Zapis z czujników: wczytanie pliku i wycinanie z niego okna.

Podział na `SensorRecording` (cały plik) i `RecordingWindow` (jeden ruch)
jest celowy. Wczytanie kosztuje tyle, ile plik — kilkadziesiąt tysięcy
wierszy — a rachunki dotyczą zawsze wycinka długości jednego uderzenia
i zależą od tego, który to wycinek. Dlatego `SensorRecording` niczego nie
liczy poza osią czasu, a wszystko, co zależy od zakresu, powstaje
w `RecordingWindow`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .constants import (
    ACC,
    DT_MIN,
    GAP_DT,
    GYR,
    LIN,
    MIN_ROWS,
    ROT,
    ROTW,
    SMOOTH_S,
    TIME_COLUMNS,
)
from .numeric import boxcar
from .time_axis import TimeAxis


def supports(names) -> bool:
    """Czy z takim kompletem kolumn da się zbudować animację 3D.

    Potrzebna jest oś czasu i cokolwiek, z czego wyjdzie orientacja. Sam
    akcelerometr nie wystarczy — bez orientacji nie da się oddzielić
    grawitacji od ruchu.
    """
    available = {str(name).strip().lower() for name in names}
    has_time = any(key in available for key in TIME_COLUMNS)
    has_orientation = (all(key in available for key in ROT)
                       or all(key in available for key in GYR))
    return has_time and has_orientation


@dataclass(frozen=True)
class SensorRecording:
    """Surowe serie z jednego pliku CSV, w jednostkach SI."""

    rows: int
    time: TimeAxis
    acc: np.ndarray | None
    gyr: np.ndarray | None
    rot: np.ndarray | None
    lin: np.ndarray | None
    rot_w: np.ndarray | None

    # ------------------------------------------------------------
    #  WCZYTANIE
    # ------------------------------------------------------------

    @classmethod
    def from_csv(cls, path: Path) -> "SensorRecording":
        df = pd.read_csv(path)
        columns = {str(name).strip().lower(): name for name in df.columns}
        reader = _ColumnReader(df, columns)

        return cls(
            rows=len(df),
            time=TimeAxis.detect(df, columns, len(df)),
            acc=reader.group(ACC),
            gyr=reader.group(GYR),
            rot=reader.group(ROT),
            lin=reader.group(LIN),
            rot_w=reader.column(ROTW),
        )

    # ------------------------------------------------------------
    #  CHARAKTERYSTYKA
    # ------------------------------------------------------------

    @property
    def orientation_source(self) -> str | None:
        """Z czego wyjdzie orientacja: fuzja, sam wektor obrotu, samo gyro."""
        if self.rot is not None:
            return "fused" if self.gyr is not None else "rot"
        if self.gyr is not None:
            return "gyro"
        return None

    @property
    def duration(self) -> float:
        if self.rows <= 1:
            return 0.0
        return float(self.time.seconds[-1] - self.time.seconds[0])

    def describe(self) -> dict:
        """Krótka charakterystyka pliku — do komunikatów i do testów."""
        duration = self.duration
        source = self.orientation_source
        return {
            "ok": source is not None and self.rows >= MIN_ROWS,
            "source": source,
            "n": self.rows,
            "duration": duration,
            "fs": (self.rows - 1) / duration if duration > 0 else 0.0,
            "time_source": self.time.source,
            "gaps": self.time.gaps,
            "has_position": self.acc is not None or self.lin is not None,
        }

    # ------------------------------------------------------------
    #  WYCINEK
    # ------------------------------------------------------------

    def window(self, lo: int, hi: int) -> "RecordingWindow":
        """Okno [lo, hi) gotowe do rachunków — patrz RecordingWindow."""
        return RecordingWindow.of(self, lo, hi)


class _ColumnReader:
    """Wyciąga z ramki kolumny po nazwach niezależnych od wielkości liter.

    Zwraca None zamiast rzucać, gdy kolumny nie ma albo gdy po zalepieniu
    dziur sąsiadami nadal zostały wartości nieliczbowe: brak czujnika jest
    normalny (zapisy różnią się między wersjami zegarka), a rekonstrukcja
    sama zdecyduje, co da się bez niego policzyć.
    """

    def __init__(self, df: pd.DataFrame, columns: dict):
        self._df = df
        self._columns = columns

    def group(self, names) -> np.ndarray | None:
        """Trójka kolumn (x, y, z) jako tablica (N, 3)."""
        if not all(name in self._columns for name in names):
            return None

        block = self._df[[self._columns[name] for name in names]]
        block = block.apply(pd.to_numeric, errors="coerce").ffill().bfill()

        values = block.to_numpy(dtype=np.float64)
        return values if np.isfinite(values).all() else None

    def column(self, name) -> np.ndarray | None:
        if name not in self._columns:
            return None

        values = pd.to_numeric(self._df[self._columns[name]], errors="coerce")
        values = values.ffill().bfill().to_numpy(dtype=np.float64)
        return values if np.isfinite(values).all() else None


@dataclass(frozen=True)
class RecordingWindow:
    """Jeden ruch: osie i serie przycięte do zakresu wierszy [lo, hi).

    Czas jest przesunięty tak, że okno zaczyna się w zerze, a żyroskop jest
    już wygładzony oknem SMOOTH_S — tak wchodzi do każdego dalszego kroku,
    więc lepiej zrobić to raz.
    """

    lo: int
    hi: int
    seconds: np.ndarray
    dt: np.ndarray
    smooth_window: int
    acc: np.ndarray | None
    gyr: np.ndarray | None
    rot: np.ndarray | None
    lin: np.ndarray | None
    rot_w: np.ndarray | None

    @classmethod
    def of(cls, recording: SensorRecording, lo: int, hi: int) -> "RecordingWindow":
        cut = slice(lo, hi)

        seconds = recording.time.seconds[cut] - recording.time.seconds[lo]
        dt = np.diff(seconds)
        if not np.all(dt > 0):
            # Oś czasu bywa niemonotoniczna na granicy wycinka — prostujemy
            # ją tak samo jak TimeAxis prostuje przerwy w całym nagraniu.
            dt = np.clip(dt, DT_MIN, GAP_DT)
            seconds = np.concatenate([[0.0], np.cumsum(dt)])

        window = cls._smoothing_window(dt)
        gyr = recording.gyr[cut] if recording.gyr is not None else None

        return cls(
            lo=lo,
            hi=hi,
            seconds=seconds,
            dt=dt,
            smooth_window=window,
            acc=recording.acc[cut] if recording.acc is not None else None,
            gyr=boxcar(gyr, window) if gyr is not None else None,
            rot=recording.rot[cut] if recording.rot is not None else None,
            lin=recording.lin[cut] if recording.lin is not None else None,
            rot_w=recording.rot_w[cut] if recording.rot_w is not None else None,
        )

    @staticmethod
    def _smoothing_window(dt) -> int:
        """Szerokość okna wygładzania w PRÓBKACH, z tempa zapisu tego pliku.

        Stała liczba próbek znaczyłaby przy 400 Hz zupełnie co innego niż
        przy 25 Hz — SMOOTH_S jest w sekundach właśnie dlatego.
        """
        step = float(np.median(dt))
        if step <= 0:
            return 3
        return max(3, int(round(SMOOTH_S / step)))

    @property
    def sample_count(self) -> int:
        return len(self.seconds)

    @property
    def duration(self) -> float:
        return float(self.seconds[-1])

    @property
    def sample_rate(self) -> float:
        """Prawdziwe tempo zapisu w tym oknie, w Hz."""
        duration = self.duration
        if duration <= 0:
            return 0.0
        return (self.sample_count - 1) / duration

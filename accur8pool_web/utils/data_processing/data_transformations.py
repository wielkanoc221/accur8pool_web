"""Transformery ramki z surowym zapisem — łańcuch kroków na DataFrame.

Każda metoda zwraca `self`, więc przygotowanie danych czyta się jako listę
kroków (patrz prepare_raw_data.transform_raw_df), a nie jako ciąg
przypisań do tej samej zmiennej.

Dwa transformery, bo zegarek zapisuje pliki w dwóch formatach:

    DataFrameTransformerBase  `timestamp` to ODSTĘP od poprzedniej próbki
                              w milisekundach; roll i pitch liczymy sami,
    DataFrameTransformerV2    `timestamp` to znacznik BEZWZGLĘDNY
                              w nanosekundach, a roll i pitch są już
                              w pliku — policzone lądują obok, z sufiksem
                              _calculated, żeby dało się je porównać.

Wybór między nimi robi prepare_raw_data po kolumnie `csv_version`.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from pandas import DataFrame

from .const import (
    ACC_X,
    ACC_Y,
    ACC_Z,
    GYR_X,
    GYR_Y,
    JERK,
    PITCH,
    ROLL,
    TIME,
    TIMESTAMP,
)
from .utils import _normalize, calc_magnitude, calc_pitch, calc_roll, lowpass_filter

# Częstotliwość próbkowania używana WYŁĄCZNIE wtedy, gdy nie da się jej
# odczytać z osi czasu pliku.
FALLBACK_FS = 100.0

# Sensowny odstęp między próbkami w sekundach. Poza tym przedziałem
# uznajemy, że oś czasu znaczy co innego, niż nam się wydaje, i wracamy do
# FALLBACK_FS zamiast projektować filtr na przypadkowej liczbie.
MIN_DT, MAX_DT = 1e-4, 1.0


class DataFrameTransformerBase:
    """Łańcuch przekształceń jednej ramki. Każdy krok zwraca `self`."""

    def __init__(self, data: DataFrame, copy: bool = True):
        self.data = data.copy() if copy else data
        self.new_columns = []

    def result(self) -> DataFrame:
        return self.data

    # ------------------------------------------------------------
    #  OŚ CZASU
    # ------------------------------------------------------------

    def sampling_rate(self, default: float = FALLBACK_FS) -> float:
        """Częstotliwość próbkowania ODCZYTANA z danych, nie założona.

        Filtr dolnoprzepustowy projektuje się względem fs, więc wpisana
        gdzieś na sztywno setka znaczyła, że przy zapisie 400 Hz
        deklarowana granica 10 Hz wychodziła w rzeczywistości 40 Hz,
        a przy zapisie 25 Hz filtr przestawał być dolnoprzepustowy
        w ogóle. Zegarki zapisują w różnym tempie, więc tempo trzeba
        zmierzyć.

        Krok bierzemy z MEDIANY odstępów: pojedyncza dziura w zapisie
        (uśpiony czujnik, pauza) nie ma prawa przestawić całego filtra.
        """
        steps = self._sample_intervals()
        if steps is None:
            return default

        steps = steps[np.isfinite(steps) & (steps > 0)]
        if steps.size == 0:
            return default

        step = float(np.median(steps))
        if not (MIN_DT <= step <= MAX_DT):
            return default
        return 1.0 / step

    def _sample_intervals(self):
        """Odstępy między próbkami w sekundach albo None, gdy nie ma z czego."""
        if TIME in self.data.columns:
            return np.diff(np.asarray(self.data[TIME], dtype=float))

        if TIMESTAMP in self.data.columns:
            # Po dt_ms_to_sec/add_time TIMESTAMP niesie odstęp w sekundach.
            # Pierwszy wiersz odpada — tam odstępu jeszcze nie ma.
            return np.asarray(self.data[TIMESTAMP], dtype=float)[1:]

        return None

    def dt_ms_to_sec(self, dt_col: str = TIMESTAMP) -> "DataFrameTransformerBase":
        self.data[dt_col] = self.data[dt_col] / 1000.0
        return self

    def add_time(self, dt_col: str = TIMESTAMP,
                 time_col: str = TIME) -> "DataFrameTransformerBase":
        """Czas narastający od zera, z sumy odstępów."""
        self.data[time_col] = self.data[dt_col].cumsum()
        self.new_columns.append(time_col)
        return self

    # ------------------------------------------------------------
    #  FILTRY I WYGŁADZANIE
    # ------------------------------------------------------------

    def lowpass(self, columns: Sequence[str], cutoff: float,
                fs: float = None) -> "DataFrameTransformerBase":
        """Filtruje wskazane kolumny względem PRAWDZIWEGO tempa zapisu.

        Bez `fs` częstotliwość bierze się z sampling_rate(), czyli z osi
        czasu pliku — dlatego add_time() musi iść przed lowpass()
        w łańcuchu przygotowania.

        Filtr przechodzi po kolumnie DWA RAZY (czyli efektywnie czwarty
        rząd, zbocze dwukrotnie bardziej strome). Tak liczone są wszystkie
        dotychczasowe pliki przygotowane, więc usunięcie drugiego przebiegu
        zmieniłoby dane pod całą resztą aplikacji.
        """
        if not columns:
            return self

        fs = self.sampling_rate() if fs is None else fs
        for column in columns:
            self.data[column] = lowpass_filter(self.data[column], cutoff=cutoff, fs=fs)
            self.data[column] = lowpass_filter(self.data[column], cutoff=cutoff, fs=fs)
        return self

    def smooth(self, columns: Sequence[str],
               window: int = 5) -> "DataFrameTransformerBase":
        self.data[columns] = (
            self.data[columns]
            .rolling(window=window, center=True)
            .mean()
        )
        return self

    def normalize(self, columns: Sequence[str] = None) -> "DataFrameTransformerBase":
        """Rozciąga kolumny na 0–1. Bez podanej listy bierze wszystkie poza
        osią czasu — normalizowanie czasu zamieniłoby go w numer wiersza."""
        if columns is None:
            columns = [column for column in self.data.columns
                       if column not in (TIME, TIMESTAMP)]

        self.data[columns] = self.data[columns].apply(_normalize)
        return self

    # ------------------------------------------------------------
    #  KOLUMNY POCHODNE
    # ------------------------------------------------------------

    def add_magnitude(self, source_cols: Sequence[str],
                      new_col: str) -> "DataFrameTransformerBase":
        """Długość wektora z trójki kolumn (x, y, z)."""
        if len(source_cols) != 3:
            raise ValueError("source_cols musi mieć dokładnie 3 kolumny: x, y, z")

        self.data[new_col] = calc_magnitude(
            self.data[source_cols[0]],
            self.data[source_cols[1]],
            self.data[source_cols[2]],
        )
        return self

    def add_jerk(self, source_cols: Sequence[str], time_col: str = TIME,
                 prefix: str = JERK) -> "DataFrameTransformerBase":
        """Pochodna po czasie dla każdej składowej plus jej długość.

        Liczona na PRAWDZIWEJ osi czasu (np.gradient przyjmuje ją wprost),
        więc nierówne próbkowanie nie zamienia się w fałszywe skoki.
        """
        if time_col not in self.data.columns:
            self.add_time()

        values = self.data[list(source_cols)].to_numpy()
        time_axis = self.data[time_col].to_numpy()
        jerk = np.gradient(values, time_axis, axis=0)

        for index, column in enumerate(source_cols):
            suffix = column.split("_")[-1]
            self.data[f"jerk_{suffix}"] = jerk[:, index]

        self.data[f"{prefix}_magnitude_jerk"] = np.linalg.norm(jerk, axis=1)
        return self

    def add_roll(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[ROLL] = self._roll(alpha)
        return self

    def add_pitch(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[PITCH] = self._pitch(alpha)
        return self

    def _roll(self, alpha: float):
        return calc_roll(
            acc_y=self.data[ACC_Y].tolist(),
            acc_z=self.data[ACC_Z].tolist(),
            gyr_x=self.data[GYR_X].tolist(),
            dt=self.data[TIMESTAMP].tolist(),
            alpha=alpha,
        )

    def _pitch(self, alpha: float):
        return calc_pitch(
            acc_x_list=self.data[ACC_X].tolist(),
            acc_y_list=self.data[ACC_Y].tolist(),
            acc_z_list=self.data[ACC_Z].tolist(),
            gyr_y_list=self.data[GYR_Y].tolist(),
            dt_list=self.data[TIMESTAMP].tolist(),
            alpha=alpha,
        )

    # ------------------------------------------------------------
    #  PORZĄDKI
    # ------------------------------------------------------------

    def downsample(self) -> "DataFrameTransformerBase":
        """float64 → float32, int64 → int32. Połowa pamięci, a dokładność
        czujnika i tak jest daleko poniżej float32."""
        float_columns = self.data.select_dtypes(include=["float64"]).columns
        if len(float_columns) > 0:
            self.data[float_columns] = self.data[float_columns].astype(np.float32)

        int_columns = self.data.select_dtypes(include=["int64"]).columns
        if len(int_columns) > 0:
            self.data[int_columns] = self.data[int_columns].astype(np.int32)

        return self

    def drop_first_row(self) -> "DataFrameTransformerBase":
        self.data = self.data.iloc[1:].reset_index(drop=True)
        return self

    def drop_columns_ending_with(self, suffix: str) -> "DataFrameTransformerBase":
        self.data = self.data.loc[:, ~self.data.columns.str.endswith(suffix)]
        return self


class DataFrameTransformerV2(DataFrameTransformerBase):
    """Nowszy format zapisu (kolumna `csv_version`).

    Różni się dwiema rzeczami: znacznik czasu jest BEZWZGLĘDNY
    (nanosekundy od startu urządzenia), a roll i pitch przychodzą już
    policzone przez zegarek — nasze lądują obok, z sufiksem _calculated,
    żeby dało się je z tamtymi porównać.
    """

    def add_time(self, dt_col: str = TIMESTAMP,
                 time_col: str = TIME) -> "DataFrameTransformerBase":
        """Zamienia znacznik bezwzględny na odstęp (ms) i czas narastający.

        TIMESTAMP zostaje NADPISANY odstępem, bo dalsze kroki (roll, pitch)
        oczekują tam właśnie odstępu — tak samo jak w formacie podstawowym
        po dt_ms_to_sec.
        """
        step_ns = self.data[TIMESTAMP].diff()
        step_ns[0] = 0
        self.data[TIMESTAMP] = step_ns / 1_000_000
        self.data[time_col] = self.data[TIMESTAMP].cumsum()
        return self

    def add_roll(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[ROLL + "_calculated"] = self._roll(alpha)
        return self

    def add_pitch(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[PITCH + "_calculated"] = self._pitch(alpha)
        return self

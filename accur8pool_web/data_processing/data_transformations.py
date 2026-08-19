from __future__ import annotations

from typing import Sequence
import numpy as np
import pandas as pd
from pandas import DataFrame
from .const import *
from .utils import (
    _normalize,
    calc_pitch,
    calc_roll,
    calc_magnitude,
    lowpass_filter,
)


# Tempo zapisu użyte, gdy z osi czasu nic sensownego nie da się odczytać.
# Ta sama wartość, która wcześniej była zaszyta w lowpass_filter na sztywno
# dla WSZYSTKICH plików — teraz jest ostatecznością, a nie regułą.
FALLBACK_SAMPLING_RATE = 100.0

# Sensowny odstęp próbkowania w sekundach — po nim rozpoznajemy jednostkę
# kolumny czasu. Jednostki w kolejności prób.
DT_MIN, DT_MAX = 1e-4, 1.0
TIME_SCALES = (("s", 1.0), ("ms", 1e-3), ("us", 1e-6), ("ns", 1e-9))


def _scale_for(step: float):
    """Przelicznik na sekundy, przy którym `step` jest sensownym odstępem."""
    if not np.isfinite(step) or step <= 0:
        return None
    for _, scale in TIME_SCALES:
        if DT_MIN <= step * scale <= DT_MAX:
            return scale
    return None


def _steps_seconds(values) -> np.ndarray | None:
    """Odstępy między próbkami [s] z kolumny czasu DOWOLNEGO rodzaju.

    Kolumna `timestamp` znaczy w każdym pokoleniu pliku co innego i widać
    to w katalogu z nagraniami:

        bezwzględne nanosekundy   79635264328416, 79635274657416, ...
        odstęp w milisekundach    15378.04, 9.14, 10.67, ...
        odstęp w sekundach        0.0, 0.01096, 0.00968, ...

    a kolumna `csv_version` NIE rozstrzyga, która to — pliki z jedynką
    mają i jedno, i drugie. Rozpoznajemy więc tak samo jak
    motion3d/time_axis.py: rodzaj po monotoniczności, jednostkę po
    typowej wielkości kroku.

    Zwraca None, gdy żadna interpretacja nie daje sensownego odstępu —
    wołający bierze wtedy FALLBACK_SAMPLING_RATE.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 3:
        return None

    diffs = np.diff(values)

    # Rosnąca monotonicznie → czas bezwzględny, odstęp to różnica.
    if float(np.mean(diffs > 0)) > 0.95:
        scale = _scale_for(float(np.median(diffs)))
        if scale is not None:
            return diffs * scale

    # Inaczej → kolumna JEST odstępem. Pierwszy wiersz bywa czasem od
    # startu urządzenia (widziane 15378 ms przy próbkowaniu 10 ms), więc
    # go pomijamy.
    scale = _scale_for(float(np.median(np.abs(values[1:]))))
    if scale is not None:
        return np.abs(values[1:]) * scale

    return None


def _rate_from_steps(steps) -> float:
    """Hz z odstępów między próbkami [s], odporne na przerwy i zera."""
    if steps is None:
        return FALLBACK_SAMPLING_RATE

    steps = np.asarray(steps, dtype=float)
    steps = steps[np.isfinite(steps) & (steps > 0)]
    if steps.size == 0:
        return FALLBACK_SAMPLING_RATE

    median = float(np.median(steps))
    return 1.0 / median if median > 0 else FALLBACK_SAMPLING_RATE


class DataFrameTransformerBase:
    def __init__(self, data: DataFrame, copy: bool = True):
        self.data = data.copy() if copy else data
        self.new_columns = []

    def downsample(self) -> 'DataFrameTransformerBase':

        float_columns = self.data.select_dtypes(include=['float64']).columns
        if len(float_columns) > 0:
            self.data[float_columns] = self.data[float_columns].astype(np.float32)

        int_columns = self.data.select_dtypes(include=['int64']).columns
        if len(int_columns) > 0:
            self.data[int_columns] = self.data[int_columns].astype(np.int32)

        return self

    def drop_first_row(self) -> "DataFrameTransformerBase":
        self.data = self.data.iloc[1:].reset_index(drop=True)
        return self

    def dt_ms_to_sec(self, dt_col: str = TIMESTAMP) -> "DataFrameTransformerBase":
        self.data[dt_col] = self.data[dt_col] / 1000.0
        return self

    def add_time(self, dt_col: str = TIMESTAMP, time_col: str = TIME) -> "DataFrameTransformerBase":
        self.data[time_col] = self.data[dt_col].cumsum()
        self.new_columns.append(time_col)
        return self

    def normalize(self, columns: Sequence[str] = None) -> "DataFrameTransformerBase":
        """
        Jezeli columns is None to bierze wszystkie kolumny
        z datafram z wykluczeniem TIME i DT
        """
        if columns is None:
            columns = self.data.columns
            columns = [column for column in columns if column not in [TIME, TIMESTAMP]]

        self.data[columns] = self.data[columns].apply(_normalize)
        return self

    def smooth(self, columns: Sequence[str], window: int = 5) -> "DataFrameTransformerBase":
        self.data[columns] = (
            self.data[columns]
            .rolling(window=window, center=True)
            .mean()
        )
        return self

    def sampling_rate(self) -> float:
        """Tempo zapisu w Hz, odczytane z osi czasu TEGO pliku.

        Potrzebne filtrowi: granica przepuszczania podaje się w ułamku
        częstotliwości Nyquista, więc przy zaszytym na sztywno fs = 100 Hz
        deklarowane 5 Hz wychodziło na zapisie 400 Hz w rzeczywistości
        20 Hz — filtr zostawiał czterokrotnie więcej wysokich
        częstotliwości, niż obiecywał, i to bez żadnego sygnału.

        Mediana, nie średnia: pojedyncza przerwa w nagraniu (uśpiony
        czujnik) potrafi być tysiąc razy dłuższa od zwykłego odstępu
        i zaniżyłaby średnią o rzędy wielkości.
        """
        kolumna = TIME if TIME in self.data.columns else TIMESTAMP
        return _rate_from_steps(_steps_seconds(self.data[kolumna].to_numpy()))

    def lowpass(self, columns: Sequence[str], cutoff: float) -> "DataFrameTransformerBase":
        """Filtr dolnoprzepustowy na tych kolumnach, KTÓRE PLIK MA.

        Listy wołających opisują komplet czujników, ale zapis bez
        magnetometru albo bez linacc* jest normalny i ma się przygotować
        tak samo. Wcześniej kończyło się to KeyError-em w środku
        transformacji, czyli odmową przyjęcia poprawnego pliku.
        """
        rate = self.sampling_rate()
        for col in columns:
            if col not in self.data.columns:
                continue
            self.data[col] = lowpass_filter(self.data[col], cutoff=cutoff, fs=rate)
        return self

    def add_magnitude(
            self,
            source_cols: Sequence[str],
            new_col: str,
    ) -> "DataFrameTransformerBase":
        if len(source_cols) != 3:
            raise ValueError("source_cols musi mieć dokładnie 3 kolumny: x, y, z")

        self.data[new_col] = calc_magnitude(
            self.data[source_cols[0]],
            self.data[source_cols[1]],
            self.data[source_cols[2]],
        )
        return self

    def add_jerk(
            self,
            source_cols: Sequence[str],
            time_col: str = TIME,
            prefix: str = JERK,
    ) -> "DataFrameTransformerBase":
        if time_col not in self.data.columns:
            self.add_time()

        acc = self.data[list(source_cols)].to_numpy()
        time_axis = self.data[time_col].to_numpy()

        jerk = np.gradient(acc, time_axis, axis=0)

        for i, col in enumerate(source_cols):
            suffix = col.split("_")[-1]
            self.data[f"jerk_{suffix}"] = jerk[:, i]

        self.data[f"{prefix}_magnitude_jerk"] = np.linalg.norm(jerk, axis=1)
        return self

    def add_roll(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[ROLL] = calc_roll(
            acc_y=self.data[ACC_Y].tolist(),
            acc_z=self.data[ACC_Z].tolist(),
            gyr_x=self.data[GYR_X].tolist(),
            dt=self.data[TIMESTAMP].tolist(),
            alpha=alpha,
        )
        return self

    def add_pitch(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[PITCH] = calc_pitch(
            acc_x_list=self.data[ACC_X].tolist(),
            acc_y_list=self.data[ACC_Y].tolist(),
            acc_z_list=self.data[ACC_Z].tolist(),
            gyr_y_list=self.data[GYR_Y].tolist(),
            dt_list=self.data[TIMESTAMP].tolist(),
            alpha=alpha,
        )
        return self

    def drop_columns_ending_with(self, suffix: str) -> "DataFrameTransformerBase":
        self.data = self.data.loc[:, ~self.data.columns.str.endswith(suffix)]
        return self

    def result(self) -> DataFrame:
        return self.data


class DataFrameTransformerV2(DataFrameTransformerBase):
    def __init__(self, data: DataFrame, copy: bool = True):
        super().__init__(data, copy)

    # sampling_rate() NIE jest tu nadpisywane: _steps_seconds rozpoznaje
    # rodzaj kolumny czasu sam, a pliki z csv_version = 1 mają i znaczniki
    # bezwzględne, i odstępy — więc wersja formatu i tak by tego nie
    # rozstrzygnęła.

    def add_time(self, dt_col: str = TIMESTAMP, time_col: str = TIME) -> "DataFrameTransformerBase":
        dt_ns = self.data[TIMESTAMP].diff()
        dt_ns[0] = 0
        self.data[TIMESTAMP] = dt_ns / 1_000_000
        self.data[time_col] = self.data[TIMESTAMP].cumsum()

        return self

    def add_roll(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[ROLL + '_calculated'] = calc_roll(
            acc_y=self.data[ACC_Y].tolist(),
            acc_z=self.data[ACC_Z].tolist(),
            gyr_x=self.data[GYR_X].tolist(),
            dt=self.data[TIMESTAMP].tolist(),
            alpha=alpha,
        )
        return self

    def add_pitch(self, alpha: float = 0.98) -> "DataFrameTransformerBase":
        self.data[PITCH + "_calculated"] = calc_pitch(
            acc_x_list=self.data[ACC_X].tolist(),
            acc_y_list=self.data[ACC_Y].tolist(),
            acc_z_list=self.data[ACC_Z].tolist(),
            gyr_y_list=self.data[GYR_Y].tolist(),
            dt_list=self.data[TIMESTAMP].tolist(),
            alpha=alpha,
        )
        return self


if __name__ == '__main__':
    df = pd.read_csv(r"C:\dane_z_dzisiaj\Download\data20260718_200831.csv")
    dt = DataFrameTransformerV2(df)
    df = dt.add_time().result()
    print(df[TIME])

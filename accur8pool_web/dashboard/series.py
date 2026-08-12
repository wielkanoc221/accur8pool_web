"""Serie liczbowe z pliku CSV i ich decymacja do rozdzielczości ekranu.

Wykres 2D nigdy nie dostaje wszystkich wierszy — plik potrafi mieć ich
kilkaset tysięcy, a ekran ma kilka tysięcy pikseli. Decymacja jest tu
MIN/MAX, nie „co n-ty wiersz”: pojedynczy pik trwający jedną próbkę
zostaje zachowany co do wartości, bo jest ekstremum swojego kubełka.

Ten sam plik czyta rekonstrukcja 3D, ale przez motion3d.prepare — tam
wartości muszą zostać w jednostkach fizycznych, a tutaj są znormalizowane
do 0–1, żeby kilkanaście przebiegów o różnych rzędach wielkości dało się
oglądać na jednej osi Y.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

# Ile kubełków ma domyślnie wykres. Przeglądarka podaje własną liczbę
# wyliczoną z szerokości okna — ta wartość jest dla pierwszego rysowania,
# zanim ktokolwiek o cokolwiek zapyta.
TARGET_BUCKETS = 2500


class Series:
    """Kolumny liczbowe jednego pliku, znormalizowane do 0–1.

    Trzymamy float32 zamiast float64 (połowa pamięci, a i tak rysujemy
    z dokładnością do piksela) i wyłącznie kolumny liczbowe.
    """

    def __init__(self, names: list, values: np.ndarray):
        self.names = names
        self.values = values

    @property
    def rows(self) -> int:
        return self.values.shape[0] if self.names else 0

    def __bool__(self) -> bool:
        return bool(self.names)

    # ------------------------------------------------------------
    #  WCZYTANIE
    # ------------------------------------------------------------

    @classmethod
    def from_csv(cls, path: Path) -> "Series":
        """Wczytuje plik z pamięcią podręczną unieważnianą przez sam system
        plików — patrz _load_cached."""
        stat = path.stat()
        names, values = _load_cached(str(path), stat.st_mtime_ns, stat.st_size)
        return cls(names, values)

    # ------------------------------------------------------------
    #  ODCZYT DLA WYKRESU
    # ------------------------------------------------------------

    def payload(self, names=None, lo: int = 0, hi: int = None,
                buckets: int = TARGET_BUCKETS) -> dict:
        """{nazwa: {"x": [...], "y": [...]}} po decymacji zakresu [lo, hi)."""
        hi = self.rows if hi is None else min(hi, self.rows)
        lo = max(0, lo)

        out = {}
        for index, name in enumerate(self.names):
            if names is not None and name not in names:
                continue

            column = self.values[:, index]
            picked = minmax_indices(column, lo, hi, buckets)
            out[name] = {
                "x": picked.tolist(),
                # NaN nie przechodzi przez JSON — None rysuje się jako przerwa
                "y": [None if math.isnan(value) else float(value)
                      for value in column[picked]],
            }
        return out


def minmax_indices(column: np.ndarray, lo: int, hi: int, buckets: int) -> np.ndarray:
    """Indeksy punktów do narysowania dla jednej serii w zakresie [lo, hi).

    Dzielimy zakres na kubełki i z każdego bierzemy MINIMUM I MAKSIMUM.
    To jest cała różnica względem brania co n-tego wiersza: przy stride pik
    znika, jeśli nie trafi akurat w wielokrotność kroku — a przy danych
    z akcelerometru to właśnie piki są tym, na co się patrzy.

    Ta sama metoda jest używana w oscyloskopach cyfrowych i edytorach audio
    do rysowania przebiegów.
    """
    count = hi - lo
    if count <= 0:
        return np.empty(0, dtype=np.int64)

    # Mniej punktów niż miejsca na wykresie — rysujemy wszystko bez zmian
    if count <= buckets * 2:
        return np.arange(lo, hi, dtype=np.int64)

    edges = np.linspace(lo, hi, buckets + 1).astype(np.int64)
    picked = np.empty(buckets * 2, dtype=np.int64)

    for bucket in range(buckets):
        start, end = edges[bucket], edges[bucket + 1]
        chunk = column[start:end] if end > start else None

        if chunk is None or np.all(np.isnan(chunk)):
            picked[2 * bucket] = picked[2 * bucket + 1] = start
            continue

        picked[2 * bucket] = start + int(np.nanargmin(chunk))
        picked[2 * bucket + 1] = start + int(np.nanargmax(chunk))

    # unique sortuje i usuwa duplikaty (kubełek, w którym min == max)
    return np.unique(picked)


@lru_cache(maxsize=2)
def _load_cached(path_str: str, mtime: int, size: int):
    """Wczytuje CSV i zwraca gotowe do rysowania, znormalizowane serie.

    Klucz cache zawiera mtime i rozmiar pliku, więc podmiana pliku na dysku
    unieważnia wpis sama z siebie — nie trzeba niczego czyścić.

    maxsize=2 jest celowo małe: jedna ramka z pliku 300 MB potrafi zająć
    kilka GB RAM-u.
    """
    df = pd.read_csv(path_str)

    numeric = df.select_dtypes(include=["number"])
    numeric = numeric.drop(columns=[c for c in ("index",) if c in numeric.columns])

    names = list(numeric.columns)
    if not names:
        return [], np.empty((0, 0), dtype=np.float32)

    return names, _normalized(numeric.to_numpy(dtype=np.float32, copy=True))


def _normalized(values: np.ndarray) -> np.ndarray:
    """Każda kolumna rozciągnięta na 0–1; kolumna stała wychodzi zerami."""
    column_min = np.nanmin(values, axis=0)
    column_max = np.nanmax(values, axis=0)

    span = column_max - column_min
    flat = span == 0
    span[flat] = 1.0

    values = (values - column_min) / span
    values[:, flat] = 0.0
    return values

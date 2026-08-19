"""Wybór klatek animacji z próbek pliku.

KLATKA = PRAWDZIWA PRÓBKA Z PLIKU. Nie ma tu przepróbkowania na okrągły
raster typu 60 kl/s — każda klatka to jeden wiersz CSV, ze swoim własnym,
zmierzonym czasem. Czujnik nie próbkuje idealnie równo i ta nierówność
jest częścią tego, jak ruch naprawdę wyglądał.

Decymacja (co k-ta próbka) to jedyne miejsce, w którym z animacji wypadają
całe pomiary — i wchodzi dopiero wtedy, gdy wołający sam poprosi o limit
albo gdy okno nie mieści się w MAX_FRAMES.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .constants import MAX_FRAMES


@dataclass(frozen=True)
class FrameSampler:
    """Numery próbek, które trafią na klatki animacji."""

    indices: np.ndarray
    stride: int

    @classmethod
    def of(cls, sample_count: int, sample_rate: float, fps_limit: float) -> "FrameSampler":
        """`fps_limit` nie jest tempem docelowym, tylko GÓRNYM LIMITEM.

        Gdy plik ma gęstsze próbkowanie, bierzemy co k-tą próbkę. Nadal
        prawdziwą — nigdy uśrednioną.
        """
        stride = cls._stride(sample_count, sample_rate, fps_limit)
        indices = np.arange(0, sample_count, stride)
        if indices[-1] != sample_count - 1:
            # Ostatnia próbka wchodzi zawsze — bez niej animacja kończyłaby
            # się przed końcem zaznaczonego zakresu.
            indices = np.append(indices, sample_count - 1)
        return cls(indices, stride)

    @staticmethod
    def _stride(sample_count: int, sample_rate: float, fps_limit: float) -> int:
        stride = 1
        if fps_limit > 0 and sample_rate > fps_limit:
            # Margines na zaokrąglenie: przy zapisie dokładnie 400 Hz
            # i limicie 100 iloraz wychodzi 400.0000000001, a bez tego
            # `ceil` robiłby z tego krok 5 zamiast 4 i bez powodu wyrzucał
            # co piątą próbkę.
            stride = max(1, int(math.ceil(sample_rate / fps_limit - 1e-9)))

        if math.ceil(sample_count / stride) > MAX_FRAMES:
            stride = int(math.ceil(sample_count / MAX_FRAMES))
        return stride

    def __len__(self) -> int:
        return len(self.indices)

    def take(self, series: np.ndarray) -> np.ndarray:
        """Wycina z serii próbek same klatki."""
        return series[self.indices]

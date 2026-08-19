"""Orientacja nadgarstka w oknie ruchu.

Zegarek mierzy orientację na dwa niezależne sposoby: wektorem obrotu
(wyjście fuzji czujników) i żyroskopem. Ta klasa wybiera między nimi
i mówi wprost, co wybrała — `description` idzie do interfejsu, bo bez
niego nie da się odróżnić pomiaru od ścieżki awaryjnej.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import quaternions as quat
from .constants import ROT_GYRO_MAX
from .errors import Motion3DError
from .numeric import boxcar, derivative
from .recording import RecordingWindow


@dataclass(frozen=True)
class Orientation:
    """Obrót urządzenie → świat w każdej próbce okna, razem z pochodnymi.

    `rot_vs_gyro` to rozjazd wektora obrotu z żyroskopem w
    wielokrotnościach samego sygnału; None znaczy, że nie było czym
    porównać.
    """

    q: np.ndarray                  # kwaterniony (N, 4)
    matrices: np.ndarray           # macierze obrotu (N, 3, 3)
    omega: np.ndarray              # prędkość kątowa w układzie urządzenia
    domega: np.ndarray             # jej pochodna po czasie
    source: str                    # "fused" | "rot" | "gyro"
    description: str
    smoothed: bool
    rot_vs_gyro: float | None

    @classmethod
    def solve(cls, window: RecordingWindow, smooth: bool = True) -> "Orientation":
        """Odtwarza orientację najlepszą dostępną metodą.

        Kolejność jest ustalona: wektor obrotu wygrywa, dopóki zgadza się
        z żyroskopem; bez wektora zostaje całkowanie żyroskopu; bez obu nie
        ma z czego liczyć niczego i to jest błąd dla użytkownika.
        """
        solver = _OrientationSolver(window, smooth)

        if window.rot is not None:
            q, source, description, smoothed, mismatch = solver.from_rotation_vector()
        elif window.gyr is not None:
            q, source, description, smoothed, mismatch = solver.from_gyro()
        else:
            raise Motion3DError(
                "Plik nie zawiera ani rotation vectora (rot*), ani żyroskopu "
                "(gyr*) — nie ma z czego odtworzyć orientacji.")

        omega = solver.angular_velocity(q)
        return cls(
            q=q,
            matrices=quat.rotation_matrices(q),
            omega=omega,
            domega=derivative(omega, window.seconds),
            source=source,
            description=description,
            smoothed=smoothed,
            rot_vs_gyro=mismatch,
        )


class _OrientationSolver:
    """Same ścieżki odtwarzania — jedna metoda na jedno źródło danych."""

    def __init__(self, window: RecordingWindow, smooth: bool):
        self._window = window
        self._smooth = smooth

    # ------------------------------------------------------------
    #  ŹRÓDŁA ORIENTACJI
    # ------------------------------------------------------------

    def from_rotation_vector(self):
        window = self._window
        q, scalar_origin = quat.from_rotation_vector(
            window.rot, window.dt, window.rot_w, window.acc, window.gyr)

        smoothed = False
        if self._smooth:
            q, smoothed = quat.smooth_held_samples(q)

        mismatch = self._gyro_mismatch(q)

        if mismatch is not None and mismatch > ROT_GYRO_MAX:
            q, source, description, _, _ = self.from_gyro()
            description = (f"rotation vector rozjeżdża się z żyroskopem "
                           f"({mismatch:.1f}× sygnał) — orientacja całkowana "
                           f"z żyroskopu")
            return q, source, description, False, mismatch

        source = "fused" if window.gyr is not None else "rot"
        return q, source, self._describe(smoothed, scalar_origin), smoothed, mismatch

    def from_gyro(self):
        window = self._window
        q = quat.integrate_gyro(window.gyr, window.dt, window.acc)
        return q, "gyro", "orientacja całkowana z żyroskopu", False, None

    # ------------------------------------------------------------
    #  PRĘDKOŚĆ KĄTOWA
    # ------------------------------------------------------------

    def angular_velocity(self, q) -> np.ndarray:
        """Żyroskop, a gdy go nie ma — pochodna orientacji.

        Żyroskop mierzy prędkość kątową wprost i gęściej, niż aktualizuje
        się rotation vector, więc ma pierwszeństwo.
        """
        if self._window.gyr is not None:
            return self._window.gyr
        return self._smoothed_angular_velocity(q)

    def _smoothed_angular_velocity(self, q) -> np.ndarray:
        window = self._window
        return boxcar(quat.angular_velocity(q, window.seconds), window.smooth_window)

    # ------------------------------------------------------------
    #  KONTROLA JAKOŚCI
    # ------------------------------------------------------------

    def _gyro_mismatch(self, q) -> float | None:
        """Rozjazd wektora obrotu z żyroskopem, w wielokrotnościach sygnału.

        To jedyny w całej ścieżce test, który potrafi POWIEDZIEĆ, że
        orientacja jest zepsuta, zamiast ją narysować. Prędkość kątowa daje
        się policzyć na dwa niezależne sposoby: z pochodnej kwaternionów
        i wprost z żyroskopu. Na zdrowym zapisie wychodzą praktycznie te
        same przebiegi. Gdy rotation vector przeskakuje — bo przeszedł
        przez filtr dolnoprzepustowy, bo w zgubiło znak, bo plik jest
        uszkodzony — jego pochodna staje się grzebieniem igieł, a żyroskop
        zostaje gładki. Wystarczy porównać.

        Próg (ROT_GYRO_MAX) jest wysoko, bo pomyłka w drugą stronę też
        kosztuje: zdrowy rotation vector jest lepszym źródłem niż całkowany
        żyroskop, który nie ma odniesienia kursu.
        """
        gyr = self._window.gyr
        if gyr is None:
            return None

        scale = float(np.sqrt((gyr ** 2).sum(axis=1).mean()))
        if scale <= 1e-6:
            return None

        from_quaternions = self._smoothed_angular_velocity(q)
        error = np.sqrt(((from_quaternions - gyr) ** 2).sum(axis=1).mean())
        return float(error / scale)

    # ------------------------------------------------------------
    #  OPIS DLA INTERFEJSU
    # ------------------------------------------------------------

    @staticmethod
    def _describe(smoothed: bool, scalar_origin: str) -> str:
        description = "orientacja z rotation vectora"
        if smoothed:
            description += ", wygładzona między aktualizacjami czujnika"
        if scalar_origin != "rotw z pliku":
            description += f" ({scalar_origin})"
        return description

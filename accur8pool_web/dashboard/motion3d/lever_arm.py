"""Pozycja nadgarstka z modelu sztywnej dźwigni.

Uderzenie w bilardzie to ruch wahadłowy: łokieć stoi, przedramię się
kołysze, nadgarstek jedzie po łuku. Zegarek jest więc na końcu sztywnej
dźwigni obracającej się wokół nieruchomego punktu:

    p(t) = R(t) · d

gdzie R(t) to ZMIERZONA orientacja, a d — stały wektor od osi obrotu do
zegarka, wyrażony w układzie urządzenia. Nieznana jest jedna trójka liczb
na cały segment, a nie trajektoria w każdej próbce; szczegóły dopasowania
przy `LeverArm.fit`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import quaternions as quat
from .constants import G, LEVER_FIT_WARN, LEVER_MAX, LEVER_RIDGE
from .numeric import boxcar, skew
from .orientation import Orientation
from .recording import RecordingWindow


@dataclass(frozen=True)
class LeverArm:
    """Dopasowana dźwignia: wektor d, jakość dopasowania i jej pochodzenie.

    `known` = False znaczy, że pozycji nie ma z czego odtworzyć (brak
    akcelerometru albo dopasowanie, z którego nic nie wyszło). Animacja
    pokazuje wtedy sam obrót w miejscu — i mówi o tym wprost, zamiast
    rysować tor, którego nie zmierzono.
    """

    vector: np.ndarray             # d w układzie urządzenia [m]
    quality: float                 # udział przyspieszenia wytłumaczony modelem
    acc_source: str
    known: bool

    # ------------------------------------------------------------
    #  DOPASOWANIE
    # ------------------------------------------------------------

    @classmethod
    def fit(cls, window: RecordingWindow, orientation: Orientation) -> "LeverArm":
        """Najlepsza dźwignia z dostępnych serii przyspieszenia.

        Gdy plik ma i acc*, i linacc*, nie zgadujemy, która seria jest
        lepsza: dopasowujemy dźwignię do obu i bierzemy tę, którą model
        tłumaczy lepiej. Na danych z zegarka raz wygrywa jedna, raz druga —
        zależnie od tego, jak plik był zapisany.
        """
        best = None
        for source, a_world in cls._acceleration_candidates(window, orientation):
            smoothed = boxcar(a_world, window.smooth_window)
            vector, quality = _solve_least_squares(orientation, smoothed)
            if best is None or quality > best.quality:
                best = cls(vector=vector, quality=quality,
                           acc_source=source, known=True)

        if best is None:
            return cls(np.zeros(3), 0.0, "brak akcelerometru — sam obrót", False)
        if not best.vector.any():
            return cls(np.zeros(3), 0.0,
                       "przyspieszenia nie da się wytłumaczyć obrotem — sam obrót",
                       False)
        return best

    @staticmethod
    def _acceleration_candidates(window: RecordingWindow, orientation: Orientation):
        """Przyspieszenie ruchu w układzie świata, każdą dostępną drogą.

        Kolejność ma znaczenie przy remisie: acc* było w danych z zegarka
        bardziej wiarygodne, więc idzie pierwsze.
        """
        if window.acc is not None:
            yield ("acc* minus grawitacja",
                   quat.to_world(orientation.matrices, window.acc)
                   - np.array([0.0, 0.0, G]))
        if window.lin is not None:
            yield ("linacc*", quat.to_world(orientation.matrices, window.lin))

    # ------------------------------------------------------------
    #  RUCH Z DŹWIGNI
    # ------------------------------------------------------------

    @property
    def length_m(self) -> float:
        return float(np.linalg.norm(self.vector))

    def positions(self, orientation: Orientation) -> np.ndarray:
        """Tor nadgarstka [m], wyśrodkowany na zerze.

        Środek jest umowny: dźwignia daje położenie względem osi obrotu,
        a scena i tak skaluje się do zawartości.
        """
        samples = len(orientation.q)
        positions = quat.to_world(orientation.matrices,
                                  np.broadcast_to(self.vector, (samples, 3)))
        return positions - positions.mean(axis=0)

    def velocities(self, orientation: Orientation) -> np.ndarray:
        """Prędkość nadgarstka [m/s]: v = R · (ω × d)."""
        return quat.to_world(orientation.matrices,
                             np.cross(orientation.omega, self.vector))

    # ------------------------------------------------------------
    #  OPIS DLA INTERFEJSU
    # ------------------------------------------------------------

    def describe(self) -> str:
        if not self.known:
            return "bez pozycji — animacja pokazuje sam obrót nadgarstka w miejscu"

        arm_cm = self.length_m * 100
        percent = self.quality * 100
        if self.quality < LEVER_FIT_WARN:
            return (f"tor z ramienia {arm_cm:.0f} cm, ale model tłumaczy tylko "
                    f"{percent:.0f}% przyspieszenia — ruch miał dużą składową "
                    f"przesunięcia całej ręki")
        return (f"tor z ramienia {arm_cm:.0f} cm "
                f"(model tłumaczy {percent:.0f}% przyspieszenia)")


def _solve_least_squares(orientation: Orientation, a_world: np.ndarray):
    """Wektor od osi obrotu do zegarka, wyznaczony z przyspieszenia.

    Dla punktu sztywno związanego z obracającym się ciałem, w odległości d
    od nieruchomej osi obrotu (d w układzie ciała):

        p = R·d
        v = ṗ = R·[ω]ₓ·d
        a = v̇ = R·( [ω̇]ₓ + [ω]ₓ[ω]ₓ )·d

    Pierwszy człon to przyspieszenie styczne, drugi dośrodkowe. Całość jest
    LINIOWA względem d, więc M(t)·d = a(t) to zwykły przesztywniony układ
    równań: trzy niewiadome na 3·N równań. Rozwiązanie metodą najmniejszych
    kwadratów uśrednia szum zamiast go całkować — i to jest cała różnica
    względem podwójnego całkowania przyspieszenia.

    Zwraca (d, jakość), gdzie jakość ∈ [0, 1] to udział zmierzonego
    przyspieszenia wytłumaczony przez tę dźwignię. Blisko jedynki: ruch był
    obrotem wokół w miarę nieruchomego punktu, czyli dokładnie tym, czego
    oczekujemy po uderzeniu. Blisko zera: dominowało przesunięcie całego
    ramienia, którego z samego obrotu nie da się odtworzyć.
    """
    omega_skew = skew(orientation.omega)
    model = orientation.matrices @ (skew(orientation.domega)
                                    + omega_skew @ omega_skew)

    normal = np.einsum("nij,nik->jk", model, model)
    right = np.einsum("nij,ni->j", model, a_world)

    trace = float(np.trace(normal))
    if not math.isfinite(trace) or trace <= 0:
        return np.zeros(3), 0.0

    try:
        ridge = (LEVER_RIDGE * trace / 3.0) * np.eye(3)
        vector = np.linalg.solve(normal + ridge, right)
    except np.linalg.LinAlgError:
        return np.zeros(3), 0.0

    length = float(np.linalg.norm(vector))
    if not math.isfinite(length) or length < 1e-9:
        return np.zeros(3), 0.0

    # Absurdalnie długie ramię znaczy, że dopasowanie poszło w szum, ale
    # jego KIERUNEK zwykle zostaje sensowny — przycinamy więc samą długość,
    # zamiast rezygnować z pozycji w całości.
    if length > LEVER_MAX:
        vector = vector * (LEVER_MAX / length)

    # Jakość liczona z PRZYCIĘTEGO d, czyli z tego, co naprawdę zobaczy
    # użytkownik — nie z rozwiązania, które odrzuciliśmy.
    residual = float(((model @ vector - a_world) ** 2).sum())
    total = float((a_world ** 2).sum())
    quality = 1.0 - residual / total if total > 0 else 0.0

    return vector, float(np.clip(quality, 0.0, 1.0))

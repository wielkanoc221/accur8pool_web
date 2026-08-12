"""Drobne narzędzia liczbowe wspólne dla całej rekonstrukcji.

Wszystko tutaj działa na tablicach (N, 3) albo (N, 4) i nie wie nic
o czujnikach ani o scenie — dzięki temu daje się sprawdzić na jednej
linijce w konsoli.
"""

from __future__ import annotations

import numpy as np


def boxcar(x, window):
    """Średnia krocząca po osi 0, wyśrodkowana, bez przesuwania fazy.

    Liczona z sumy skumulowanej, więc koszt nie zależy od szerokości okna.
    Brzegi dopełniane odbiciem — dopełnienie krawędzią zaniżałoby średnią
    na końcach. Symetria w czasie jest tu istotna: filtr przesuwający fazę
    widać w animacji od razu jako ruch spóźniony za wykresem.
    """
    n = len(x)
    window = int(window)
    if window < 3 or n < 3:
        return x.copy()

    window = min(window, 2 * n - 1)
    if window % 2 == 0:
        window += 1
    half = window // 2

    pad = min(half, n - 1)
    padded = np.pad(x, ((pad, pad), (0, 0)), mode="reflect")
    if pad < half:                  # okno szersze niż dane — dociągamy krawędzią
        padded = np.pad(padded, ((half - pad, half - pad), (0, 0)), mode="edge")

    cumulative = np.cumsum(padded, axis=0)
    cumulative = np.concatenate([np.zeros((1, x.shape[1])), cumulative], axis=0)
    return (cumulative[window:] - cumulative[:-window]) / window


def derivative(x, t):
    """Pochodna po czasie na NIERÓWNEJ siatce próbek.

    Czujnik nie próbkuje równo (widzieliśmy odstępy od 7.2 do 12.8 ms przy
    nominalnych 10 ms), a np.gradient przyjmuje oś czasu wprost, więc
    nierówność nie zamienia się w fałszywe skoki pochodnej.
    """
    return np.gradient(x, t, axis=0, edge_order=2)


def skew(v):
    """Macierze [v]ₓ dla całej serii wektorów: [v]ₓ·u = v × u."""
    matrices = np.zeros((len(v), 3, 3))
    matrices[:, 0, 1] = -v[:, 2]
    matrices[:, 0, 2] = v[:, 1]
    matrices[:, 1, 0] = v[:, 2]
    matrices[:, 1, 2] = -v[:, 0]
    matrices[:, 2, 0] = -v[:, 1]
    matrices[:, 2, 1] = v[:, 0]
    return matrices

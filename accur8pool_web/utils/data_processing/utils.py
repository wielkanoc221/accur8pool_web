"""Funkcje liczbowe przygotowania danych: kąty, magnitudy, filtr.

Wszystko tutaj działa na zwykłych listach albo tablicach i nie zna
DataFrame'a — składa je dopiero DataFrameTransformerBase. Dzięki temu
każdą z tych funkcji da się sprawdzić na kilku liczbach w konsoli.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt


# ============================================================
#  KĄTY — FILTR KOMPLEMENTARNY
#
#  Akcelerometr zna pion, ale w ruchu mierzy głównie ruch. Żyroskop jest
#  gładki, lecz całkowany dryfuje. Filtr komplementarny bierze z każdego
#  to, w czym jest dobry: krótkoterminowo wierzy żyroskopowi (waga alfa),
#  a długoterminowo ściąga wynik do kąta z akcelerometru.
# ============================================================

DEFAULT_ALPHA = 0.98


def calc_roll(acc_y, acc_z, gyr_x, dt, alpha: float = DEFAULT_ALPHA) -> list:
    """Przechylenie wokół osi X, w radianach."""
    return _complementary_filter(
        acc_angles=np.arctan2(np.asarray(acc_y, dtype=float),
                              np.asarray(acc_z, dtype=float)),
        gyro_rates=gyr_x,
        dt_list=dt,
        alpha=alpha,
    )


def calc_pitch(acc_x_list, acc_y_list, acc_z_list, gyr_y_list, dt_list,
               alpha: float = DEFAULT_ALPHA) -> list:
    """Pochylenie wokół osi Y, w radianach."""
    acc_x = np.asarray(acc_x_list, dtype=float)
    acc_y = np.asarray(acc_y_list, dtype=float)
    acc_z = np.asarray(acc_z_list, dtype=float)

    return _complementary_filter(
        acc_angles=np.arctan2(-acc_x, np.sqrt(acc_y ** 2 + acc_z ** 2)),
        gyro_rates=gyr_y_list,
        dt_list=dt_list,
        alpha=alpha,
    )


def _complementary_filter(acc_angles, gyro_rates, dt_list, alpha: float) -> list:
    """Wspólny rdzeń roll i pitch: całkowanie żyroskopu ściągane do
    akcelerometru.

    Startujemy od kąta z PIERWSZEJ próbki akcelerometru — bez tego pierwsze
    kilkadziesiąt próbek to dojeżdżanie od zera do prawdziwego przechylenia.
    Iterujemy po najkrótszej z serii, bo zapisy bywają niepełne.
    """
    count = min(len(acc_angles), len(gyro_rates), len(dt_list))

    estimate = acc_angles[0]
    angles = []

    for index in range(count):
        estimate = estimate + gyro_rates[index] * dt_list[index]      # żyroskop
        estimate = alpha * estimate + (1 - alpha) * acc_angles[index]  # korekta
        angles.append(estimate)

    return angles


def calc_yaw_complementary(mag_x_list, mag_y_list, mag_z_list, gyr_z_list,
                           roll_list, pitch_list, deltatime_list,
                           alpha: float = DEFAULT_ALPHA) -> list:
    """Kurs z magnetometru skompensowanego przechyłem, ściągany żyroskopem.

    Kąty zawijamy do (-π, π] przy każdym kroku — także RÓŻNICĘ kątów.
    Bez tego przejście przez ±180° daje błąd bliski 2π i estymata skacze
    o pełny obrót.
    """
    count = min(len(mag_x_list), len(mag_y_list), len(mag_z_list),
                len(gyr_z_list), len(roll_list), len(pitch_list),
                len(deltatime_list))

    yaw_estimate = 0.0
    yaw_list = []

    for index in range(count):
        roll = roll_list[index]
        pitch = pitch_list[index]

        yaw_from_gyro = yaw_estimate + gyr_z_list[index] * deltatime_list[index]
        yaw_from_mag = _tilt_compensated_heading(
            mag_x_list[index], mag_y_list[index], mag_z_list[index], roll, pitch)

        error = _wrap_angle_pi(yaw_from_mag - yaw_from_gyro)
        yaw_estimate = _wrap_angle_pi(yaw_from_gyro + (1 - alpha) * error)

        yaw_list.append(yaw_estimate)

    return yaw_list


def _tilt_compensated_heading(mx, my, mz, roll, pitch):
    """Kurs z magnetometru sprowadzonego do poziomu.

    Pole magnetyczne jest mierzone w układzie urządzenia, więc przy
    przechylonym nadgarstku jego składowa pozioma miesza się z pionową.
    """
    horizontal_x = mx * np.cos(pitch) + mz * np.sin(pitch)
    horizontal_y = (mx * np.sin(roll) * np.sin(pitch)
                    + my * np.cos(roll)
                    - mz * np.sin(roll) * np.cos(pitch))
    return np.arctan2(-horizontal_y, horizontal_x)


def _wrap_angle_pi(angle):
    return (angle + np.pi) % (2 * np.pi) - np.pi


# ============================================================
#  MAGNITUDY I POCHODNE
# ============================================================

def calc_magnitude(acc_x_list, acc_y_list, acc_z_list) -> np.ndarray:
    """Długość wektora — niezależna od orientacji urządzenia, więc widać
    na niej samo uderzenie, a nie to, jak ktoś trzyma rękę."""
    x = np.asarray(acc_x_list, dtype=float)
    y = np.asarray(acc_y_list, dtype=float)
    z = np.asarray(acc_z_list, dtype=float)
    return np.sqrt(x ** 2 + y ** 2 + z ** 2)


def calc_jerk(acc_x_list, acc_y_list, acc_z_list, dt_list) -> np.ndarray:
    """Pochodna magnitudy przyspieszenia po czasie.

    Pierwsza próbka nie ma poprzedniczki, więc zostaje zerem.
    """
    magnitude = calc_magnitude(acc_x_list, acc_y_list, acc_z_list)
    dt = np.asarray(dt_list, dtype=float)

    jerk = np.zeros_like(magnitude)
    jerk[1:] = (magnitude[1:] - magnitude[:-1]) / dt[1:]
    return jerk


def _normalize(signal) -> np.ndarray:
    """Rozciąga sygnał na 0–1; stała wychodzi zerami (nie ma co rozciągać)."""
    values = np.asarray(signal, dtype=float)
    lowest, highest = np.min(values), np.max(values)

    if np.isclose(lowest, highest):
        return np.zeros_like(values)
    return (values - lowest) / (highest - lowest)


def _smooth(signal, window: int = 5) -> np.ndarray:
    """Średnia krocząca liczona z sumy skumulowanej (koszt niezależny od
    szerokości okna). Okno jest zawsze nieparzyste, żeby dało się je
    wyśrodkować."""
    values = np.asarray(signal, dtype=float)
    if values.size < 3:
        return values

    window = max(3, window)
    if window % 2 == 0:
        window += 1

    cumulative = np.cumsum(np.insert(values, 0, 0))
    return (cumulative[window:] - cumulative[:-window]) / window


# ============================================================
#  FILTR
# ============================================================

def lowpass_filter(data, cutoff: float = 8, fs: float = 100):
    """Filtr Butterwortha 2. rzędu, zerofazowy (filtfilt).

    `cutoff` musi leżeć poniżej częstotliwości Nyquista — inaczej butter()
    dostaje znormalizowaną granicę >= 1 i wywraca się na ValueError.
    Zwracamy wtedy sygnał nietknięty: filtr, który miałby przepuścić
    wszystko, i tak nie ma czego odciąć, a wysypanie się na tym zabierało
    plikowi całą wersję przygotowaną.
    """
    nyquist = fs / 2.0
    if not (0 < cutoff < nyquist):
        return np.asarray(data, dtype=float)

    b, a = butter(2, cutoff / nyquist, btype="low")
    return filtfilt(b, a, data, axis=0)


def get_df_from_csv(path) -> pd.DataFrame:
    return pd.read_csv(path)

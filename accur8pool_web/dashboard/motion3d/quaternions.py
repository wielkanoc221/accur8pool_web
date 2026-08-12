"""Kwaterniony (w, x, y, z) w konwencji Androida.

Kwaternion obraca wektor Z UKŁADU URZĄDZENIA DO UKŁADU ŚWIATA, gdzie oś Z
jest pionem. Sprawdzenie na danych z zegarka: przyspieszenie obrócone do
świata ma średnią [0, 0, 9.81] — czyli samą grawitację, tak jak być
powinno.

Funkcje operują na CAŁYCH SERIACH (tablice (N, 4)), bo tak wchodzą dane
z pliku i tak są dalej używane. Nie ma tu klasy opakowującej pojedynczy
kwaternion — nic by nie wniosła poza kosztem N wywołań na próbkę.
"""

from __future__ import annotations

import math

import numpy as np

from .constants import G
from .numeric import boxcar, derivative


def normalize(q):
    length = np.linalg.norm(q, axis=1, keepdims=True)
    length[length == 0] = 1.0
    return q / length


def rotation_matrices(q):
    """Macierze obrotu URZĄDZENIE → ŚWIAT dla całej serii kwaternionów."""
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.empty((len(q), 3, 3))
    R[:, 0, 0] = 1 - 2 * (y * y + z * z)
    R[:, 0, 1] = 2 * (x * y - z * w)
    R[:, 0, 2] = 2 * (x * z + y * w)
    R[:, 1, 0] = 2 * (x * y + z * w)
    R[:, 1, 1] = 1 - 2 * (x * x + z * z)
    R[:, 1, 2] = 2 * (y * z - x * w)
    R[:, 2, 0] = 2 * (x * z - y * w)
    R[:, 2, 1] = 2 * (y * z + x * w)
    R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R


def to_world(R, v):
    """Seria wektorów z układu urządzenia do układu świata."""
    return np.einsum("nij,nj->ni", R, v)


def align_signs(q):
    """Usuwa przeskoki znaku.

    q i -q to ten sam obrót, ale różniczkowanie i wygładzanie między nimi
    przelatuje przez pół sfery — na animacji wygląda to jak obrót o 180°,
    którego w danych nie ma.
    """
    dots = np.sum(q[1:] * q[:-1], axis=1)
    signs = np.cumprod(np.where(dots < 0, -1.0, 1.0))
    q = q.copy()
    q[1:] *= signs[:, None]
    return q


def mean_world_vertical(q, acc):
    """Średnia pionowa składowa przyspieszenia obróconego do świata.

    Miara poprawności orientacji. Przy dobrej orientacji przyspieszenie
    obrócone do układu świata to średnio sama grawitacja skierowana
    w GÓRĘ, czyli [0, 0, +G] — ruch nadgarstka w oknie uderzenia zaczyna
    się i kończy w spoczynku, więc jego własne przyspieszenie ma średnią
    bliską zeru i zostaje tylko grawitacja. Przy orientacji błędnej nie ma
    powodu, żeby akurat tak wyszło.
    """
    R = rotation_matrices(normalize(q))
    return float(np.einsum("nj,nj->n", R[:, 2, :], acc).mean())


def scalar_part(rot_vec, abs_w, dt, gyr, first_sign):
    """Czwarta składowa kwaternionu RAZEM ZE ZNAKIEM.

    ROTATION_VECTOR niesie tylko trzy składowe, a Android liczy czwartą
    jako w = +sqrt(1 - |v|²). To jest prawdą TYLKO dla obrotów do 180°:
    w = cos(θ/2), więc powyżej tego kąta prawdziwe w jest UJEMNE, a
    dodatni pierwiastek daje obrót ODWROTNY do rzeczywistego.

    I nie jest to przypadek egzotyczny. Kwaternion opisuje obrót
    urządzenie → świat, a świat to układ ENU — nadgarstek nad stołem bywa
    względem niego obrócony o więcej niż 180° i przekracza tę granicę
    w ŚRODKU ruchu. Orientacja przeskakuje wtedy na odwrotną i z powrotem,
    czyli zegarek na animacji wariuje — tym częściej, im szybszy ruch, bo
    tym więcej razy granica zostaje przekroczona.

    ZNAKU NIE DA SIĘ WYBRAĆ PO SĄSIEDZTWIE
    --------------------------------------
    Kuszące jest wziąć tego z dwóch kandydatów (±|w|, v), który leży bliżej
    poprzedniego obrotu. Tyle że dokładnie w punkcie przejścia |w| = 0
    i OBAJ kandydaci są tam identyczni — różnica między nimi jest rzędu
    |w|, czyli znika w tym samym miejscu, w którym trzeba podjąć decyzję.
    Test bliskości nie przełącza więc znaku nigdy i przejście przez 180°
    zostaje niezauważone.

    Znak trzeba PRZEWIDZIEĆ, a nie wybrać. Z kinematyki kwaternionu

        ẇ = -½ · v · ω

    czyli żyroskop mówi wprost, w którą stronę w zmierza — także wtedy, gdy
    właśnie przechodzi przez zero. Wystarczy jeden krok Eulera od
    poprzedniej, już ustalonej wartości: wielkość |w| bierzemy z danych,
    a z przewidywania tylko ZNAK, więc nic się tu nie całkuje i nic nie
    dryfuje. Bez żyroskopu zostaje ekstrapolacja liniowa po dwóch
    poprzednich próbkach, która przez zero przechodzi tak samo.

    ZNAK PIERWSZEJ PRÓBKI JEST PARAMETREM, NIE ZAŁOŻENIEM
    -----------------------------------------------------
    Śledzenie jest poprawne tylko wtedy, gdy startuje z dobrego znaku. Przy
    złym starcie nachylenie z żyroskopu jest nadal prawdziwe, ale odnosi
    się do drugiej gałęzi rozwiązania — przejścia przez zero wypadają wtedy
    w złych miejscach i seria wychodzi POMIESZANA, a nie po prostu
    odwrócona. Takiego wyniku nie da się już naprawić żadnym globalnym
    odwróceniem. Dlatego `first_sign` wchodzi tu z zewnątrz:
    from_rotation_vector liczy obie gałęzie i wybiera po pionie.
    """
    n = len(rot_vec)
    w = np.empty(n)
    w[0] = first_sign * abs_w[0]

    for i in range(1, n):
        if gyr is not None:
            predicted = w[i - 1] - 0.5 * float(rot_vec[i - 1] @ gyr[i - 1]) * dt[i - 1]
        elif i >= 2:
            predicted = 2.0 * w[i - 1] - w[i - 2]
        else:
            predicted = w[i - 1]
        w[i] = abs_w[i] if predicted >= 0.0 else -abs_w[i]

    return w


def from_rotation_vector(rot_vec, dt, rot_w=None, acc=None, gyr=None):
    """Kwaternion z ROTATION_VECTOR, razem ze znakiem czwartej składowej.

    Kolumna `rotw` niesie ten znak wprost, więc gdy jest wiarygodna,
    wygrywa ze wszystkim. Bywa jednak zapisana błędnie (w jednym z plików
    ma stałą wartość 246). Dlatego bierzemy ją tylko wtedy, gdy faktycznie
    domyka kwaternion do długości 1 — ten sam test wyłapuje też rot*
    zniekształcone gdziekolwiek po drodze.

    Bez wiarygodnego rotw znak czwartej składowej odtwarza scalar_part, ale
    ten potrzebuje znaku PIERWSZEJ próbki, którego z samych rot* nie da się
    odczytać. Liczymy więc obie gałęzie rozwiązania i wybieramy tę, w
    której grawitacja wychodzi w górę (mean_world_vertical). Wyboru nie da
    się odłożyć na potem: gałęzie różnią się nie tylko globalnym znakiem,
    ale i miejscami przejść przez zero.

    Zwraca (kwaterniony, opis pochodzenia czwartej składowej).
    """
    squared = (rot_vec * rot_vec).sum(axis=1)

    # |v| > 1 jest fizycznie niemożliwe (|v| = |sin(θ/2)|), więc taka
    # próbka znaczy, że wektor obrotu został po drodze zniekształcony —
    # np. przez filtr dolnoprzepustowy, który przestrzeliwuje na zboczu.
    # Przycięcie samego `squared` dawałoby w tych miejscach w = 0, czyli
    # obrót o równe 180° wzięty znikąd; skalujemy więc CAŁY wektor
    # z powrotem na sferę, co zachowuje przynajmniej oś obrotu.
    broken = squared > 1.0
    if broken.any():
        rot_vec = rot_vec.copy()
        rot_vec[broken] /= np.sqrt(squared[broken])[:, None]
        squared = np.minimum(squared, 1.0)

    abs_w = np.sqrt(np.maximum(1.0 - squared, 0.0))

    if rot_w is not None and _closes_unit_sphere(squared, rot_w):
        q = np.column_stack([rot_w, rot_vec])
        origin = "rotw z pliku"
    else:
        branches = [
            np.column_stack([scalar_part(rot_vec, abs_w, dt, gyr, sign), rot_vec])
            for sign in (1.0, -1.0)
        ]
        if acc is None:
            # Nie ma czym rozstrzygnąć — zostaje założenie Androida (w > 0).
            q = branches[0]
            origin = "znak w z kinematyki, bez potwierdzenia pionem"
        else:
            q = max(branches, key=lambda branch: mean_world_vertical(branch, acc))
            origin = "znak w z kinematyki, gałąź wybrana wg pionu"

    if broken.any():
        origin += f", {int(broken.sum())} próbek poza sferą jednostkową"

    return align_signs(normalize(q)), origin


def _closes_unit_sphere(squared, rot_w):
    """Czy `rotw` domyka kwaternion do długości 1 na WIĘKSZOŚCI próbek."""
    return np.mean(np.abs(np.sqrt(squared + rot_w * rot_w) - 1.0) < 0.05) > 0.9


def smooth_held_samples(q):
    """Wygładza schodki rotation vectora przetrzymywanego między pomiarami.

    W części plików rotation vector aktualizuje się wolniej niż
    akcelerometr, a w CSV każdy wiersz i tak ma jakąś wartość — po prostu
    tę samą, aż przyjdzie nowa. Bez wygładzenia animacja co kilka klatek
    stoi i przeskakuje, a prędkość kątowa liczona z takiej serii to
    grzebień igieł zamiast gładkiego przebiegu.

    Zabieg to zwykła średnia krocząca o oknie równym okresowi aktualizacji
    czujnika. Na schodkach o takim właśnie kroku daje dokładnie rampę
    liniową między pomiarami, czyli to samo, co interpolacja — tyle że bez
    szukania węzłów i bez ryzyka rozciągnięcia jednej zmiany na całą
    poprzedzającą ją chwilę bezruchu. Tam, gdzie wartość stoi naprawdę,
    średnia ze stałej jest tą samą stałą.

    Zwraca (kwaterniony, czy cokolwiek zrobiono) — interfejs ma powiedzieć
    wprost, kiedy ogląda się wygładzenie, a kiedy surowy pomiar.
    """
    changes = np.nonzero(np.any(np.abs(np.diff(q, axis=0)) > 1e-9, axis=1))[0]
    if len(changes) < 2:
        return q, False

    period = int(round(float(np.median(np.diff(changes)))))
    if period < 2:
        return q, False             # czujnik nadaje w pełnym tempie

    return normalize(boxcar(q, max(3, period))), True


def angular_velocity(q, t):
    """Prędkość kątowa w układzie URZĄDZENIA, wyliczona z serii kwaternionów.

    ω = 2 · część_wektorowa(q⁻¹ ⊗ q̇). Ścieżka dla plików bez żyroskopu —
    gdy jest, bierzemy jego pomiar, bo jest gęstszy i mniej zaszumiony niż
    pochodna orientacji.
    """
    dq = derivative(q, t)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    dw, dx, dy, dz = dq[:, 0], dq[:, 1], dq[:, 2], dq[:, 3]
    return 2.0 * np.column_stack([
        w * dx - x * dw - y * dz + z * dy,
        w * dy + x * dz - y * dw - z * dx,
        w * dz - x * dy + y * dx - z * dw,
    ])


# ============================================================
#  CAŁKOWANIE ŻYROSKOPU
# ============================================================

# Siła ściągania orientacji do pionu z akcelerometru na jeden krok.
_TILT_GAIN = 0.02

# Akcelerometr mierzy prawie samą grawitację tylko w bezruchu. W trakcie
# uderzenia mierzy głównie ruch i „korekta” z niego przewróciłaby
# orientację, więc poprawiamy wyłącznie w tym paśmie.
_STILL_MIN, _STILL_MAX = 0.85 * G, 1.15 * G


def integrate_gyro(gyr, dt, acc=None):
    """Orientacja z samego żyroskopu, z korektą pionu z akcelerometru.

    Ścieżka awaryjna dla plików bez rotation vectora. Całkowanie prędkości
    kątowej dryfuje wokół pionu (żyroskop nie wie, gdzie jest dół), więc po
    każdym kroku orientacja jest delikatnie ściągana tak, aby kierunek
    zmierzonej grawitacji zgadzał się z pionem świata. Kurs (obrót wokół
    pionu) zostaje bez odniesienia — magnetometr w hali bilardowej i tak
    kłamie, a do oglądania ruchu nadgarstka kurs nie jest potrzebny.
    """
    n = len(gyr)
    q = np.empty((n, 4))
    q[0] = (1.0, 0.0, 0.0, 0.0)

    for i in range(1, n):
        rotated = _advance(q[i - 1], gyr[i], dt[i - 1])
        if acc is not None:
            rotated = _pull_towards_gravity(rotated, acc[i])
        q[i] = _unit(rotated)

    return align_signs(q)


def _advance(previous, omega, step):
    """Jeden krok całkowania: q_nowe = q_stare ⊗ dq.

    Prędkość kątowa jest w układzie ciała, stąd mnożenie z prawej strony.
    """
    wx, wy, wz = omega
    angle = math.sqrt(wx * wx + wy * wy + wz * wz) * step
    if angle <= 1e-9:
        return tuple(previous)

    w0, x0, y0, z0 = previous
    scale = math.sin(angle / 2) / (angle / step)
    dw, dx, dy, dz = math.cos(angle / 2), wx * scale, wy * scale, wz * scale
    return (
        w0 * dw - x0 * dx - y0 * dy - z0 * dz,
        w0 * dx + x0 * dw + y0 * dz - z0 * dy,
        w0 * dy - x0 * dz + y0 * dw + z0 * dx,
        w0 * dz + x0 * dy - y0 * dx + z0 * dw,
    )


def _pull_towards_gravity(q, acc):
    """Ściąga orientację tak, żeby zmierzony pion zgadzał się z pionem świata."""
    ax, ay, az = acc
    length = math.sqrt(ax * ax + ay * ay + az * az)
    if not (_STILL_MIN < length < _STILL_MAX):
        return q

    ax, ay, az = ax / length, ay / length, az / length

    w, x, y, z = q
    # pion świata (0, 0, 1) przeniesiony do układu urządzenia
    gx = 2 * (x * z - w * y)
    gy = 2 * (y * z + w * x)
    gz = 1 - 2 * (x * x + y * y)

    # Błąd = iloczyn wektorowy zmierzonego i przewidzianego pionu.
    # Wszystkie cztery składowe liczymy ze STAREGO kwaternionu —
    # podstawianie w trakcie mieszałoby dwa obroty.
    ex = ay * gz - az * gy
    ey = az * gx - ax * gz
    ez = ax * gy - ay * gx

    s = _TILT_GAIN * 0.5
    return (
        w - s * (x * ex + y * ey + z * ez),
        x + s * (w * ex + y * ez - z * ey),
        y + s * (w * ey - x * ez + z * ex),
        z + s * (w * ez + x * ey - y * ex),
    )


def _unit(q):
    w, x, y, z = q
    length = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    return w / length, x / length, y / length, z / length

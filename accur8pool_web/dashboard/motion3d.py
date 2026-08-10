"""ACCUR8POOL — rekonstrukcja ruchu 3D z surowego zapisu IMU.

Moduł jest CZYSTO LICZBOWY: dostaje ścieżkę do CSV i zakres wierszy,
oddaje gotową scenę Plotly plus klatki animacji. Nie wie nic o Django,
o segmentach w bazie ani o tym, kto na to patrzy — dzięki temu daje się
testować na danych syntetycznych o znanej kinematyce.

CO JEST TRUDNE W TYCH DANYCH
----------------------------
1. Oś czasu ma w każdym pokoleniu pliku inne znaczenie. Widzieliśmy trzy:
   `timestamp` jako bezwzględne nanosekundy (elapsedRealtimeNanos),
   `timestamp` jako ODSTĘP od poprzedniej próbki w milisekundach, oraz
   `timestamp` jako ten sam odstęp, ale w sekundach — obok kolumny `time`
   z czasem narastającym. Interpretacja liczb wprost, bez rozpoznania
   jednostki, myli się o trzy rzędy wielkości i animacja leci 1000× za
   szybko albo za wolno. Dlatego oś czasu jest WYKRYWANA (_time_axis),
   a nie zakładana. To jedyne miejsce, w którym powstaje czas w sekundach
   i wszystko dalej liczy się z niego.

2. `rotw` bywa śmieciem (w jednym z plików ma stałą wartość 246), więc
   czwarta składowa kwaternionu jest LICZONA z pozostałych trzech —
   dokładnie tak, jak robi to Android dla TYPE_ROTATION_VECTOR:
   w = sqrt(1 - x² - y² - z²). Kolumna `rotw` jest brana tylko wtedy,
   gdy sama z siebie domyka kwaternion do długości 1.

3. Rotation vector przychodzi wolniej niż akcelerometr i jest w pliku
   POWTÓRZONY między aktualizacjami (w jednym pliku zmienia się co piąty
   wiersz). Animacja z takich danych chodzi skokowo, dlatego przetrzymane
   próbki są interpolowane (_smooth_held).

4. Pozycji żaden czujnik nie mierzy — powstaje z DWUKROTNEGO CAŁKOWANIA
   przyspieszenia, a każdy błąd narasta w niej kwadratowo. Stąd cały
   aparat w _positions: filtr górnoprzepustowy po każdym całkowaniu
   i zerowanie prędkości w chwilach bezruchu (ZUPT). To działa na
   ODCINKU DŁUGOŚCI UDERZENIA — i właśnie po to są segmenty. Puszczenie
   tego na piętnastominutowym nagraniu nie ma sensu i jest blokowane
   przez MAX_ROWS.

UKŁADY WSPÓŁRZĘDNYCH
--------------------
Kwaternion obraca wektor Z UKŁADU URZĄDZENIA DO UKŁADU ŚWIATA, gdzie
oś Z jest pionem (konwencja Androida). Sprawdzenie na danych z zegarka:
przyspieszenie obrócone do świata ma średnią [0, 0, 9.81] — czyli sama
grawitacja, tak jak być powinno.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd


class Motion3DError(Exception):
    """Dane albo zakres nie pozwalają nic policzyć. To jest komunikat DLA
    UŻYTKOWNIKA — widok zamienia go na 422, nie na 500."""


# ============================================================
#  STAŁE
# ============================================================

G = 9.80665

# Kolumny, których szukamy. Nazwy porównujemy po lowercase i bez spacji.
ACC = ("accx", "accy", "accz")
GYR = ("gyrx", "gyry", "gyrz")
ROT = ("rotx", "roty", "rotz")
LIN = ("linaccx", "linaccy", "linaccz")
TIME_COLUMNS = ("time", "timestamp")

# Górny limit wierszy w jednym oknie animacji. Nie chodzi o pamięć, tylko
# o sens: pozycja z podwójnego całkowania rozjeżdża się po kilkunastu
# sekundach, więc animowanie dziesięciu minut i tak dałoby bzdurę.
MAX_ROWS = 200_000

# Minimum, żeby cokolwiek dało się scałkować i przefiltrować.
MIN_ROWS = 8

# Sufit klatek na jedną odpowiedź. Powyżej tego progu wchodzi decymacja
# (co k-ta próbka), czyli JEDYNE miejsce, w którym z animacji wypadają
# całe pomiary. Dlatego próg jest wysoki: 6000 klatek to minuta zapisu
# przy 100 Hz i piętnaście sekund przy 400 Hz, a więc znacznie więcej,
# niż trwa jakiekolwiek pojedyncze uderzenie. Segment normalnej długości
# nie dociera tu nigdy i idzie co do próbki. Faktyczny krok wraca
# w `meta.stride` i interfejs go pokazuje.
MAX_FRAMES = 6000

# Górny limit klatek na sekundę, gdy wołający nic nie poda. Wyżej niż
# jakikolwiek realny zapis z zegarka, więc domyślnie NIC nie jest
# odrzucane — klatka odpowiada próbce jeden do jednego.
DEFAULT_FPS = 120.0

# Sensowny krok próbkowania w sekundach. Służy do rozpoznania jednostki
# osi czasu: dobra jednostka to ta, przy której typowy odstęp między
# próbkami wpada w ten przedział.
DT_MIN, DT_MAX = 1e-4, 1.0

# Dłuższa przerwa to luka w nagraniu (pauza, uśpiony czujnik), a nie
# próbka trwająca minutę. Skracamy ją, żeby animacja nie stała w miejscu,
# i meldujemy o tym w `meta`.
GAP_DT = 0.25

FALLBACK_FS = 100.0

# ---------- wykrywanie bezruchu dla ZUPT ----------
#
# ZUPT jest JEDYNYM miejscem w całej ścieżce, w którym z prędkości znika
# ruch, którego czujnik nie odróżnił od dryfu. Wszystko poniżej to jest
# odpowiedź na jedno pytanie: co konkretnie wolno uznać za bezruch.
#
# 1. AKTYWNOŚĆ, NIE WARTOŚĆ CHWILOWA. Poprzednia wersja pytała o samo
#    |a| w danej próbce, a to jest test, który W ŚRODKU KAŻDEGO GŁADKIEGO
#    RUCHU daje odpowiedź „stoi”: przyspieszenie przechodzi tam przez
#    zero — dokładnie wtedy, gdy prędkość jest NAJWIĘKSZA. Przy powolnym
#    przesunięciu przejście przez zero trwa kilkadziesiąt milisekund,
#    czyli dłużej niż wymagana seria, więc ZUPT wbijał zero prędkości
#    w szczyt ruchu i kasował z niego wszystko. Teraz liczy się wartość
#    SKUTECZNA w oknie STILL_WIN_S: w prawdziwym bezruchu jest mała,
#    a przy przejściu przez zero duża, bo tuż obok są oba szczyty.
#
# 2. PRÓG Z DANYCH, NIE Z TABLICY. Stała liczba musi być kompromisem
#    między czułym a zaszumionym czujnikiem i w obie strony jest zła:
#    za wysoka kasuje spokojne przymierzanie, za niska nie znajduje ani
#    jednego postoju. Bierzemy więc niski kwantyl aktywności w tym
#    właśnie zakresie i mnożymy przez zapas — próg sam schodzi na
#    zapisie cichym i sam rośnie na hałaśliwym.
STILL_QUANTILE = 0.10
STILL_MARGIN = 1.6

# Twarde widełki na próg wyliczony z danych. Sufity to dawne, hojne
# stałe — wyżej nie wchodzimy nigdy.
#
# Podłogi są bardzo nisko i to jest celowe: mają ratować wyłącznie
# przypadek zapisu idealnie gładkiego, w którym kwantyl wychodzi
# dokładnie zerowy i żadna próbka nie przeszłaby testu. Prawdziwy
# akcelerometr w zegarku szumi na poziomie setnych m/s², więc kwantyl
# jest tam o rząd–dwa wyższy od podłogi i podłoga nie ma nic do rzeczy.
# Gdyby postawić ją „na oko” wyżej, stałaby się ukrytym progiem
# kasującym najdrobniejsze ruchy na czystych zapisach.
STILL_ACC_MIN, STILL_ACC_MAX = 1e-3, 0.45     # m/s²
STILL_GYR_MIN, STILL_GYR_MAX = 5e-4, 0.35     # rad/s

# Gdy przy progu z danych nie ma ANI JEDNEGO postoju, próg rośnie
# potęgami STILL_RELAX aż do sufitu. Bez postoju pozycja odpływa
# liniowo i tor uderzenia robi się spiralą, więc warto spróbować —
# ale faktycznie użyty próg wraca w `meta.still_threshold`.
STILL_RELAX = 1.6
STILL_STEPS = 8

# Okno wartości skutecznej i minimalna długość serii — w SEKUNDACH,
# nie w próbkach. Dawne „pięć próbek” znaczyło 50 ms przy 100 Hz, ale
# już tylko 12 ms przy 400 Hz, czyli tyle, ile trwa jeden dołek szumu.
STILL_WIN_S = 0.10
MIN_STILL_S = 0.05
MIN_STILL_SAMPLES = 3

# Udział wierszy z NOWĄ wartością rotation vectora, powyżej którego
# uznajemy, że czujnik nadaje w pełnym tempie i nie ma czego
# interpolować. Patrz _smooth_held.
HELD_RATIO = 0.5

# Ile typowych okresów aktualizacji czujnika wolno przykryć jedną
# interpolacją. Dłuższa przerwa między zmianami wartości to nie jest
# przetrzymana próbka, tylko orientacja, która NAPRAWDĘ stała w miejscu.
HELD_SPAN = 2.0

NEUTRAL_COLOR = "#94a3b8"

# Bryła zegarka i osie urządzenia, jako ułamki skali sceny.
WATCH_FRACTION = 0.12
AXIS_FRACTION = 1.7
# Połowa przekątnej bryły z _watch_geometry: sqrt(0.45² + 0.60² + 0.16²).
WATCH_RADIUS = 0.77

# Podłoga boku sceny w centymetrach.
#
# Sześcian musi mieć JAKIŚ minimalny rozmiar, bo przy ręce stojącej
# w miejscu inaczej rozciąga sam szum na cały ekran. Ale poprzednie
# 6 cm było podłogą wyższą niż niejeden prawdziwy ruch nadgarstka:
# delikatne zagranie mieściło się w kilku pikselach pośrodku pustej
# sceny i nie było czego oglądać. 1 cm zostawia szum szumem, a ruch
# rzędu milimetrów robi widocznym — realną skalę i tak podają podziałki
# osi oraz `meta.span_cm`.
MIN_SPAN_CM = 1.0

# Ile klatek przerwy między sąsiednimi fazami traktujemy jako
# niedokładność zaznaczenia, a nie jako celową dziurę. Przy 100 Hz trzy
# klatki to 30 ms — poniżej progu, w którym ktokolwiek celowo zostawiłby
# odstęp, przeciągając myszą po wykresie.
MAX_PHASE_GAP = 3


# ============================================================
#  NARZĘDZIA LICZBOWE
# ============================================================

def _moving_average(x, win):
    """Średnia krocząca po osi 0, wyśrodkowana, bez przesuwania fazy.

    Liczona z sumy skumulowanej, więc koszt nie zależy od szerokości okna.
    Brzegi dopełniane odbiciem — dopełnienie krawędzią („edge”) zaniżałoby
    średnią na końcach i filtr górnoprzepustowy zostawiałby tam garb.
    """
    n = len(x)
    win = int(win)
    if win < 3 or n < 3:
        return np.zeros_like(x)

    win = min(win, 2 * n - 1)
    if win % 2 == 0:
        win += 1
    half = win // 2

    pad = min(half, n - 1)
    padded = np.pad(x, ((pad, pad), (0, 0)), mode="reflect")
    if pad < half:                      # okno szersze niż dane — dociągamy krawędzią
        padded = np.pad(padded, ((half - pad, half - pad), (0, 0)), mode="edge")

    c = np.cumsum(padded, axis=0)
    c = np.concatenate([np.zeros((1, x.shape[1])), c], axis=0)
    return (c[win:] - c[:-win]) / win


def _highpass(x, dt, fc):
    """Filtr górnoprzepustowy: sygnał minus jego wolna składowa.

    Zamiast filtru rekurencyjnego (który jest sekwencyjny i przesuwa fazę)
    odejmujemy średnią kroczącą o oknie 1/fc sekundy. Efekt jest ten sam —
    znika dryf i stała składowa — a operacja jest wektorowa i symetryczna
    w czasie, więc nie opóźnia ruchu względem oryginału. Przy animacji
    przesunięcie fazy widać od razu jako ruch „spóźniony” za wykresem.
    """
    if fc <= 0:
        return x - x.mean(axis=0)
    win = int(round(1.0 / (fc * dt)))
    if win < 3:
        return x - x.mean(axis=0)
    return x - _moving_average(x, win)


def _cumtrapz(y, dt):
    """Całka skumulowana metodą trapezów, zaczynając od zera."""
    out = np.zeros_like(y)
    np.cumsum(0.5 * (y[1:] + y[:-1]) * dt[:, None], axis=0, out=out[1:])
    return out


def _runs_at_least(mask, min_len, erode=0):
    """Zostawia w masce tylko serie długości co najmniej min_len.

    `erode` skraca każdą zachowaną serię o tyle próbek z obu stron.
    Maska bezruchu powstaje z wielkości liczonej w oknie, więc jest
    o pół okna ROZMYTA w obie strony i sięga w początek ruchu. Bez
    skrócenia ZUPT wbijałby zero prędkości w pierwsze próbki ruszania
    z miejsca — czyli dokładnie tam, gdzie zaczyna się to, co chcemy
    zobaczyć.
    """
    if not mask.any():
        return mask
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.nonzero(edges == 1)[0]
    ends = np.nonzero(edges == -1)[0]

    out = np.zeros_like(mask)
    for s, e in zip(starts, ends):
        if e - s < min_len:
            continue
        s, e = s + erode, e - erode
        if e > s:
            out[s:e] = True
    return out


# ---------- kwaterniony (w, x, y, z) ----------

def _quat_normalize(q):
    n = np.linalg.norm(q, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return q / n


def _quat_matrices(q):
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


def _to_world(R, v):
    return np.einsum("nij,nj->ni", R, v)


def _quat_align_signs(q):
    """Usuwa przeskoki znaku. q i -q to ten sam obrót, ale interpolacja
    między nimi przelatuje przez pół sfery — na animacji wygląda to jak
    gwałtowny obrót o 180°, którego w danych nie ma."""
    dots = np.sum(q[1:] * q[:-1], axis=1)
    signs = np.cumprod(np.where(dots < 0, -1.0, 1.0))
    q = q.copy()
    q[1:] *= signs[:, None]
    return q


def _quat_from_rotvec(rv, rw=None):
    """Kwaternion z trzech składowych ROTATION_VECTOR.

    Czwarta składowa jest liczona jako sqrt(1 - |v|²) — tak definiuje ją
    Android. Kolumna `rotw` bywa w plikach zapisana błędnie (stała wartość
    niebędąca żadnym kosinusem), więc jest brana pod uwagę tylko wtedy, gdy
    faktycznie domyka kwaternion do długości 1.
    """
    n2 = np.clip((rv * rv).sum(axis=1), 0.0, 1.0)
    w = np.sqrt(1.0 - n2)

    if rw is not None:
        domyka = np.abs(np.sqrt(n2 + rw * rw) - 1.0) < 0.05
        if np.mean(domyka) > 0.9:
            w = rw

    return _quat_align_signs(_quat_normalize(np.column_stack([w, rv])))


def _smooth_held(q, t):
    """Interpoluje kwaterniony powtórzone między aktualizacjami czujnika.

    Rotation vector w części plików aktualizuje się wolniej niż
    akcelerometr, a w CSV każdy wiersz ma jakąś wartość — po prostu tę
    samą, aż przyjdzie nowa. Bez interpolacji animacja co kilka klatek
    stoi i przeskakuje.

    UWAGA: to jedyne miejsce, w którym DOKŁADAMY ruch, którego czujnik nie
    zmierzył. Dlatego zwraca też informację, czy w ogóle coś zrobiło —
    interfejs ma o tym powiedzieć wprost, a `smooth=0` w zapytaniu wyłącza
    to całkowicie, jeśli ktoś woli zobaczyć surowe schodki.

    Interpolacja liniowa po składowych z ponowną normalizacją (nlerp) —
    przy kroku poniżej ~20° różni się od slerp o ułamek procenta, a jest
    w całości wektorowa.
    """
    zmiany = np.any(np.abs(np.diff(q, axis=0)) > 1e-9, axis=1)
    idx = np.concatenate([[0], np.nonzero(zmiany)[0] + 1])

    # Mniej niż trzy punkty — nie ma czego interpolować.
    #
    # Powyżej progu HELD_RATIO wierszy z nową wartością uznajemy, że
    # czujnik nadaje w pełnym tempie i każda klatka jest pomiarem.
    # Próg jest nisko (połowa), bo interpolacja ZASTĘPUJE zmierzone
    # wartości rampą — przy 80%, jak było wcześniej, wystarczyło, żeby
    # co piąta próbka powtórzyła się przez samo zaokrąglenie drobnego
    # ruchu, i cały zapis szedł przez wygładzanie, którego nie
    # potrzebował. Interpolujemy dopiero wtedy, gdy przetrzymywanie
    # próbek jest ewidentne, a nie „możliwe”.
    if len(idx) < 3 or len(idx) > HELD_RATIO * len(q):
        return q, False

    # Nie każda przerwa między zmianami jest przetrzymaną próbką.
    #
    # Gdy nadgarstek stoi, rotation vector NIE ZMIENIA SIĘ, bo nie ma
    # czego mierzyć — i wygląda to w pliku dokładnie tak samo jak
    # przetrzymanie. Interpolacja przez taką przerwę rozciąga początek
    # obrotu wstecz na całą poprzedzającą go chwilę bezruchu: animacja
    # pokazuje wtedy powolny obrót w momencie, w którym ręka jeszcze
    # stała. Dlatego mostkujemy tylko przerwy porównywalne z typowym
    # okresem aktualizacji; w dłuższych wstawiamy węzeł HELD_SPAN
    # okresów przed zmianą, więc wartość jest TRZYMANA aż do chwili,
    # w której czujnik mógł ją realnie zaktualizować.
    okres = max(1, int(np.median(np.diff(idx))))
    limit = max(2, int(round(okres * HELD_SPAN)))

    przytrzymania = [i1 - limit for i0, i1 in zip(idx[:-1], idx[1:])
                     if i1 - i0 > limit]
    if przytrzymania:
        idx = np.union1d(idx, np.asarray(przytrzymania, dtype=idx.dtype))

    # Po wstawieniu węzłów może się okazać, że nie ma już czego
    # interpolować — same sąsiadujące próbki. Wtedy oddajemy oryginał
    # i mówimy wprost, że nic nie dokładaliśmy.
    if not np.any(np.diff(idx) > 1):
        return q, False

    out = np.empty_like(q)
    for k in range(4):
        out[:, k] = np.interp(t, t[idx], q[idx, k])
    return _quat_normalize(out), True


def _integrate_gyro(gyr, dt, acc=None):
    """Orientacja z samego żyroskopu, z korektą pionu z akcelerometru.

    Ścieżka awaryjna dla plików bez rotation vectora. Całkowanie prędkości
    kątowej dryfuje wokół pionu (żyroskop nie wie, gdzie jest dół), więc
    po każdym kroku orientacja jest delikatnie ściągana tak, aby kierunek
    zmierzonej grawitacji zgadzał się z pionem świata. Kurs (obrót wokół
    pionu) zostaje bez odniesienia — magnetometr w hali bilardowej i tak
    kłamie, a do oglądania ruchu nadgarstka kurs nie jest potrzebny.
    """
    n = len(gyr)
    q = np.empty((n, 4))
    q[0] = (1.0, 0.0, 0.0, 0.0)

    korekta = 0.02
    for i in range(1, n):
        wx, wy, wz = gyr[i]
        krok = dt[i - 1]
        kat = math.sqrt(wx * wx + wy * wy + wz * wz) * krok

        w0, x0, y0, z0 = q[i - 1]
        if kat > 1e-9:
            s = math.sin(kat / 2) / (kat / krok)
            dw, dx, dy, dz = math.cos(kat / 2), wx * s, wy * s, wz * s
            # q_nowe = q_stare ⊗ dq  (prędkość kątowa jest w układzie ciała)
            w1 = w0 * dw - x0 * dx - y0 * dy - z0 * dz
            x1 = w0 * dx + x0 * dw + y0 * dz - z0 * dy
            y1 = w0 * dy - x0 * dz + y0 * dw + z0 * dx
            z1 = w0 * dz + x0 * dy - y0 * dx + z0 * dw
        else:
            w1, x1, y1, z1 = w0, x0, y0, z0

        if acc is not None:
            ax, ay, az = acc[i]
            dlugosc = math.sqrt(ax * ax + ay * ay + az * az)
            # Korygujemy tylko wtedy, gdy akcelerometr mierzy prawie samą
            # grawitację. W trakcie uderzenia mierzy głównie ruch i taka
            # „korekta” przewróciłaby orientację.
            if 0.85 * G < dlugosc < 1.15 * G:
                ax, ay, az = ax / dlugosc, ay / dlugosc, az / dlugosc
                # pion świata (0,0,1) przeniesiony do układu urządzenia
                gx = 2 * (x1 * z1 - w1 * y1)
                gy = 2 * (y1 * z1 + w1 * x1)
                gz = 1 - 2 * (x1 * x1 + y1 * y1)
                # Błąd = iloczyn wektorowy zmierzonego i przewidzianego
                # pionu. Wszystkie cztery składowe liczymy ze STAREGO
                # kwaternionu — podstawianie w trakcie mieszałoby dwa obroty.
                ex = ay * gz - az * gy
                ey = az * gx - ax * gz
                ez = ax * gy - ay * gx
                s = korekta * 0.5
                w1, x1, y1, z1 = (
                    w1 - s * (x1 * ex + y1 * ey + z1 * ez),
                    x1 + s * (w1 * ex + y1 * ez - z1 * ey),
                    y1 + s * (w1 * ey - x1 * ez + z1 * ex),
                    z1 + s * (w1 * ez + x1 * ey - y1 * ex),
                )

        norma = math.sqrt(w1 * w1 + x1 * x1 + y1 * y1 + z1 * z1) or 1.0
        q[i] = (w1 / norma, x1 / norma, y1 / norma, z1 / norma)

    return _quat_align_signs(q)


# ============================================================
#  OŚ CZASU
# ============================================================

_UNITS = (("s", 1.0), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9))


def _unit_for(krok):
    """Jednostka, przy której `krok` jest sensownym odstępem próbkowania."""
    if not math.isfinite(krok) or krok <= 0:
        return None, None
    for nazwa, skala in _UNITS:
        if DT_MIN <= krok * skala <= DT_MAX:
            return nazwa, skala
    return None, None


def _axis_from_column(raw, nazwa):
    """Zamienia surową kolumnę czasu na sekundy od początku nagrania.

    Kolumna bywa dwojaka i rozpoznajemy to po monotoniczności:
      • rosnąca  → czas bezwzględny, liczy się różnica względem pierwszej
                   próbki;
      • skacząca → ODSTĘP od poprzedniej próbki, liczy się suma.
    Jednostkę (s / ms / µs / ns) wybiera _unit_for po typowej wielkości.
    """
    d = np.diff(raw)
    if len(d) == 0:
        return None, None

    if np.mean(d > 0) > 0.95:
        jednostka, skala = _unit_for(float(np.median(d)))
        if jednostka:
            return (raw - raw[0]) * skala, f"{nazwa} narastający [{jednostka}]"

    jednostka, skala = _unit_for(float(np.median(np.abs(raw))))
    if jednostka:
        kroki = np.abs(raw) * skala
        # Pierwszy wiersz bywa czasem liczonym od startu urządzenia
        # (widzieliśmy tam 15 sekund przy próbkowaniu 10 ms) — dla nas
        # nagranie zaczyna się w zerze.
        kroki[0] = 0.0
        return np.cumsum(kroki), f"{nazwa} — odstęp próbek [{jednostka}]"

    return None, None


def _time_axis(df, cols, n):
    """(czas w sekundach, opis źródła, liczba luk)."""
    for klucz in TIME_COLUMNS:
        if klucz not in cols:
            continue
        raw = pd.to_numeric(df[cols[klucz]], errors="coerce").to_numpy(dtype=np.float64)
        if not np.isfinite(raw).all():
            raw = pd.Series(raw).ffill().bfill().to_numpy()
        if not np.isfinite(raw).all():
            continue

        t, opis = _axis_from_column(raw, klucz)
        if t is None:
            continue

        dt = np.diff(t)
        luki = int((dt > GAP_DT).sum())
        # Luka to pauza w nagraniu, a nie próbka trwająca minutę. Skracamy
        # ją do GAP_DT, żeby animacja nie stała, i mówimy o tym w meta.
        dt = np.clip(dt, DT_MIN, GAP_DT)
        return np.concatenate([[0.0], np.cumsum(dt)]), opis, luki

    return np.arange(n) / FALLBACK_FS, f"brak kolumny czasu — założono {FALLBACK_FS:g} Hz", 0


# ============================================================
#  WCZYTANIE PLIKU
# ============================================================

def supports(names):
    """Czy z takim kompletem kolumn da się zbudować animację 3D.

    Potrzebna jest oś czasu i cokolwiek, z czego wyjdzie orientacja.
    Sam akcelerometr nie wystarczy — bez orientacji nie da się oddzielić
    grawitacji od ruchu.
    """
    dostepne = {str(n).strip().lower() for n in names}
    ma_czas = any(k in dostepne for k in TIME_COLUMNS)
    ma_orientacje = all(k in dostepne for k in ROT) or all(k in dostepne for k in GYR)
    return ma_czas and ma_orientacje


def prepare(path: Path):
    """Wczytuje CSV i wystawia surowe serie w jednostkach SI.

    Świadomie NIE liczy tu orientacji ani pozycji: jedno i drugie zależy
    od wybranego zakresu (filtry i całkowanie liczą się od jego początku),
    a plik potrafi mieć kilkadziesiąt tysięcy wierszy, z których obejrzy
    się dwa uderzenia. Rachunki idą w build_motion, na wycinku.
    """
    df = pd.read_csv(path)
    cols = {str(c).strip().lower(): c for c in df.columns}
    n = len(df)

    def grupa(nazwy):
        if not all(k in cols for k in nazwy):
            return None
        blok = df[[cols[k] for k in nazwy]].apply(pd.to_numeric, errors="coerce")
        blok = blok.ffill().bfill()
        arr = blok.to_numpy(dtype=np.float64)
        if not np.isfinite(arr).all():
            return None
        return arr

    def kolumna(nazwa):
        if nazwa not in cols:
            return None
        arr = pd.to_numeric(df[cols[nazwa]], errors="coerce").ffill().bfill()
        arr = arr.to_numpy(dtype=np.float64)
        return arr if np.isfinite(arr).all() else None

    t, opis_czasu, luki = _time_axis(df, cols, n)

    return {
        "n": n,
        "t": t,
        "time_source": opis_czasu,
        "gaps": luki,
        "acc": grupa(ACC),
        "gyr": grupa(GYR),
        "rot": grupa(ROT),
        "lin": grupa(LIN),
        "rotw": kolumna("rotw"),
    }


def describe(prep):
    """Krótka charakterystyka pliku — do komunikatów i do testów."""
    n = prep["n"]
    t = prep["t"]
    czas = float(t[-1] - t[0]) if n > 1 else 0.0

    if prep["rot"] is not None:
        source = "fused" if prep["gyr"] is not None else "rot"
    elif prep["gyr"] is not None:
        source = "gyro"
    else:
        source = None

    return {
        "ok": source is not None and n >= MIN_ROWS,
        "source": source,
        "n": n,
        "duration": czas,
        "fs": (n - 1) / czas if czas > 0 else 0.0,
        "time_source": prep["time_source"],
        "gaps": prep["gaps"],
        "has_position": prep["acc"] is not None or prep["lin"] is not None,
    }


# ============================================================
#  POZYCJA
# ============================================================

def _activity(x, win):
    """Wartość skuteczna (RMS) długości wektora w oknie `win` próbek.

    To jest miara AKTYWNOŚCI, a nie chwilowej wartości: rośnie zarówno
    od stałego wychylenia, jak i od wahań wokół zera. Dzięki temu
    odróżnia prawdziwy bezruch od przejścia przyspieszenia przez zero
    w środku ruchu, gdzie chwilowa wartość też jest mała, ale tuż obok
    stoją oba szczyty.
    """
    kwadraty = (x * x).sum(axis=1)[:, None]
    return np.sqrt(np.maximum(_moving_average(kwadraty, max(3, win))[:, 0], 0.0))


def _still_threshold(akt, kwantyl_margines, lo, hi):
    """Próg bezruchu wyliczony z rozkładu aktywności w tym zakresie."""
    if len(akt) == 0:
        return lo
    return float(np.clip(np.quantile(akt, STILL_QUANTILE) * kwantyl_margines,
                         lo, hi))


def _zupt(v, a, gyr, win, min_len):
    """Zerowanie prędkości w chwilach bezruchu (Zero-velocity UPdaTe).

    Jeśli w oknie są momenty, w których ręka faktycznie stoi — a przy
    uderzeniu w bilardzie są, bo przymierzanie to seria zatrzymań — to
    prędkość policzona z całkowania musi w nich wynosić zero. Cokolwiek
    tam wyszło, jest dryfem. Interpolujemy ten dryf między kolejnymi
    zatrzymaniami i odejmujemy.

    To jest najskuteczniejszy pojedynczy zabieg na całej ścieżce: bez
    niego pozycja odpływa liniowo i tor uderzenia wygląda jak spirala.

    PRÓG DOBIERA SIĘ DO ZAPISU, A NIE ODWROTNIE
    -------------------------------------------
    Próg jest tu wprost pokrętłem „ile prawdziwego wolnego ruchu
    skasować”: wszystko, co pod niego wpadnie, dostaje prędkość zero,
    nawet jeśli ręka naprawdę się przesuwała. Dlatego nie jest stałą,
    tylko wynika z rozkładu aktywności w TYM zakresie — zaczyna od
    najcichszych dziesięciu procent i rozluźnia się dopiero wtedy, gdy
    przy takim progu nie ma ani jednego postoju.

    Zwraca (prędkość, liczba próbek bezruchu, użyte progi albo None).
    None znaczy: nie znaleziono bezruchu nawet przy najluźniejszym progu,
    prędkość wraca nietknięta i dryfem musi zająć się filtr.
    """
    akt_a = _activity(a, win)
    akt_g = _activity(gyr, win) if gyr is not None else None

    margines = STILL_MARGIN
    stoi = None
    wezly = np.empty(0, dtype=np.int64)
    prog_a = prog_g = 0.0

    for _ in range(STILL_STEPS):
        prog_a = _still_threshold(akt_a, margines, STILL_ACC_MIN, STILL_ACC_MAX)
        stoi = akt_a < prog_a
        if akt_g is not None:
            prog_g = _still_threshold(akt_g, margines, STILL_GYR_MIN, STILL_GYR_MAX)
            stoi = stoi & (akt_g < prog_g)

        stoi = _runs_at_least(stoi, min_len, erode=win // 2)
        wezly = np.nonzero(stoi)[0]
        if len(wezly) >= 2:
            break
        if prog_a >= STILL_ACC_MAX and (akt_g is None or prog_g >= STILL_GYR_MAX):
            break
        margines *= STILL_RELAX

    if len(wezly) < 2:
        return v, 0, None

    osie = np.arange(len(v), dtype=np.float64)
    dryf = np.empty_like(v)
    for k in range(3):
        dryf[:, k] = np.interp(osie, wezly, v[wezly, k])
    return v - dryf, int(stoi.sum()), (prog_a, prog_g if akt_g is not None else None)


def _positions(a_world, gyr, dt, hp_hz, zupt):
    """Przyspieszenie w układzie świata → prędkość → pozycja.

    Całkowanie zamienia każdą resztkową stałą składową w rampę: stały
    błąd przyspieszenia 0.05 m/s² (a tyle daje błąd orientacji rzędu
    0.3°) to po dwóch sekundach 10 cm odpłynięcia. Trzeba więc coś z tym
    zrobić — pytanie tylko, CZYM.

    DRYF ZDEJMUJE SIĘ RAZ, NIE TRZY RAZY
    ------------------------------------
    Wcześniej prędkość przechodziła i przez filtr górnoprzepustowy,
    i przez ZUPT, a potem to samo dostawała jeszcze pozycja. Te zabiegi
    nie sumują się w „lepiej”: ZUPT opiera się na chwilach, w których
    prędkość NAPRAWDĘ była zerowa, i po nim rampy już nie ma, więc drugi,
    ślepy filtr na tym samym sygnale zabierał tylko kawałek prawdziwego
    wolnego ruchu. Teraz:

      • ZUPT znalazł postoje → filtr na prędkości NIE wchodzi wcale,
        a ten na pozycji jest o oktawę łagodniejszy (zostaje jako
        zabezpieczenie przed resztką, nie jako główny mechanizm);
      • ZUPT nie znalazł nic → wracamy do ślepego detrendu na obu
        etapach, bo bez niego pozycja odpłynie. To jest gorsza droga
        i dlatego wraca w `meta` — użytkownik ma wiedzieć, że oglądał
        rekonstrukcję bez punktu zaczepienia.

    Filtr górnoprzepustowy jest tu ODEJMOWANIEM średniej kroczącej, więc
    NIE tłumi wysokich częstotliwości — drobny, szybki ruch przechodzi
    przez niego nietknięty. Ubywa wyłącznie tego, co wolniejsze od hp_hz.
    """
    krok = float(np.median(dt))

    def probki(sekundy, minimum):
        return max(minimum, int(round(sekundy / krok))) if krok > 0 else minimum

    a = _highpass(a_world, krok, hp_hz)
    v = _cumtrapz(a, dt)

    postoje, progi = 0, None
    if zupt:
        v, postoje, progi = _zupt(
            v, a, gyr,
            probki(STILL_WIN_S, MIN_STILL_SAMPLES),
            probki(MIN_STILL_S, MIN_STILL_SAMPLES),
        )

    if progi is None:
        v = _highpass(v, krok, hp_hz)
        hp_pos = hp_hz
    else:
        hp_pos = hp_hz * 0.5

    p = _cumtrapz(v, dt)
    p = _highpass(p, krok, hp_pos)

    return p, v, postoje, progi


# ============================================================
#  SCENA
# ============================================================

def _watch_geometry(rozmiar):
    """Bryła zegarka w układzie URZĄDZENIA plus trójkąty ścian.

    `rozmiar` jest w tych samych jednostkach, w których rysuje się scena
    (centymetry) — wierzchołki wychodzą stąd gotowe do dodania do pozycji.

    Prostopadłościan o proporcjach koperty zegarka: szerszy w osi Y
    (wzdłuż przedramienia), płaski w Z (od skóry w górę). Obracany jest
    w przeglądarce, bo obrót ośmiu wierzchołków to nic, a przesłanie ich
    dla każdej klatki byłoby kilkukrotnie większym JSON-em niż cała reszta.
    """
    hx, hy, hz = rozmiar * 0.45, rozmiar * 0.60, rozmiar * 0.16
    verts = [
        (-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
        (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz),
    ]
    faces = [
        (0, 1, 2), (0, 2, 3),      # spód
        (4, 6, 5), (4, 7, 6),      # wierzch (tarcza)
        (0, 5, 1), (0, 4, 5),      # bok -Y
        (3, 2, 6), (3, 6, 7),      # bok +Y
        (0, 3, 7), (0, 7, 4),      # bok -X
        (1, 5, 6), (1, 6, 2),      # bok +X
    ]
    return verts, faces


def _phase_spans(phases, t_probek, lo, hi, t_klatek):
    """Zakresy faz przeliczone z numerów wierszy CSV na numery klatek.

    Fazy w bazie są opisane numerami wierszy, bo taka jest oś X wykresu
    2D. Animacja ma własny, równomierny raster klatek, więc przejście
    prowadzi przez CZAS: numer wiersza → sekunda → numer klatki. Gdyby
    przeliczać wprost proporcją numerów, kolory rozjechałyby się wszędzie
    tam, gdzie próbkowanie nie jest idealnie równe — a nie jest.
    """
    out = []
    for faza in phases:
        klucz, etykieta, kolor, start, koniec = faza
        start = max(int(start), lo)
        koniec = min(int(koniec), hi)
        if koniec <= start:
            continue

        t0 = float(t_probek[start - lo])
        t1 = float(t_probek[min(koniec - lo, len(t_probek) - 1)])

        i0 = int(np.searchsorted(t_klatek, t0, side="left"))
        i1 = int(np.searchsorted(t_klatek, t1, side="right")) - 1
        i0 = max(0, min(i0, len(t_klatek) - 1))
        i1 = max(i0, min(i1, len(t_klatek) - 1))

        out.append({
            "phase": klucz,
            "label": etykieta,
            "color": kolor or NEUTRAL_COLOR,
            "i0": i0,
            "i1": i1,
            "t0": round(t0, 4),
            "t1": round(t1, 4),
        })

    out.sort(key=lambda s: s["i0"])

    # Domknięcie mikroszczelin między fazami.
    #
    # Fazy zaznacza się przeciągnięciem po wykresie, więc koniec jednej
    # i początek następnej rzadko wypadają na tym samym wierszu — zostaje
    # między nimi kilka próbek niczyich. Na wykresie 2D to niewidoczne,
    # ale w animacji podświetlenie gaśnie wtedy na jedną klatkę i wygląda
    # to jak usterka. Zszywamy przerwy do MAX_PHASE_GAP klatek; szersze
    # zostają, bo taka dziura to już świadoma decyzja, a nie niedokładność
    # przeciągnięcia myszą.
    for wczesniejszy, pozniejszy in zip(out, out[1:]):
        szczelina = pozniejszy["i0"] - wczesniejszy["i1"]
        if 1 < szczelina <= MAX_PHASE_GAP + 1:
            pozniejszy["i0"] = wczesniejszy["i1"] + 1


    return out


def _build_figure(pos_cm, zakres, watch_color="#1d4ed8"):
    """Scena Plotly: ślady statyczne (tor) plus ślady odświeżane co klatkę.

    CAŁY tor jest szary i cienki — to tło, kontekst całego zaznaczonego
    ruchu. Kolorem podświetla się wyłącznie faza, która akurat trwa, i to
    robi już przeglądarka na śladzie `phase`. Malowanie wszystkich faz
    naraz dawało tęczę, w której nie było widać, gdzie w tej chwili jest
    ręka — a o to w animacji chodzi.

    Kolejność śladów jest częścią kontraktu z motion3d.js — indeksy
    dynamicznych wracają w payload["dynamic"], żeby przeglądarka nie
    musiała ich zgadywać ani szukać po nazwie.
    """
    data = []

    # --- statyczne ---
    data.append({
        "type": "scatter3d", "mode": "lines", "name": "rzut na podłogę",
        "x": [round(v, 3) for v in pos_cm[:, 0]],
        "y": [round(v, 3) for v in pos_cm[:, 1]],
        "z": [round(zakres["z"][0], 3)] * len(pos_cm),
        "line": {"color": "rgba(148,163,184,0.30)", "width": 1},
        "hoverinfo": "skip", "showlegend": False,
    })

    data.append({
        "type": "scatter3d", "mode": "lines", "name": "tor ruchu",
        "x": [round(v, 3) for v in pos_cm[:, 0]],
        "y": [round(v, 3) for v in pos_cm[:, 1]],
        "z": [round(v, 3) for v in pos_cm[:, 2]],
        "line": {"color": NEUTRAL_COLOR, "width": 2},
        "hoverinfo": "skip", "showlegend": False,
    })

    dynamiczne = {}

    # Podświetlenie trwającej fazy — leży POD ogonem i bryłą, bo jest
    # tłem dla bieżącego ruchu, a nie jego wskaźnikiem.
    dynamiczne["phase"] = len(data)
    data.append({
        "type": "scatter3d", "mode": "lines", "name": "bieżąca faza",
        "x": [], "y": [], "z": [],
        "line": {"color": NEUTRAL_COLOR, "width": 6},
        "hoverinfo": "skip", "showlegend": False,
    })

    dynamiczne["trail"] = len(data)
    data.append({
        "type": "scatter3d", "mode": "lines", "name": "ostatnia chwila",
        "x": [], "y": [], "z": [],
        "line": {"color": "#0f172a", "width": 8},
        "hoverinfo": "skip", "showlegend": False,
    })

    dynamiczne["watch"] = len(data)
    data.append({
        "type": "mesh3d", "name": "zegarek",
        "x": [], "y": [], "z": [], "i": [], "j": [], "k": [],
        "color": watch_color, "opacity": 0.85, "flatshading": True,
        "hoverinfo": "skip", "showlegend": False,
    })

    dynamiczne["axes"] = []
    for kolor, nazwa in (("#ef4444", "oś X urządzenia"),
                         ("#22c55e", "oś Y urządzenia"),
                         ("#3b82f6", "oś Z urządzenia")):
        dynamiczne["axes"].append(len(data))
        data.append({
            "type": "scatter3d", "mode": "lines", "name": nazwa,
            "x": [], "y": [], "z": [],
            "line": {"color": kolor, "width": 6},
            "hoverinfo": "skip", "showlegend": False,
        })

    dynamiczne["marker"] = len(data)
    data.append({
        "type": "scatter3d", "mode": "markers", "name": "nadgarstek",
        "x": [], "y": [], "z": [],
        "marker": {"size": 6, "color": "#0f172a"},
        "hoverinfo": "skip", "showlegend": False,
    })

    def os(tytul, klucz):
        return {
            "title": {"text": tytul},
            "range": [round(zakres[klucz][0], 2), round(zakres[klucz][1], 2)],
            "backgroundcolor": "#f8fafc",
            "gridcolor": "#e2e8f0",
            "zerolinecolor": "#cbd5e1",
            "showspikes": False,
        }

    layout = {
        "template": "plotly_white",
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
        "showlegend": True,
        "legend": {"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0},
        "scene": {
            "xaxis": os("X [cm]", "x"),
            "yaxis": os("Y [cm]", "y"),
            "zaxis": os("Z — pion [cm]", "z"),
            # Równe zakresy osi + sześcian: centymetr w każdą stronę ma
            # na ekranie tę samą długość. Bez tego tor ruchu jest
            # rozciągnięty w osi, w której akurat było najmniej ruchu.
            "aspectmode": "cube",
            "camera": {"eye": {"x": 1.5, "y": -1.7, "z": 0.9}},
        },
        "uirevision": "motion3d",   # obrót sceny przeżywa podmianę danych
    }

    return {"data": data, "layout": layout}, dynamiczne


# ============================================================
#  GŁÓWNE WEJŚCIE
# ============================================================

def build_motion(prep, lo, hi, phases=(), fps=DEFAULT_FPS, hp_hz=0.35,
                 zupt=True, smooth=True, pos_scale=1.0, watch_scale=1.0):
    """Buduje scenę i klatki dla zakresu wierszy [lo, hi).

    KLATKA = PRAWDZIWA PRÓBKA Z PLIKU. Nie ma tu przepróbkowania na
    okrągły raster typu 60 kl/s — każda klatka to jeden wiersz CSV, ze
    swoim własnym, zmierzonym czasem. Czujnik nie próbkuje idealnie
    równo (widzieliśmy odstępy od 7,2 do 12,8 ms przy nominalnych 10 ms)
    i ta nierówność jest częścią tego, jak ruch naprawdę wyglądał.
    Wcześniejsza wersja interpolowała liniowo na równą siatkę 60 kl/s;
    na tych danych kosztowało to zerowo pod względem amplitudy, ale
    każda klatka była wtedy średnią ważoną dwóch pomiarów, a nie
    pomiarem. Teraz nie jest.

    `fps` nie jest już częstotliwością docelową, tylko GÓRNYM LIMITEM:
    gdy plik ma gęstsze próbkowanie, bierzemy co k-tą próbkę. Nadal
    prawdziwą — nigdy uśrednioną.

    Odtwarzacz w przeglądarce dobiera klatkę po czasie z zegara
    ściennego (wyszukiwanie binarne po `payload.t`), więc animacja idzie
    1:1 z czasem zapisanym w pliku niezależnie od tego, ile klatek zdąży
    narysować karta graficzna.
    """
    n = prep["n"]
    lo = max(0, int(lo))
    hi = min(int(hi), n)

    if hi - lo < MIN_ROWS:
        raise Motion3DError(
            f"Zakres jest za krótki — potrzeba co najmniej {MIN_ROWS} próbek.")
    if hi - lo > MAX_ROWS:
        raise Motion3DError(
            f"Zakres obejmuje {hi - lo:,} próbek. Animacja liczy się dla "
            f"pojedynczego uderzenia — zaznacz segment albo przybliż wykres "
            f"(limit {MAX_ROWS:,}).".replace(",", " "))

    wyc = slice(lo, hi)
    t = prep["t"][wyc] - prep["t"][lo]
    dt = np.diff(t)
    if not np.all(dt > 0):
        dt = np.clip(dt, DT_MIN, GAP_DT)
        t = np.concatenate([[0.0], np.cumsum(dt)])

    # ---------- orientacja ----------
    interpolowana = False
    if prep["rot"] is not None:
        rw = prep["rotw"][wyc] if prep["rotw"] is not None else None
        q = _quat_from_rotvec(prep["rot"][wyc], rw)
        if smooth:
            q, interpolowana = _smooth_held(q, t)
        source = "fused" if prep["gyr"] is not None else "rot"
        opis_orientacji = ("orientacja z rotation vectora, interpolowana "
                           "między aktualizacjami czujnika") if interpolowana \
            else "orientacja z rotation vectora (każda klatka to pomiar)"
    elif prep["gyr"] is not None:
        q = _integrate_gyro(prep["gyr"][wyc], dt,
                            prep["acc"][wyc] if prep["acc"] is not None else None)
        source = "gyro"
        opis_orientacji = "orientacja całkowana z żyroskopu"
    else:
        raise Motion3DError(
            "Plik nie zawiera ani rotation vectora (rot*), ani żyroskopu (gyr*) — "
            "nie ma z czego odtworzyć orientacji.")

    R = _quat_matrices(q)
    gyr = prep["gyr"][wyc] if prep["gyr"] is not None else None

    # ---------- przyspieszenie w układzie świata ----------
    kandydaci = []
    if prep["acc"] is not None:
        kandydaci.append(("acc* minus grawitacja",
                          _to_world(R, prep["acc"][wyc]) - np.array([0.0, 0.0, G])))
    if prep["lin"] is not None:
        kandydaci.append(("linacc*", _to_world(R, prep["lin"][wyc])))

    if kandydaci:
        # Ruch nadgarstka w oknie uderzenia zaczyna się i kończy w spoczynku,
        # więc jego przyspieszenie ma średnią bliską zeru. Ta średnia jest
        # więc miarą błędu — wybieramy serię, w której jest mniejsza. Na
        # danych z zegarka raz wygrywa acc*, raz linacc*, zależnie od tego,
        # jak plik był zapisany.
        opis_acc, a_world = min(
            kandydaci, key=lambda k: float(np.linalg.norm(k[1].mean(axis=0))))
        pozycja_znana = True
    else:
        opis_acc = "brak akcelerometru — sam obrót"
        a_world = np.zeros((len(t), 3))
        pozycja_znana = False

    # ---------- pozycja ----------
    pos, vel, postoje, progi_zupt = _positions(a_world, gyr, dt, hp_hz, zupt)

    # ---------- klatki = próbki z pliku ----------
    czas = float(t[-1])
    liczba_probek = len(t)
    fps_natywne = (liczba_probek - 1) / czas if czas > 0 else 0.0

    # Krok decymacji. Bierzemy co k-tą PRAWDZIWĄ próbkę — nigdy średnią
    # z sąsiednich. Limit z `fps` przydaje się przy plikach 400 Hz i wyżej,
    # gdzie i tak nie da się tego zobaczyć; MAX_FRAMES pilnuje rozmiaru
    # odpowiedzi.
    krok = 1
    if fps > 0 and fps_natywne > fps:
        # Margines na zaokrąglenie: przy zapisie dokładnie 400 Hz i limicie
        # 100 iloraz wychodzi 400.0000000001, a bez tego `ceil` robiłby
        # z tego krok 5 zamiast 4 i bez powodu wyrzucał co piątą próbkę.
        krok = max(1, int(math.ceil(fps_natywne / fps - 1e-9)))
    if math.ceil(liczba_probek / krok) > MAX_FRAMES:
        krok = int(math.ceil(liczba_probek / MAX_FRAMES))

    idx = np.arange(0, liczba_probek, krok)
    if idx[-1] != liczba_probek - 1:
        # Ostatnia próbka zawsze wchodzi — bez niej animacja kończyłaby się
        # przed końcem zaznaczonego zakresu.
        idx = np.append(idx, liczba_probek - 1)

    klatki = len(idx)
    tk = t[idx]
    pos_k = pos[idx]
    vel_k = vel[idx]
    q_k = q[idx]

    # Statystyki liczą się z metrów RZECZYWISTYCH, scena rysuje się
    # w centymetrach i dopiero tu wchodzi pos_scale — inaczej podkręcenie
    # skali dla czytelności zawyżałoby raportowaną drogę nadgarstka.
    droga = float(np.linalg.norm(np.diff(pos_k, axis=0), axis=1).sum())
    v_max = float(np.linalg.norm(vel_k, axis=1).max())

    pos_cm = pos_k * 100.0 * float(pos_scale)

    # ---------- zakres sceny ----------
    #
    # Bok sześcianu to rozpiętość ruchu POWIĘKSZONA dokładnie o tyle, ile
    # wystaje poza nadgarstek bryła zegarka razem z osiami urządzenia.
    # Plotly przycina wszystko, co wypada poza `range`, a osie sięgały
    # dalej niż margines — więc za każdym razem, gdy ręka dochodziła do
    # skraju swojego toru, osie chowały się pod ścianą sceny. Działo się
    # to w położeniach skrajnych, czyli tam, gdzie akurat najwięcej widać.
    srodek = (pos_cm.max(axis=0) + pos_cm.min(axis=0)) / 2.0
    rozpietosc = float((pos_cm.max(axis=0) - pos_cm.min(axis=0)).max())

    # Skala odniesienia dla bryły i osi. Przy ruchu drobniejszym niż
    # MIN_SPAN_CM bierze się z podłogi — inaczej zegarek kurczyłby się
    # razem z ruchem i nie byłoby po nim widać, jak jest obrócony.
    skala = max(rozpietosc, MIN_SPAN_CM)
    rozmiar_watch = skala * WATCH_FRACTION * float(watch_scale)
    dlugosc_osi = rozmiar_watch * AXIS_FRACTION
    margines = max(dlugosc_osi, rozmiar_watch * WATCH_RADIUS) * 1.08

    bok = max(rozpietosc + 2.0 * margines, MIN_SPAN_CM)
    zakres = {
        "x": (srodek[0] - bok / 2, srodek[0] + bok / 2),
        "y": (srodek[1] - bok / 2, srodek[1] + bok / 2),
        "z": (srodek[2] - bok / 2, srodek[2] + bok / 2),
    }

    # ---------- fazy ----------
    spans = _phase_spans(phases, t, lo, hi, tk)

    faza_klatki = np.full(klatki, -1, dtype=np.int64)
    for nr, span in enumerate(spans):
        faza_klatki[span["i0"]:span["i1"] + 1] = nr

    figure, dynamiczne = _build_figure(pos_cm, zakres)

    # ---------- bryła zegarka i osie ----------
    # `rozmiar_watch` policzył się wyżej, razem z marginesem sceny —
    # jedno zależy od drugiego i nie może się rozjechać. Wszystko jest
    # w CENTYMETRACH, nic tu już nie wraca na metry.
    verts, faces = _watch_geometry(rozmiar_watch)

    etykieta = f"{opis_orientacji}, pozycja z {opis_acc}"

    # Opis obróbki, która NAPRAWDĘ weszła na ten konkretny zapis. Nie
    # deklaracja z dokumentacji, tylko użyte progi — po to, żeby dało się
    # odróżnić „ruchu nie było” od „ruch wpadł pod próg i został zdjęty”.
    if not pozycja_znana:
        opis_filtrow = "brak akcelerometru — pozycji nie liczymy"
    elif not zupt:
        opis_filtrow = (f"ZUPT wyłączony w zapytaniu, "
                        f"detrend {hp_hz:g} Hz na prędkości i pozycji")
    elif progi_zupt is None:
        opis_filtrow = (f"detrend {hp_hz:g} Hz na prędkości i pozycji, "
                        f"bez ZUPT — w zakresie nie ma chwili bezruchu, "
                        f"o którą można zaczepić zero")
    else:
        progi_opis = f"|a| < {progi_zupt[0]:.3f} m/s²"
        if progi_zupt[1] is not None:
            progi_opis += f" i |ω| < {progi_zupt[1]:.3f} rad/s"
        opis_filtrow = (f"ZUPT przy {progi_opis} (aktywność w oknie "
                        f"{STILL_WIN_S * 1000:.0f} ms), "
                        f"detrend pozycji {hp_hz * 0.5:g} Hz")

    return {
        "figure": figure,
        "payload": {
            "t": [round(float(v), 4) for v in tk],
            # Cztery miejsca po przecinku w centymetrach to mikrometr.
            # Trzy (10 µm) wystarczały, dopóki najmniejsza scena miała
            # 6 cm; przy scenie centymetrowej byłaby to już jedna
            # tysięczna kadru, czyli widoczne schodki na drobnym ruchu.
            "pos": [[round(float(c), 4) for c in wiersz] for wiersz in pos_cm],
            "quat": [[round(float(c), 6) for c in wiersz] for wiersz in q_k],
            "speed": [round(float(v), 4) for v in np.linalg.norm(vel_k, axis=1)],
            "phase": faza_klatki.tolist(),
            "verts": [[round(c, 4) for c in v] for v in verts],
            "faces": {"i": [f[0] for f in faces],
                      "j": [f[1] for f in faces],
                      "k": [f[2] for f in faces]},
            "axis_len": round(dlugosc_osi, 4),
            "dynamic": dynamiczne,
            "spans": spans,
        },
        "meta": {
            "label": etykieta,
            "filters": opis_filtrow,
            "source": source,
            "frames": klatki,
            # Ostatnia klatka JEST ostatnią próbką zaznaczenia, więc czas
            # animacji i długość wycinka to teraz jedno i to samo.
            "duration": round(czas, 4),
            # Średnia liczba klatek na sekundę — informacyjnie, do podpisu.
            # Odtwarzacz jej NIE używa: klatki nie leżą w równym rastrze,
            # numer dobiera się szukaniem po `payload.t`.
            "fps": round((klatki - 1) / czas, 2) if czas > 0 else 0.0,
            "sample_rate": round(fps_natywne, 2),
            "stride": krok,
            "interpolated": interpolowana,
            "rows": hi - lo,
            "lo": lo,
            "hi": hi,
            "path_cm": round(droga * 100.0, 2),
            "v_max": round(v_max, 4),
            "has_position": pozycja_znana,
            "time_source": prep["time_source"],
            "gaps": prep["gaps"],
            "still_samples": postoje,
            # Bok sześcianu sceny. Bez tej liczby „mały ruch” i „duży
            # ruch” wyglądają na ekranie tak samo — scena skaluje się
            # do zawartości, więc dopiero ona mówi, co się właściwie
            # ogląda.
            "span_cm": round(bok, 3),
            "hp_hz": round(float(hp_hz), 4),
            # Progi, przy których faktycznie zadziałał ZUPT (None =
            # nie zadziałał wcale). Pokazujemy je, bo to jedyny zabieg
            # w całej ścieżce, który KASUJE zmierzony ruch.
            "still_threshold": ({"acc": round(progi_zupt[0], 5),
                                 "gyr": (round(progi_zupt[1], 5)
                                         if progi_zupt[1] is not None else None)}
                                if progi_zupt else None),
            "phases": spans,
        },
    }

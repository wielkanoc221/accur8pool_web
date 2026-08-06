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

# Sufit klatek na jedną odpowiedź. 3000 próbek to pół minuty zapisu
# przy 100 Hz — powyżej JSON zaczyna ważyć więcej, niż sama animacja
# jest warta. Powyżej tego progu wchodzi decymacja (co k-ta próbka).
MAX_FRAMES = 3000

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

# Próg „stoi w miejscu” dla ZUPT: przyspieszenie po odjęciu grawitacji
# i prędkość kątowa poniżej tych wartości przez co najmniej MIN_STILL
# próbek pod rząd.
STILL_ACC = 0.45      # m/s²
STILL_GYR = 0.35      # rad/s
MIN_STILL = 5

NEUTRAL_COLOR = "#94a3b8"

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


def _runs_at_least(mask, min_len):
    """Zostawia w masce tylko serie długości co najmniej min_len."""
    if not mask.any():
        return mask
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.nonzero(edges == 1)[0]
    ends = np.nonzero(edges == -1)[0]

    out = np.zeros_like(mask)
    for s, e in zip(starts, ends):
        if e - s >= min_len:
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

    # Mniej niż trzy punkty — nie ma czego interpolować. Więcej niż 80%
    # wierszy — czujnik nadaje w pełnym tempie i każda klatka jest
    # prawdziwym pomiarem; nie ma czego poprawiać ani co udawać.
    if len(idx) < 3 or len(idx) > 0.8 * len(q):
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

def _zupt(v, a, gyr):
    """Zerowanie prędkości w chwilach bezruchu (Zero-velocity UPdaTe).

    Jeśli w oknie są momenty, w których ręka faktycznie stoi — a przy
    uderzeniu w bilardzie są, bo przymierzanie to seria zatrzymań — to
    prędkość policzona z całkowania musi w nich wynosić zero. Cokolwiek
    tam wyszło, jest dryfem. Interpolujemy ten dryf między kolejnymi
    zatrzymaniami i odejmujemy.

    To jest najskuteczniejszy pojedynczy zabieg na całej ścieżce: bez
    niego pozycja odpływa liniowo i tor uderzenia wygląda jak spirala.
    """
    stoi = np.linalg.norm(a, axis=1) < STILL_ACC
    if gyr is not None:
        stoi &= np.linalg.norm(gyr, axis=1) < STILL_GYR
    stoi = _runs_at_least(stoi, MIN_STILL)

    wezly = np.nonzero(stoi)[0]
    if len(wezly) < 2:
        return v, 0

    osie = np.arange(len(v), dtype=np.float64)
    dryf = np.empty_like(v)
    for k in range(3):
        dryf[:, k] = np.interp(osie, wezly, v[wezly, k])
    return v - dryf, int(stoi.sum())


def _positions(a_world, gyr, dt, hp_hz, zupt):
    """Przyspieszenie w układzie świata → prędkość → pozycja.

    Po każdym całkowaniu wchodzi filtr górnoprzepustowy, bo każde
    całkowanie zamienia resztkową stałą składową w rampę: stały błąd
    przyspieszenia 0.05 m/s² (a tyle daje błąd orientacji rzędu 0.3°)
    to po dwóch sekundach 10 cm odpłynięcia.
    """
    krok = float(np.median(dt))

    a = _highpass(a_world, krok, hp_hz)
    v = _cumtrapz(a, dt)
    v = _highpass(v, krok, hp_hz)

    postoje = 0
    if zupt:
        v, postoje = _zupt(v, a, gyr)

    p = _cumtrapz(v, dt)
    p = _highpass(p, krok, hp_hz)

    return p, v, postoje


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
    pos, vel, postoje = _positions(a_world, gyr, dt, hp_hz, zupt)

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
    srodek = (pos_cm.max(axis=0) + pos_cm.min(axis=0)) / 2.0
    rozpietosc = float((pos_cm.max(axis=0) - pos_cm.min(axis=0)).max())
    # Minimalny bok sceny: przy prawie nieruchomej ręce sześcian o boku
    # 2 mm pokazywałby szum jako wielki ruch. 6 cm to skala nadgarstka.
    bok = max(rozpietosc * 1.35, 6.0)
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
    # `bok` jest bokiem sceny w CENTYMETRACH, więc bryła i osie też —
    # nic tu już nie przelicza się na metry i z powrotem.
    rozmiar_watch = bok * 0.16 * float(watch_scale)
    verts, faces = _watch_geometry(rozmiar_watch)

    etykieta = f"{opis_orientacji}, pozycja z {opis_acc}"

    return {
        "figure": figure,
        "payload": {
            "t": [round(float(v), 4) for v in tk],
            "pos": [[round(float(c), 3) for c in wiersz] for wiersz in pos_cm],
            "quat": [[round(float(c), 5) for c in wiersz] for wiersz in q_k],
            "speed": [round(float(v), 4) for v in np.linalg.norm(vel_k, axis=1)],
            "phase": faza_klatki.tolist(),
            "verts": [[round(c, 3) for c in v] for v in verts],
            "faces": {"i": [f[0] for f in faces],
                      "j": [f[1] for f in faces],
                      "k": [f[2] for f in faces]},
            "axis_len": round(rozmiar_watch * 1.7, 3),
            "dynamic": dynamiczne,
            "spans": spans,
        },
        "meta": {
            "label": etykieta,
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
            "phases": spans,
        },
    }

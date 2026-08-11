"""ACCUR8POOL — rekonstrukcja ruchu 3D z surowego zapisu IMU.

Moduł jest CZYSTO LICZBOWY: dostaje ścieżkę do CSV i zakres wierszy,
oddaje gotową scenę Plotly plus klatki animacji. Nie wie nic o Django,
o segmentach w bazie ani o tym, kto na to patrzy — dzięki temu daje się
testować na danych syntetycznych o znanej kinematyce.

SKĄD BIERZE SIĘ RUCH
--------------------
Zegarek mierzy DWIE rzeczy naprawdę: orientację (rotation vector albo
żyroskop) i przyspieszenie. Pozycji nie mierzy nikt.

Poprzednia wersja robiła pozycję z DWUKROTNEGO CAŁKOWANIA przyspieszenia
i cała reszta pliku była walką ze skutkami tej decyzji: filtry
górnoprzepustowe, wykrywanie bezruchu, progi dobierane z kwantyli,
zerowanie prędkości (ZUPT). Nie da się tego wygrać na odcinku jednego
uderzenia. Błąd orientacji rzędu 0.3° zostawia w przyspieszeniu stałą
0.05 m/s², a to po dwóch sekundach 10 cm odpłynięcia — więc tor uderzenia
wychodził spiralą. Filtr, który by to skasował, musi mieć częstotliwość
graniczną rzędu 0.3 Hz, czyli akurat tam, gdzie leży samo uderzenie:
lekarstwo zjadało pacjenta. Stąd animacja, która „słabo wygląda”.

Tutaj pozycja bierze się z KINEMATYKI, a nie z całkowania. Uderzenie
w bilardzie to ruch wahadłowy: łokieć stoi, przedramię się kołysze,
nadgarstek jedzie po łuku. Zegarek jest więc na końcu sztywnej dźwigni
obracającej się wokół nieruchomego punktu:

    p(t) = R(t) · d

gdzie R(t) to ZMIERZONA orientacja, a d — stały wektor od osi obrotu do
zegarka, wyrażony w układzie urządzenia. Nieznana jest jedna trójka
liczb na cały segment, a nie trajektoria w każdej próbce. Wektor d liczy
się metodą najmniejszych kwadratów z przyspieszenia, bo dla takiej
dźwigni

    a(t) = R(t) · ( [ω̇]ₓ + [ω]ₓ[ω]ₓ ) · d

jest LINIOWE względem d (patrz _lever_fit). Trzy niewiadome na kilkaset
równań — problem jest przeskalowany tysiąckrotnie, więc szum się uśrednia
zamiast narastać. Pozycja nie dryfuje, bo nigdzie nie ma całkowania,
a tor jest gładki, bo powstaje wprost z orientacji.

Za to model NIE POKAŻE czystego przesunięcia bez obrotu — takiego ruchu
nie ma z czego odtworzyć. Jakość dopasowania (ile procent zmierzonego
przyspieszenia tłumaczy dźwignia) wraca w `meta.lever_fit` i interfejs
ma ją pokazać, żeby dało się odróżnić rekonstrukcję od zgadywanki.

CO JESZCZE JEST TRUDNE W TYCH DANYCH
------------------------------------
1. Oś czasu ma w każdym pokoleniu pliku inne znaczenie. Widzieliśmy trzy:
   `timestamp` jako bezwzględne nanosekundy (elapsedRealtimeNanos),
   `timestamp` jako ODSTĘP od poprzedniej próbki w milisekundach, oraz
   ten sam odstęp w sekundach — obok kolumny `time` z czasem narastającym.
   Interpretacja wprost, bez rozpoznania jednostki, myli się o trzy rzędy
   wielkości i animacja leci 1000× za szybko albo za wolno. Dlatego oś
   czasu jest WYKRYWANA (_time_axis), a nie zakładana.

2. `rotw` bywa śmieciem (w jednym z plików ma stałą wartość 246), więc
   czwarta składowa kwaternionu bywa odtwarzana z pozostałych trzech.
   Androidowe w = sqrt(1 - x² - y² - z²) jest jednak prawdziwe TYLKO do
   180° obrotu — powyżej daje obrót odwrotny, a granicę widać w danych
   z zegarka regularnie. Znak w odtwarza więc _quat_w z kinematyki,
   a gałąź rozwiązania wybiera pion z akcelerometru.

3. Rotation vector przychodzi wolniej niż akcelerometr i jest w pliku
   POWTÓRZONY między aktualizacjami. Animacja z takich danych chodzi
   skokowo, dlatego przetrzymane próbki są wygładzane (_smooth_quat).

4. Wektora obrotu NIE WOLNO filtrować składowa po składowej — jego
   składowe wiąże warunek |q| = 1, o którym filtr nie wie. Przez pewien
   czas robiło to przygotowanie danych (rotx/roty/rotz przez filtr
   dolnoprzepustowy, rotw nietknięte) i dlatego animacja musiała czytać
   plik surowy. Teraz rot* przechodzą przez transform_raw_df bez zmian,
   więc wersja przygotowana jest jedynym źródłem dla całej aplikacji —
   szczegóły przy COLUMNS_TO_FILTER_5_CUT_OFF w prepare_raw_data.
   Gdyby mimo to trafił się zapis z zepsutą orientacją, wyłapuje go
   porównanie z żyroskopem w build_motion i animacja przechodzi na
   całkowanie żyroskopu.

UKŁADY WSPÓŁRZĘDNYCH
--------------------
Kwaternion obraca wektor Z UKŁADU URZĄDZENIA DO UKŁADU ŚWIATA, gdzie
oś Z jest pionem (konwencja Androida). Sprawdzenie na danych z zegarka:
przyspieszenie obrócone do świata ma średnią [0, 0, 9.81] — czyli samą
grawitację, tak jak być powinno.
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

# Minimum, żeby cokolwiek dało się policzyć i zróżniczkować.
MIN_ROWS = 8

# Górny limit wierszy w jednym oknie animacji. Nie chodzi o pamięć, tylko
# o sens: model dźwigni opisuje POJEDYNCZY ruch, a nie kwadrans nagrania,
# w którym łokieć zdążył zmienić położenie kilkaset razy.
MAX_ROWS = 200_000

# Sufit klatek na jedną odpowiedź. Powyżej wchodzi decymacja (co k-ta
# próbka) — jedyne miejsce, w którym z animacji wypadają całe pomiary.
# Segment normalnej długości nie dociera tu nigdy: 4000 klatek to
# czterdzieści sekund zapisu przy 100 Hz.
MAX_FRAMES = 4000

# Brak limitu klatek, gdy wołający nic nie poda: klatka = próbka z pliku.
NO_FPS_LIMIT = 0.0

# Sensowny krok próbkowania w sekundach. Służy do rozpoznania jednostki
# osi czasu: dobra jednostka to ta, przy której typowy odstęp między
# próbkami wpada w ten przedział.
DT_MIN, DT_MAX = 1e-4, 1.0

# Dłuższa przerwa to luka w nagraniu (pauza, uśpiony czujnik), a nie
# próbka trwająca minutę. Skracamy ją, żeby animacja nie stała w miejscu,
# i meldujemy o tym w `meta`.
GAP_DT = 0.25

FALLBACK_FS = 100.0

# ---------- model dźwigni ----------
#
# Okno wygładzania prędkości kątowej i przyspieszenia przed dopasowaniem.
# Dźwignię wyznacza się z DRUGIEJ pochodnej ruchu, więc wchodzi tu szum
# czujnika pomnożony przez kwadrat częstotliwości. 30 ms to kompromis:
# przepuszcza wszystko, co w uderzeniu istotne (poniżej ~15 Hz), a ucina
# to, co i tak jest szumem kwantyzacji.
SMOOTH_S = 0.03

# Sufit na długość dźwigni. Powyżej metra nie ma już mowy o ruchu ręki —
# taki wynik świadczy o tym, że dopasowanie poszło w szum, a nie o długim
# ramieniu.
#
# Podłogi CELOWO NIE MA. Krótkie ramię to nie jest błąd, tylko obrót
# nadgarstka wokół siebie samego — ruch drobny, ale prawdziwy. Podłoga
# rozdmuchiwałaby go do swojej wysokości i zawyżała `path_cm`, czyli
# kłamała w jedynej liczbie, którą użytkownik odczytuje wprost. Fit,
# z którego nic nie wyszło, wychwytuje test na zero kilka linijek niżej.
LEVER_MAX = 0.90

# Regularyzacja Tichonowa jako ułamek śladu macierzy normalnej. Ratuje
# przypadek obrotu wokół jednej osi, w którym jeden kierunek d nie ma
# w danych żadnego pokrycia i bez tego wyszedłby z dzielenia przez zero.
LEVER_RIDGE = 1e-3

# Ile razy prędkość kątowa policzona z rotation vectora może rozminąć się
# z żyroskopem, zanim uznamy rotation vector za niezdatny i przejdziemy na
# całkowanie żyroskopu. 1.0 znaczy „błąd wielkości samego sygnału”, czyli
# przebieg, w którym nie ma już informacji — patrz build_motion.
ROT_GYRO_MAX = 1.0

# Poniżej tego dopasowania mówimy wprost, że dźwignia tłumaczy zmierzone
# przyspieszenie słabo — ruch miał zapewne dużą składową przesunięcia,
# której z obrotu nie da się odtworzyć.
LEVER_FIT_WARN = 0.35

# ---------- scena ----------

NEUTRAL_COLOR = "#94a3b8"

# Bryła zegarka i osie urządzenia, jako ułamki boku sceny.
WATCH_FRACTION = 0.13
AXIS_FRACTION = 1.7
# Połowa przekątnej bryły z _watch_geometry: sqrt(0.45² + 0.60² + 0.16²).
WATCH_RADIUS = 0.77

# Podłoga boku sceny w centymetrach. Sześcian musi mieć jakiś minimalny
# rozmiar, bo przy ręce stojącej w miejscu inaczej rozciąga sam szum na
# cały ekran.
MIN_SPAN_CM = 1.0

# Ile klatek przerwy między sąsiednimi fazami traktujemy jako
# niedokładność zaznaczenia, a nie jako celową dziurę. Przy 100 Hz trzy
# klatki to 30 ms — poniżej progu, w którym ktokolwiek celowo zostawiłby
# odstęp, przeciągając myszą po wykresie.
MAX_PHASE_GAP = 3


# ============================================================
#  NARZĘDZIA LICZBOWE
# ============================================================

def _boxcar(x, win):
    """Średnia krocząca po osi 0, wyśrodkowana, bez przesuwania fazy.

    Liczona z sumy skumulowanej, więc koszt nie zależy od szerokości okna.
    Brzegi dopełniane odbiciem — dopełnienie krawędzią zaniżałoby średnią
    na końcach. Symetria w czasie jest tu istotna: filtr przesuwający fazę
    widać w animacji od razu jako ruch spóźniony za wykresem.
    """
    n = len(x)
    win = int(win)
    if win < 3 or n < 3:
        return x.copy()

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


def _derivative(x, t):
    """Pochodna po czasie na NIERÓWNEJ siatce próbek.

    Czujnik nie próbkuje równo (widzieliśmy odstępy od 7.2 do 12.8 ms przy
    nominalnych 10 ms), a np.gradient przyjmuje oś czasu wprost, więc
    nierówność nie zamienia się w fałszywe skoki pochodnej.
    """
    return np.gradient(x, t, axis=0, edge_order=2)


def _skew(v):
    """Macierze [v]ₓ dla całej serii wektorów: [v]ₓ·u = v × u."""
    S = np.zeros((len(v), 3, 3))
    S[:, 0, 1] = -v[:, 2]
    S[:, 0, 2] = v[:, 1]
    S[:, 1, 0] = v[:, 2]
    S[:, 1, 2] = -v[:, 0]
    S[:, 2, 0] = -v[:, 1]
    S[:, 2, 1] = v[:, 0]
    return S


# ============================================================
#  KWATERNIONY (w, x, y, z)
# ============================================================

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
    """Usuwa przeskoki znaku. q i -q to ten sam obrót, ale różniczkowanie
    i wygładzanie między nimi przelatuje przez pół sfery — na animacji
    wygląda to jak obrót o 180°, którego w danych nie ma."""
    dots = np.sum(q[1:] * q[:-1], axis=1)
    signs = np.cumprod(np.where(dots < 0, -1.0, 1.0))
    q = q.copy()
    q[1:] *= signs[:, None]
    return q


def _quat_w(rv, absw, dt, gyr, znak0):
    """Czwarta składowa kwaternionu RAZEM ZE ZNAKIEM.

    ROTATION_VECTOR niesie tylko trzy składowe, a Android liczy czwartą
    jako w = +sqrt(1 - |v|²). To jest prawdą TYLKO dla obrotów do 180°:
    w = cos(θ/2), więc powyżej tego kąta prawdziwe w jest UJEMNE, a
    dodatni pierwiastek daje obrót ODWROTNY do rzeczywistego.

    I nie jest to przypadek egzotyczny. Kwaternion opisuje obrót
    urządzenie → świat, a świat to układ ENU — nadgarstek nad stołem
    bywa względem niego obrócony o więcej niż 180° i przekracza tę
    granicę w ŚRODKU ruchu. Orientacja przeskakuje wtedy na odwrotną
    i z powrotem, czyli zegarek na animacji wariuje — tym częściej, im
    szybszy ruch, bo tym więcej razy granica zostaje przekroczona.

    ZNAKU NIE DA SIĘ WYBRAĆ PO SĄSIEDZTWIE
    --------------------------------------
    Kuszące jest wziąć tego z dwóch kandydatów (±|w|, v), który leży
    bliżej poprzedniego obrotu. Tyle że dokładnie w punkcie przejścia
    |w| = 0 i OBAJ kandydaci są tam identyczni — różnica między nimi
    jest rzędu |w|, czyli znika w tym samym miejscu, w którym trzeba
    podjąć decyzję. Test bliskości nie przełącza więc znaku nigdy
    i przejście przez 180° zostaje niezauważone.

    Znak trzeba PRZEWIDZIEĆ, a nie wybrać. Z kinematyki kwaternionu

        ẇ = -½ · v · ω

    czyli żyroskop mówi wprost, w którą stronę w zmierza — także wtedy,
    gdy właśnie przechodzi przez zero. Wystarczy jeden krok Eulera od
    poprzedniej, już ustalonej wartości: wielkość |w| bierzemy z danych,
    a z przewidywania tylko ZNAK, więc nic się tu nie całkuje i nic nie
    dryfuje. Bez żyroskopu zostaje ekstrapolacja liniowa po dwóch
    poprzednich próbkach, która przez zero przechodzi tak samo.

    ZNAK PIERWSZEJ PRÓBKI JEST PARAMETREM, NIE ZAŁOŻENIEM
    -----------------------------------------------------
    Śledzenie jest poprawne tylko wtedy, gdy startuje z dobrego znaku.
    Przy złym starcie nachylenie z żyroskopu jest nadal prawdziwe, ale
    odnosi się do drugiej gałęzi rozwiązania — przejścia przez zero
    wypadają wtedy w złych miejscach i seria wychodzi POMIESZANA, a nie
    po prostu odwrócona. Takiego wyniku nie da się już naprawić żadnym
    globalnym odwróceniem. Dlatego `znak0` wchodzi tu z zewnątrz:
    _quat_from_rotvec liczy obie gałęzie i wybiera po pionie.
    """
    n = len(rv)
    w = np.empty(n)
    w[0] = znak0 * absw[0]

    for i in range(1, n):
        if gyr is not None:
            pred = w[i - 1] - 0.5 * float(rv[i - 1] @ gyr[i - 1]) * dt[i - 1]
        elif i >= 2:
            pred = 2.0 * w[i - 1] - w[i - 2]
        else:
            pred = w[i - 1]
        w[i] = absw[i] if pred >= 0.0 else -absw[i]

    return w


def _pion_w_swiecie(q, acc):
    """Średnia pionowa składowa przyspieszenia obróconego do świata.

    Miara poprawności orientacji. Przy dobrej orientacji przyspieszenie
    obrócone do układu świata to średnio sama grawitacja skierowana
    w GÓRĘ, czyli [0, 0, +G] — ruch nadgarstka w oknie uderzenia zaczyna
    się i kończy w spoczynku, więc jego własne przyspieszenie ma średnią
    bliską zeru i zostaje tylko grawitacja. Przy orientacji błędnej nie
    ma powodu, żeby akurat tak wyszło.
    """
    R = _quat_matrices(_quat_normalize(q))
    return float(np.einsum("nj,nj->n", R[:, 2, :], acc).mean())


def _quat_from_rotvec(rv, dt, rw=None, acc=None, gyr=None):
    """Kwaternion z ROTATION_VECTOR, razem ze znakiem czwartej składowej.

    Kolumna `rotw` niesie ten znak wprost, więc gdy jest wiarygodna,
    wygrywa ze wszystkim. Bywa jednak zapisana błędnie (w jednym z plików
    ma stałą wartość 246). Dlatego bierzemy ją tylko wtedy, gdy
    faktycznie domyka kwaternion do długości 1 — ten sam test wyłapuje
    też rot* zniekształcone gdziekolwiek po drodze.

    Bez wiarygodnego rotw znak czwartej składowej odtwarza _quat_w, ale
    ten potrzebuje znaku PIERWSZEJ próbki, którego z samych rot* nie da
    się odczytać. Liczymy więc obie gałęzie rozwiązania i wybieramy tę,
    w której grawitacja wychodzi w górę (_pion_w_swiecie). Wyboru nie da
    się odłożyć na potem: gałęzie różnią się nie tylko globalnym znakiem,
    ale i miejscami przejść przez zero.

    Zwraca (kwaterniony, opis pochodzenia czwartej składowej).
    """
    n2 = (rv * rv).sum(axis=1)

    # |v| > 1 jest fizycznie niemożliwe (|v| = |sin(θ/2)|), więc taka
    # próbka znaczy, że wektor obrotu został po drodze zniekształcony —
    # np. przez filtr dolnoprzepustowy, który przestrzeliwuje na zboczu.
    # Przycięcie samego n2 dawałoby w tych miejscach w = 0, czyli obrót
    # o równe 180° wzięty znikąd; skalujemy więc CAŁY wektor z powrotem
    # na sferę, co zachowuje przynajmniej oś obrotu.
    zepsute = n2 > 1.0
    if zepsute.any():
        rv = rv.copy()
        rv[zepsute] /= np.sqrt(n2[zepsute])[:, None]
        n2 = np.minimum(n2, 1.0)

    absw = np.sqrt(np.maximum(1.0 - n2, 0.0))

    if rw is not None and np.mean(np.abs(np.sqrt(n2 + rw * rw) - 1.0) < 0.05) > 0.9:
        q = np.column_stack([rw, rv])
        opis = "rotw z pliku"
    else:
        galezie = [np.column_stack([_quat_w(rv, absw, dt, gyr, s), rv])
                   for s in (1.0, -1.0)]
        if acc is None:
            # Nie ma czym rozstrzygnąć — zostaje założenie Androida (w > 0).
            q = galezie[0]
            opis = "znak w z kinematyki, bez potwierdzenia pionem"
        else:
            q = max(galezie, key=lambda kandydat: _pion_w_swiecie(kandydat, acc))
            opis = "znak w z kinematyki, gałąź wybrana wg pionu"

    if zepsute.any():
        opis += f", {int(zepsute.sum())} próbek poza sferą jednostkową"

    return _quat_align_signs(_quat_normalize(q)), opis


def _smooth_quat(q):
    """Wygładza schodki rotation vectora przetrzymywanego między pomiarami.

    W części plików rotation vector aktualizuje się wolniej niż
    akcelerometr, a w CSV każdy wiersz i tak ma jakąś wartość — po prostu
    tę samą, aż przyjdzie nowa. Bez wygładzenia animacja co kilka klatek
    stoi i przeskakuje, a prędkość kątowa liczona z takiej serii to grzebień
    igieł zamiast gładkiego przebiegu.

    Zabieg to zwykła średnia krocząca o oknie równym okresowi aktualizacji
    czujnika. Na schodkach o takim właśnie kroku daje dokładnie rampę
    liniową między pomiarami, czyli to samo, co interpolacja — tyle że bez
    szukania węzłów i bez ryzyka rozciągnięcia jednej zmiany na całą
    poprzedzającą ją chwilę bezruchu. Tam, gdzie wartość stoi naprawdę,
    średnia ze stałej jest tą samą stałą.

    Zwraca (kwaterniony, czy cokolwiek zrobiono) — interfejs ma powiedzieć
    wprost, kiedy ogląda się wygładzenie, a kiedy surowy pomiar.
    """
    zmiany = np.nonzero(np.any(np.abs(np.diff(q, axis=0)) > 1e-9, axis=1))[0]
    if len(zmiany) < 2:
        return q, False

    okres = int(round(float(np.median(np.diff(zmiany)))))
    if okres < 2:
        return q, False             # czujnik nadaje w pełnym tempie

    return _quat_normalize(_boxcar(q, max(3, okres))), True


def _quat_angular_velocity(q, t):
    """Prędkość kątowa w układzie URZĄDZENIA, wyliczona z serii kwaternionów.

    ω = 2 · część_wektorowa(q⁻¹ ⊗ q̇). Ścieżka dla plików bez żyroskopu —
    gdy jest, bierzemy jego pomiar, bo jest gęstszy i mniej zaszumiony niż
    pochodna orientacji.
    """
    dq = _derivative(q, t)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    dw, dx, dy, dz = dq[:, 0], dq[:, 1], dq[:, 2], dq[:, 3]
    return 2.0 * np.column_stack([
        w * dx - x * dw - y * dz + z * dy,
        w * dy + x * dz - y * dw - z * dx,
        w * dz - x * dy + y * dx - z * dw,
    ])


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
    od wybranego zakresu (dźwignia dopasowuje się do konkretnego ruchu),
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
#  POZYCJA — MODEL DŹWIGNI
# ============================================================

def _lever_fit(R, omega, domega, a_world):
    """Wektor od osi obrotu do zegarka, wyznaczony z przyspieszenia.

    Dla punktu sztywno związanego z obracającym się ciałem, w odległości
    d od nieruchomej osi obrotu (d w układzie ciała):

        p = R·d
        v = ṗ = R·[ω]ₓ·d
        a = v̇ = R·( [ω̇]ₓ + [ω]ₓ[ω]ₓ )·d

    Pierwszy człon to przyspieszenie styczne, drugi dośrodkowe. Całość
    jest LINIOWA względem d, więc M(t)·d = a(t) to zwykły przesztywniony
    układ równań: trzy niewiadome na 3·N równań. Rozwiązanie metodą
    najmniejszych kwadratów uśrednia szum zamiast go całkować — i to jest
    cała różnica względem podwójnego całkowania przyspieszenia.

    Zwraca (d, jakość), gdzie jakość ∈ [0, 1] to udział zmierzonego
    przyspieszenia wytłumaczony przez tę dźwignię. Blisko jedynki: ruch
    był obrotem wokół w miarę nieruchomego punktu, czyli dokładnie tym,
    czego oczekujemy po uderzeniu. Blisko zera: dominowało przesunięcie
    całego ramienia, którego z samego obrotu nie da się odtworzyć.
    """
    W = _skew(omega)
    M = R @ (_skew(domega) + W @ W)

    A = np.einsum("nij,nik->jk", M, M)
    b = np.einsum("nij,ni->j", M, a_world)

    slad = float(np.trace(A))
    if not math.isfinite(slad) or slad <= 0:
        return np.zeros(3), 0.0

    try:
        d = np.linalg.solve(A + (LEVER_RIDGE * slad / 3.0) * np.eye(3), b)
    except np.linalg.LinAlgError:
        return np.zeros(3), 0.0

    dlugosc = float(np.linalg.norm(d))
    if not math.isfinite(dlugosc) or dlugosc < 1e-9:
        return np.zeros(3), 0.0

    # Absurdalnie długie ramię znaczy, że dopasowanie poszło w szum, ale
    # jego KIERUNEK zwykle zostaje sensowny — przycinamy więc samą
    # długość, zamiast rezygnować z pozycji w całości.
    if dlugosc > LEVER_MAX:
        d = d * (LEVER_MAX / dlugosc)

    # Jakość liczona z PRZYCIĘTEGO d, czyli z tego, co naprawdę zobaczy
    # użytkownik — nie z rozwiązania, które odrzuciliśmy.
    reszta = float(((M @ d - a_world) ** 2).sum())
    calosc = float((a_world ** 2).sum())
    jakosc = 1.0 - reszta / calosc if calosc > 0 else 0.0

    return d, float(np.clip(jakosc, 0.0, 1.0))


# ============================================================
#  SCENA
# ============================================================

# Kolory ścian bryły zegarka. Tarcza jest jasna, spód ciemny, boki
# pośrednie — dzięki temu widać ORIENTACJĘ samej bryły, nawet gdy osie
# urządzenia patrzą prosto w kamerę i skracają się do punktu.
WATCH_TOP = "#e2e8f0"
WATCH_SIDE = "#475569"
WATCH_BOTTOM = "#1e293b"


def _watch_geometry(rozmiar):
    """Bryła zegarka w układzie URZĄDZENIA: wierzchołki, trójkąty, kolory.

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
    kolory = ([WATCH_BOTTOM] * 2 + [WATCH_TOP] * 2 + [WATCH_SIDE] * 8)
    return verts, faces, kolory


def _phase_spans(phases, t_probek, lo, hi, t_klatek):
    """Zakresy faz przeliczone z numerów wierszy CSV na numery klatek.

    Fazy w bazie są opisane numerami wierszy, bo taka jest oś X wykresu
    2D. Animacja ma własny raster klatek, więc przejście prowadzi przez
    CZAS: numer wiersza → sekunda → numer klatki. Gdyby przeliczać wprost
    proporcją numerów, kolory rozjechałyby się wszędzie tam, gdzie
    próbkowanie nie jest idealnie równe — a nie jest.
    """
    out = []
    for klucz, etykieta, kolor, start, koniec in phases:
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

    # Domknięcie mikroszczelin między fazami. Fazy zaznacza się
    # przeciągnięciem po wykresie, więc koniec jednej i początek następnej
    # rzadko wypadają na tym samym wierszu. Na wykresie 2D to niewidoczne,
    # ale w animacji podświetlenie gaśnie wtedy na jedną klatkę i wygląda
    # to jak usterka. Szersze dziury zostają — taka przerwa to już
    # świadoma decyzja, a nie niedokładność przeciągnięcia myszą.
    for wczesniejszy, pozniejszy in zip(out, out[1:]):
        szczelina = pozniejszy["i0"] - wczesniejszy["i1"]
        if 1 < szczelina <= MAX_PHASE_GAP + 1:
            pozniejszy["i0"] = wczesniejszy["i1"] + 1

    return out


def _build_figure(pos_cm, zakres, kolory_zegarka):
    """Scena Plotly: ślady statyczne (tor) plus ślady odświeżane co klatkę.

    CAŁY tor jest szary i cienki — to tło, kontekst całego zaznaczonego
    ruchu. Kolorem podświetla się wyłącznie faza, która akurat trwa.
    Malowanie wszystkich faz naraz dawało tęczę, w której nie było widać,
    gdzie w tej chwili jest ręka — a o to w animacji chodzi.

    Kolejność śladów jest częścią kontraktu z motion3d.js — indeksy
    dynamicznych wracają w payload["dynamic"], żeby przeglądarka nie
    musiała ich zgadywać ani szukać po nazwie.
    """
    def seria(v):
        return [round(float(c), 3) for c in v]

    data = [
        # Rzut toru na podłogę sceny. Bez niego oko nie ma jak ocenić
        # głębokości i każdy łuk wygląda na płaski.
        {
            "type": "scatter3d", "mode": "lines", "name": "rzut na podłogę",
            "x": seria(pos_cm[:, 0]), "y": seria(pos_cm[:, 1]),
            "z": [round(zakres["z"][0], 3)] * len(pos_cm),
            "line": {"color": "rgba(148,163,184,0.22)", "width": 2},
            "hoverinfo": "skip", "showlegend": False,
        },
        {
            "type": "scatter3d", "mode": "lines", "name": "tor ruchu",
            "x": seria(pos_cm[:, 0]), "y": seria(pos_cm[:, 1]), "z": seria(pos_cm[:, 2]),
            "line": {"color": "rgba(148,163,184,0.55)", "width": 3},
            "hoverinfo": "skip", "showlegend": False,
        },
    ]

    dynamiczne = {}

    # Podświetlenie trwającej fazy — leży POD ogonem i bryłą, bo jest
    # tłem dla bieżącego ruchu, a nie jego wskaźnikiem.
    dynamiczne["phase"] = len(data)
    data.append({
        "type": "scatter3d", "mode": "lines", "name": "bieżąca faza",
        "x": [], "y": [], "z": [],
        "line": {"color": NEUTRAL_COLOR, "width": 7},
        "hoverinfo": "skip", "showlegend": False,
    })

    dynamiczne["trail"] = len(data)
    data.append({
        "type": "scatter3d", "mode": "lines", "name": "ostatnia chwila",
        "x": [], "y": [], "z": [],
        "line": {"color": "#38bdf8", "width": 6},
        "hoverinfo": "skip", "showlegend": False,
    })

    dynamiczne["watch"] = len(data)
    data.append({
        "type": "mesh3d", "name": "zegarek",
        "x": [], "y": [], "z": [], "i": [], "j": [], "k": [],
        "facecolor": kolory_zegarka,
        "flatshading": True,
        "lighting": {"ambient": 0.62, "diffuse": 0.85, "specular": 0.18,
                     "roughness": 0.45, "fresnel": 0.1},
        "lightposition": {"x": 100, "y": 200, "z": 300},
        "hoverinfo": "skip", "showlegend": False,
    })

    dynamiczne["axes"] = []
    for kolor, nazwa in (("#f87171", "oś X urządzenia"),
                         ("#4ade80", "oś Y urządzenia"),
                         ("#60a5fa", "oś Z urządzenia")):
        dynamiczne["axes"].append(len(data))
        data.append({
            "type": "scatter3d", "mode": "lines", "name": nazwa,
            "x": [], "y": [], "z": [],
            "line": {"color": kolor, "width": 5},
            "hoverinfo": "skip", "showlegend": False,
        })

    def os(tytul, klucz):
        return {
            # Podpisy i cyfry na ciemnej scenie muszą być jaśniejsze niż na
            # jasnej karcie — #64748b, którym są opisane osie wykresu 2D,
            # daje tu ok. 3:1 i przy obrocie sceny znika w siatce.
            "title": {"text": tytul, "font": {"color": "#cbd5e1", "size": 11.5}},
            "tickfont": {"color": "#94a3b8", "size": 10.5},
            # Zaokrąglenie idzie do mikrometra, a nie do setnej centymetra:
            # przy `aspectmode: cube` Plotly rozciąga zakresy do sześcianu,
            # więc nierówne boki po zaokrągleniu zniekształcałyby tor.
            "range": [round(zakres[klucz][0], 4), round(zakres[klucz][1], 4)],
            "backgroundcolor": "#0f172a",
            "gridcolor": "rgba(148,163,184,0.16)",
            "zerolinecolor": "rgba(148,163,184,0.35)",
            "color": "#94a3b8",
            "showspikes": False,
        }

    layout = {
        # Ten sam krój co reszta interfejsu (patrz views.PLOT_FONT).
        "font": {
            "family": ("Inter, system-ui, -apple-system, 'Segoe UI', Roboto, "
                       "'Helvetica Neue', Arial, sans-serif"),
            "color": "#94a3b8",
        },
        # Ciemna scena, bo animacja to jasny obiekt w ruchu na tle
        # nieruchomej siatki — na białym tle jedno i drugie ma ten sam
        # ciężar i tor gubi się w gridzie.
        "paper_bgcolor": "#0b1220",
        "plot_bgcolor": "#0b1220",
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
        "showlegend": False,
        "hovermode": False,
        "scene": {
            "xaxis": os("X [cm]", "x"),
            "yaxis": os("Y [cm]", "y"),
            "zaxis": os("Z — pion [cm]", "z"),
            # Równe zakresy osi + sześcian: centymetr w każdą stronę ma
            # na ekranie tę samą długość. Bez tego tor ruchu jest
            # rozciągnięty w osi, w której akurat było najmniej ruchu.
            "aspectmode": "cube",
            "camera": {"eye": {"x": 1.5, "y": -1.7, "z": 0.9}},
            "dragmode": "orbit",
        },
        "uirevision": "motion3d",   # obrót sceny przeżywa podmianę danych
    }

    return {"data": data, "layout": layout}, dynamiczne


# ============================================================
#  GŁÓWNE WEJŚCIE
# ============================================================

def build_motion(prep, lo, hi, phases=(), fps=NO_FPS_LIMIT, smooth=True,
                 watch_scale=1.0):
    """Buduje scenę i klatki dla zakresu wierszy [lo, hi).

    KLATKA = PRAWDZIWA PRÓBKA Z PLIKU. Nie ma tu przepróbkowania na
    okrągły raster typu 60 kl/s — każda klatka to jeden wiersz CSV, ze
    swoim własnym, zmierzonym czasem. Czujnik nie próbkuje idealnie
    równo i ta nierówność jest częścią tego, jak ruch naprawdę wyglądał.

    `fps` nie jest częstotliwością docelową, tylko GÓRNYM LIMITEM: gdy
    plik ma gęstsze próbkowanie, bierzemy co k-tą próbkę. Nadal
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

    krok_s = float(np.median(dt))
    okno = max(3, int(round(SMOOTH_S / krok_s))) if krok_s > 0 else 3

    # ---------- orientacja ----------
    gyr = _boxcar(prep["gyr"][wyc], okno) if prep["gyr"] is not None else None
    acc = prep["acc"][wyc] if prep["acc"] is not None else None

    wygladzona = False
    niezgodnosc = None
    if prep["rot"] is not None:
        rw = prep["rotw"][wyc] if prep["rotw"] is not None else None
        q, opis_w = _quat_from_rotvec(prep["rot"][wyc], dt, rw, acc, gyr)
        if smooth:
            q, wygladzona = _smooth_quat(q)

        # Rotation vector kontra żyroskop.
        #
        # To jedyny w całej ścieżce test, który potrafi POWIEDZIEĆ, że
        # orientacja jest zepsuta, zamiast ją narysować. Prędkość kątowa
        # daje się policzyć na dwa niezależne sposoby: z pochodnej
        # kwaternionów i wprost z żyroskopu. Na zdrowym zapisie wychodzą
        # praktycznie te same przebiegi. Gdy rotation vector przeskakuje —
        # bo przeszedł przez filtr dolnoprzepustowy, bo w zgubiło znak,
        # bo plik jest uszkodzony — jego pochodna staje się grzebieniem
        # igieł, a żyroskop zostaje gładki. Wystarczy porównać.
        #
        # Próg jest wysoko (błąd wielkości samego sygnału), bo pomyłka
        # w drugą stronę też kosztuje: zdrowy rotation vector jest lepszym
        # źródłem niż całkowany żyroskop, który nie ma odniesienia kursu.
        if gyr is not None:
            omega_q = _boxcar(_quat_angular_velocity(q, t), okno)
            skala = float(np.sqrt((gyr ** 2).sum(axis=1).mean()))
            if skala > 1e-6:
                niezgodnosc = float(
                    np.sqrt(((omega_q - gyr) ** 2).sum(axis=1).mean()) / skala)

        if niezgodnosc is not None and niezgodnosc > ROT_GYRO_MAX:
            q = _integrate_gyro(gyr, dt, acc)
            source = "gyro"
            opis_orientacji = (
                f"rotation vector rozjeżdża się z żyroskopem "
                f"({niezgodnosc:.1f}× sygnał) — orientacja całkowana "
                f"z żyroskopu")
            wygladzona = False
        else:
            source = "fused" if gyr is not None else "rot"
            opis_orientacji = "orientacja z rotation vectora"
            if wygladzona:
                opis_orientacji += ", wygładzona między aktualizacjami czujnika"
            if opis_w != "rotw z pliku":
                opis_orientacji += f" ({opis_w})"
    elif prep["gyr"] is not None:
        q = _integrate_gyro(gyr, dt, acc)
        source = "gyro"
        opis_orientacji = "orientacja całkowana z żyroskopu"
    else:
        raise Motion3DError(
            "Plik nie zawiera ani rotation vectora (rot*), ani żyroskopu (gyr*) — "
            "nie ma z czego odtworzyć orientacji.")

    R = _quat_matrices(q)

    # ---------- prędkość kątowa ----------
    # Żyroskop mierzy ją wprost i gęściej niż aktualizuje się rotation
    # vector, więc ma pierwszeństwo. Bez niego różniczkujemy orientację.
    omega = gyr if gyr is not None else _boxcar(_quat_angular_velocity(q, t), okno)
    domega = _derivative(omega, t)

    # ---------- przyspieszenie w układzie świata ----------
    kandydaci = []
    if prep["acc"] is not None:
        kandydaci.append(("acc* minus grawitacja",
                          _to_world(R, prep["acc"][wyc]) - np.array([0.0, 0.0, G])))
    if prep["lin"] is not None:
        kandydaci.append(("linacc*", _to_world(R, prep["lin"][wyc])))

    # ---------- pozycja z modelu dźwigni ----------
    #
    # Gdy plik ma i acc*, i linacc*, nie zgadujemy, która seria jest lepsza:
    # dopasowujemy dźwignię do obu i bierzemy tę, którą model tłumaczy
    # lepiej. Na danych z zegarka raz wygrywa jedna, raz druga — zależnie
    # od tego, jak plik był zapisany.
    najlepsze = None
    for opis_acc, a_world in kandydaci:
        d, jakosc = _lever_fit(R, omega, domega, _boxcar(a_world, okno))
        if najlepsze is None or jakosc > najlepsze[2]:
            najlepsze = (opis_acc, d, jakosc)

    if najlepsze is None or not najlepsze[1].any():
        opis_acc = ("brak akcelerometru — sam obrót" if najlepsze is None
                    else "przyspieszenia nie da się wytłumaczyć obrotem — sam obrót")
        d = np.zeros(3)
        jakosc = 0.0
        pozycja_znana = False
    else:
        opis_acc, d, jakosc = najlepsze
        pozycja_znana = True

    pos = _to_world(R, np.broadcast_to(d, (len(t), 3)))
    pos -= pos.mean(axis=0)
    vel = _to_world(R, np.cross(omega, d))

    # ---------- klatki = próbki z pliku ----------
    czas = float(t[-1])
    liczba_probek = len(t)
    fps_natywne = (liczba_probek - 1) / czas if czas > 0 else 0.0

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
    q_k = q[idx]

    # Statystyki liczą się z metrów, scena rysuje w centymetrach.
    droga = float(np.linalg.norm(np.diff(pos_k, axis=0), axis=1).sum())
    v_max = float(np.linalg.norm(vel, axis=1).max())
    pos_cm = pos_k * 100.0

    # ---------- zakres sceny ----------
    #
    # Bok sześcianu to rozpiętość ruchu POWIĘKSZONA dokładnie o tyle, ile
    # wystaje poza nadgarstek bryła zegarka razem z osiami urządzenia.
    # Plotly przycina wszystko, co wypada poza `range`, a osie sięgają
    # dalej niż sama bryła — bez tego marginesu chowałyby się pod ścianą
    # sceny dokładnie w położeniach skrajnych, czyli tam, gdzie najwięcej
    # widać.
    srodek = (pos_cm.max(axis=0) + pos_cm.min(axis=0)) / 2.0
    rozpietosc = float((pos_cm.max(axis=0) - pos_cm.min(axis=0)).max())

    # Bryła zegarka skaluje się do ruchu, ale nie w dół bez końca:
    # MIN_SPAN_CM jest podłogą ODNIESIENIA, żeby przy drobnym ruchu (albo
    # przy samym obrocie w miejscu) zegarek nie skurczył się razem z nim
    # do niewidocznego punktu.
    skala = max(rozpietosc, MIN_SPAN_CM)
    rozmiar_watch = skala * WATCH_FRACTION * float(watch_scale)
    dlugosc_osi = rozmiar_watch * AXIS_FRACTION
    margines = max(dlugosc_osi, rozmiar_watch * WATCH_RADIUS) * 1.08

    # Bok wychodzi z samego ruchu i marginesu — bez własnej podłogi.
    # Podłoga na BOKU dawała odwrotny skutek niż zamierzony: przy ruchu
    # mniejszym od niej rozdmuchiwała kadr do stałego rozmiaru i to, co
    # miało być widoczne, malało do kilku pikseli pośrodku pustej sceny.
    bok = rozpietosc + 2.0 * margines
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

    verts, faces, kolory = _watch_geometry(rozmiar_watch)
    figure, dynamiczne = _build_figure(pos_cm, zakres, kolory)

    # ---------- opis ----------
    if not pozycja_znana:
        opis_modelu = ("bez pozycji — animacja pokazuje sam obrót "
                       "nadgarstka w miejscu")
    elif jakosc < LEVER_FIT_WARN:
        opis_modelu = (f"tor z ramienia {np.linalg.norm(d) * 100:.0f} cm, ale model "
                       f"tłumaczy tylko {jakosc * 100:.0f}% przyspieszenia — "
                       f"ruch miał dużą składową przesunięcia całej ręki")
    else:
        opis_modelu = (f"tor z ramienia {np.linalg.norm(d) * 100:.0f} cm "
                       f"(model tłumaczy {jakosc * 100:.0f}% przyspieszenia)")

    return {
        "figure": figure,
        "payload": {
            "t": [round(float(v), 4) for v in tk],
            "pos": [[round(float(c), 4) for c in wiersz] for wiersz in pos_cm],
            "quat": [[round(float(c), 6) for c in wiersz] for wiersz in q_k],
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
            "label": f"{opis_orientacji}; {opis_modelu}",
            "source": source,
            "acc_source": opis_acc,
            "frames": klatki,
            # Ostatnia klatka JEST ostatnią próbką zaznaczenia, więc czas
            # animacji i długość wycinka to jedno i to samo.
            "duration": round(czas, 4),
            "sample_rate": round(fps_natywne, 2),
            "stride": krok,
            "smoothed": wygladzona,
            # Rozjazd rotation vectora z żyroskopem, w wielokrotnościach
            # samego sygnału. None = nie było czym porównać. Powyżej
            # ROT_GYRO_MAX orientacja poszła z żyroskopu — bez tej liczby
            # nie da się zauważyć, że plik ma zepsuty wektor obrotu.
            "rot_vs_gyro": (round(niezgodnosc, 3)
                            if niezgodnosc is not None else None),
            "rows": hi - lo,
            "lo": lo,
            "hi": hi,
            "path_cm": round(droga * 100.0, 2),
            "v_max": round(v_max, 4),
            "has_position": pozycja_znana,
            # Długość dopasowanego ramienia i jakość dopasowania. Bez tej
            # pary nie da się odróżnić „ruch był mały” od „modelu nie ma
            # jak dopasować”.
            "lever_cm": round(float(np.linalg.norm(d)) * 100.0, 1),
            "lever_fit": round(jakosc, 3),
            # Bok sześcianu sceny. Bez tej liczby „mały ruch” i „duży ruch”
            # wyglądają na ekranie tak samo — scena skaluje się do
            # zawartości, więc dopiero ona mówi, co się właściwie ogląda.
            "span_cm": round(bok, 3),
            "time_source": prep["time_source"],
            "gaps": prep["gaps"],
            "phases": spans,
        },
    }

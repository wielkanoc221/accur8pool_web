"""ACCUR8POOL — rekonstrukcja ruchu 3D z surowego zapisu IMU.

Pakiet jest CZYSTO LICZBOWY: dostaje ścieżkę do CSV i zakres wierszy,
oddaje gotową scenę Plotly plus klatki animacji. Nie wie nic o Django,
o segmentach w bazie ani o tym, kto na to patrzy — dzięki temu daje się
testować na danych syntetycznych o znanej kinematyce.

CAŁE PUBLICZNE WEJŚCIE TO TEN PLIK
----------------------------------
    supports(names)          — czy z takich kolumn da się cokolwiek zrobić
    prepare(path)            — wczytanie pliku (SensorRecording)
    describe(recording)      — krótka charakterystyka nagrania
    build_motion(...)        — scena i klatki dla zakresu wierszy

Reszta modułów to warstwy, na które rozpada się ta ostatnia funkcja:

    recording.py    plik CSV → serie w SI i okno [lo, hi)
    time_axis.py    rozpoznanie osi czasu (patrz punkt 1 niżej)
    quaternions.py  kwaterniony i całkowanie żyroskopu
    orientation.py  wybór źródła orientacji i kontrola jego jakości
    lever_arm.py    pozycja z modelu sztywnej dźwigni
    frames.py       które próbki trafiają na klatki
    scene.py        kadr, bryła zegarka, fazy, figura Plotly
    builder.py      kolejność powyższych i kształt odpowiedzi

SKĄD BIERZE SIĘ RUCH
--------------------
Zegarek mierzy DWIE rzeczy naprawdę: orientację (rotation vector albo
żyroskop) i przyspieszenie. Pozycji nie mierzy nikt.

Poprzednia wersja robiła pozycję z DWUKROTNEGO CAŁKOWANIA przyspieszenia
i cała reszta modułu była walką ze skutkami tej decyzji: filtry
górnoprzepustowe, wykrywanie bezruchu, progi dobierane z kwantyli,
zerowanie prędkości (ZUPT). Nie da się tego wygrać na odcinku jednego
uderzenia. Błąd orientacji rzędu 0.3° zostawia w przyspieszeniu stałą
0.05 m/s², a to po dwóch sekundach 10 cm odpłynięcia — więc tor uderzenia
wychodził spiralą. Filtr, który by to skasował, musi mieć częstotliwość
graniczną rzędu 0.3 Hz, czyli akurat tam, gdzie leży samo uderzenie:
lekarstwo zjadało pacjenta. Stąd animacja, która „słabo wygląda”.

Tutaj pozycja bierze się z KINEMATYKI, a nie z całkowania — uderzenie jest
ruchem wahadłowym wokół nieruchomego łokcia, więc wystarczy jedna trójka
liczb na cały segment zamiast trajektorii w każdej próbce. Wyprowadzenie
i cena tego założenia: lever_arm.py.

Za to model NIE POKAŻE czystego przesunięcia bez obrotu — takiego ruchu
nie ma z czego odtworzyć. Jakość dopasowania (ile procent zmierzonego
przyspieszenia tłumaczy dźwignia) wraca w `meta.lever_fit` i interfejs ma
ją pokazać, żeby dało się odróżnić rekonstrukcję od zgadywanki.

CO JESZCZE JEST TRUDNE W TYCH DANYCH
------------------------------------
1. Oś czasu ma w każdym pokoleniu pliku inne znaczenie — bezwzględne
   nanosekundy, odstęp w milisekundach albo w sekundach. Interpretacja
   wprost myli się o trzy rzędy wielkości i animacja leci 1000× za szybko
   albo za wolno. Dlatego jednostka jest WYKRYWANA (time_axis.py).

2. `rotw` bywa śmieciem (w jednym z plików ma stałą wartość 246), więc
   czwarta składowa kwaternionu bywa odtwarzana z pozostałych trzech.
   Androidowe w = sqrt(1 - x² - y² - z²) jest jednak prawdziwe TYLKO do
   180° obrotu — powyżej daje obrót odwrotny, a granicę widać w danych
   z zegarka regularnie (quaternions.scalar_part).

3. Rotation vector przychodzi wolniej niż akcelerometr i jest w pliku
   POWTÓRZONY między aktualizacjami. Animacja z takich danych chodzi
   skokowo, dlatego przetrzymane próbki są wygładzane
   (quaternions.smooth_held_samples).

4. Wektora obrotu NIE WOLNO filtrować składowa po składowej — jego
   składowe wiąże warunek |q| = 1, o którym filtr nie wie. Przez pewien
   czas robiło to przygotowanie danych i dlatego animacja musiała czytać
   plik surowy. Teraz rot* przechodzą przez transform_raw_df bez zmian,
   więc wersja przygotowana jest jedynym źródłem dla całej aplikacji —
   szczegóły przy COLUMNS_TO_FILTER_5_CUT_OFF w prepare_raw_data.
   Gdyby mimo to trafił się zapis z zepsutą orientacją, wyłapuje go
   porównanie z żyroskopem (orientation.py) i animacja przechodzi na
   całkowanie żyroskopu.

UKŁADY WSPÓŁRZĘDNYCH
--------------------
Kwaternion obraca wektor Z UKŁADU URZĄDZENIA DO UKŁADU ŚWIATA, gdzie oś Z
jest pionem (konwencja Androida). Sprawdzenie na danych z zegarka:
przyspieszenie obrócone do świata ma średnią [0, 0, 9.81] — czyli samą
grawitację, tak jak być powinno.
"""

from __future__ import annotations

from pathlib import Path

from .builder import MotionBuilder
from .constants import (
    G,
    MAX_FRAMES,
    MAX_ROWS,
    MIN_ROWS,
    NO_FPS_LIMIT,
)
from .errors import Motion3DError
from .recording import RecordingWindow, SensorRecording, supports
from .time_axis import TimeAxis

__all__ = [
    "Motion3DError",
    "MotionBuilder",
    "RecordingWindow",
    "SensorRecording",
    "TimeAxis",
    "build_motion",
    "describe",
    "prepare",
    "supports",
    "G",
    "MAX_FRAMES",
    "MAX_ROWS",
    "MIN_ROWS",
    "NO_FPS_LIMIT",
]


def prepare(path: Path) -> SensorRecording:
    """Wczytuje CSV i wystawia surowe serie w jednostkach SI.

    Świadomie NIE liczy tu orientacji ani pozycji: jedno i drugie zależy od
    wybranego zakresu (dźwignia dopasowuje się do konkretnego ruchu),
    a plik potrafi mieć kilkadziesiąt tysięcy wierszy, z których obejrzy
    się dwa uderzenia. Rachunki idą w build_motion, na wycinku.
    """
    return SensorRecording.from_csv(path)


def describe(recording: SensorRecording) -> dict:
    """Krótka charakterystyka pliku — do komunikatów i do testów."""
    return recording.describe()


def build_motion(recording: SensorRecording, lo, hi, phases=(),
                 fps=NO_FPS_LIMIT, smooth=True, watch_scale=1.0) -> dict:
    """Buduje scenę i klatki dla zakresu wierszy [lo, hi).

    `fps` nie jest częstotliwością docelową, tylko GÓRNYM LIMITEM gęstości
    klatek — szczegóły w frames.py. Odtwarzacz w przeglądarce dobiera
    klatkę po czasie z zegara ściennego (wyszukiwanie binarne po
    `payload.t`), więc animacja idzie 1:1 z czasem zapisanym w pliku
    niezależnie od tego, ile klatek zdąży narysować karta graficzna.
    """
    lo, hi = _checked_range(recording, lo, hi)
    return MotionBuilder(recording, lo, hi, phases=phases, fps=fps,
                         smooth=smooth, watch_scale=watch_scale).build()


def _checked_range(recording: SensorRecording, lo, hi):
    """Zakres przycięty do pliku — albo komunikat, co z nim nie tak.

    Limity czyta z tego modułu (a nie z .constants), żeby dało się je
    podmienić w testach jednym mock.patch.object(motion3d, "MAX_ROWS", …).
    """
    lo = max(0, int(lo))
    hi = min(int(hi), recording.rows)
    rows = hi - lo

    if rows < MIN_ROWS:
        raise Motion3DError(
            f"Zakres jest za krótki — potrzeba co najmniej {MIN_ROWS} próbek.")

    if rows > MAX_ROWS:
        raise Motion3DError(
            f"Zakres obejmuje {rows:,} próbek. Animacja liczy się dla "
            f"pojedynczego uderzenia — zaznacz segment albo przybliż wykres "
            f"(limit {MAX_ROWS:,}).".replace(",", " "))

    return lo, hi

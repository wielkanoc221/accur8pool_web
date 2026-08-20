"""Tor nadgarstka z PODWÓJNEGO CAŁKOWANIA przyspieszenia.

    a_świat(t) = R(t) · a_urządzenie(t) − g
    v(t) = ∫ a dt
    p(t) = ∫ v dt

Żadnego założenia o kształcie ruchu: nadgarstek może jechać po łuku, po
prostej, przybliżać się do łokcia i oddalać. To jest różnica względem
poprzedniego modelu sztywnej dźwigni (p = R·d), który wymuszał tor na
sferze o stałym promieniu i na nagraniach demo tłumaczył tylko 24–31%
zmierzonego przyspieszenia — resztę po prostu odrzucał.

CENA jest znana i trzeba ją znać: całkowanie kumuluje błąd. Stały błąd
przyspieszenia ε narasta w pozycji jak ε·t²/2, więc po kilku sekundach
tor odpływa niezależnie od jakości czujnika.

Trzyma to w ryzach jeden z DWÓCH mechanizmów, wybierany po tym, co widać
na końcach segmentu (`_ends_at_rest`):

    spoczynek na obu końcach  →  v(0) = v(koniec) = 0, a to, co narosło
        pomiędzy, odejmuje się rampą (`_anchor_ends`). Prędkość wychodzi
        wtedy DOKŁADNA i odporna na błąd zera akcelerometru — sprawdzone
        na sygnale o znanej kinematyce do 0.05 m/s przy biasie 1 m/s².
        Pozycji już się nie filtruje.

    segment ucięty w ruchu    →  prędkości początkowej nie ma z czego
        wziąć, więc zostaje filtr górnoprzepustowy (`_DriftControl`).
        Działa, ale v_max jest wtedy przybliżone (rzędu +10%) i interfejs
        mówi o tym wprost.

Ile korekta usunęła, wraca w `meta.drift_cm` — bez tej liczby nie da się
odróżnić zmierzonego przesunięcia od odpłynięcia całkowania.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import quaternions as quat
from .constants import (
    ANCHOR_MAX_S,
    DRIFT_CUTOFF_HZ,
    DRIFT_WARN,
    G,
    REST_RATIO,
    REST_SPAN_S,
)
from .numeric import boxcar
from .orientation import Orientation
from .recording import RecordingWindow


@dataclass(frozen=True)
class Trajectory:
    """Tor i prędkość nadgarstka w układzie świata.

    `known` = False znaczy, że pozycji nie ma z czego policzyć (plik bez
    akcelerometru). Animacja pokazuje wtedy sam obrót w miejscu i mówi
    o tym wprost, zamiast rysować tor, którego nie zmierzono.
    """

    position: np.ndarray           # (N, 3) [m], wyśrodkowany na zerze
    velocity: np.ndarray           # (N, 3) [m/s]
    acc_source: str
    known: bool
    anchored: bool                 # czy użyto warunku spoczynku na końcach
    drift_m: float                 # ile usunęła korekta dryfu
    drift_ratio: float             # ...w stosunku do rozpiętości toru

    # ------------------------------------------------------------
    #  REKONSTRUKCJA
    # ------------------------------------------------------------

    @classmethod
    def solve(cls, window: RecordingWindow, orientation: Orientation) -> "Trajectory":
        """Tor z najlepszej dostępnej serii przyspieszenia.

        linacc* ma pierwszeństwo przed acc*: to wyjście fuzji czujników,
        w którym grawitacja jest już odjęta z użyciem pełnej orientacji.
        Odejmowanie jej samemu od acc* wymaga, żeby orientacja była
        dokładna co do ułamka stopnia — przy błędzie 1° zostaje 0.17 m/s²
        resztkowej grawitacji, a to po dwóch sekundach całkowania 34 cm
        toru z niczego.
        """
        source, a_world = cls._acceleration(window, orientation)
        if a_world is None:
            samples = len(orientation.q)
            return cls(position=np.zeros((samples, 3)),
                       velocity=np.zeros((samples, 3)),
                       acc_source="brak akcelerometru — sam obrót",
                       known=False, anchored=False,
                       drift_m=0.0, drift_ratio=0.0)

        drift = _DriftControl.for_window(window)

        # Bias akcelerometru odchodzi jako STAŁA, a nie średnią kroczącą —
        # i to jest różnica warta kilkunastu procent na v_max.
        #
        # Średnia krocząca dopełnia brzegi odbiciem, więc na pierwszej
        # i ostatniej próbce zostawia błąd rzędu 1.4 m/s² (przy sygnale
        # 6.8 m/s²). Sam w sobie byłby nieszkodliwy, ale ten błąd jest
        # potem CAŁKOWANY: jego całka to skok prędkości 0.91 m/s, czyli
        # dokładnie tyle, ile wynosi cała prawdziwa amplituda. Skok nie
        # znika — zostaje do końca okna i zawyżał v_max o 19–21%,
        # niezależnie od tego, czy bias w danych w ogóle był.
        #
        # Odjęcie stałej nie ma brzegów, więc nie ma i tego artefaktu.
        # Wolno je zrobić, bo ma fizyczne uzasadnienie: segment obejmuje
        # całe uderzenie, a nadgarstek na jego początku i końcu stoi, więc
        # ∫a dt = Δv = 0, czyli PRAWDZIWE przyspieszenie ma w takim oknie
        # średnią zero. Co zostaje w średniej, jest błędem zera czujnika.
        #
        # Ograniczenie: bias NARASTAJĄCY (dryf zera w trakcie nagrania)
        # zostaje tylko częściowo — łapie go dopiero filtr na prędkości.
        acceleration = a_world - a_world.mean(axis=0)
        raw_velocity = _integrate(acceleration, window.seconds)

        if _short_enough_to_anchor(window) and _ends_at_rest(a_world,
                                                            window.sample_rate):
            velocity = _anchor_ends(raw_velocity)
            # Pozycji NIE filtrujemy. Uderzenie przenosi nadgarstek z miejsca
            # na miejsce, więc tor jest tu narastający — średnia krocząca
            # odjęłaby to przesunięcie jako „dryf” i dorysowała zawracanie,
            # którego nie było (mierzone: droga 101 cm zamiast 60 przy
            # segmencie z zapasem spoczynku po bokach).
            position = _integrate(velocity, window.seconds)
            anchored = True
        else:
            velocity = drift.highpass(raw_velocity)
            position = drift.highpass(_integrate(velocity, window.seconds))
            anchored = False

        # Dryf liczymy TAK SAMO w obu gałęziach: jako różnicę względem toru
        # zupełnie nieskorygowanego. Wcześniej gałąź z kotwicą mierzyła
        # odjętą rampę PRĘDKOŚCI, co dawało 1.1 cm przy torze uciekającym na
        # 97 metrów — czyli liczba pilnująca wiarygodności milczała dokładnie
        # wtedy, gdy była potrzebna.
        uncorrected = _integrate(raw_velocity, window.seconds)
        removed = uncorrected - position

        spread = float(np.linalg.norm(position - position.mean(axis=0), axis=1).max())
        drift_m = float(np.linalg.norm(removed - removed.mean(axis=0), axis=1).max())

        return cls(
            position=position - position.mean(axis=0),
            velocity=velocity,
            acc_source=source,
            known=True,
            anchored=anchored,
            drift_m=drift_m,
            drift_ratio=drift_m / spread if spread > 1e-9 else 0.0,
        )

    @staticmethod
    def _acceleration(window: RecordingWindow, orientation: Orientation):
        """Przyspieszenie RUCHU w układzie świata (bez grawitacji)."""
        if window.lin is not None:
            return "linacc*", quat.to_world(orientation.matrices, window.lin)
        if window.acc is not None:
            return ("acc* minus grawitacja",
                    quat.to_world(orientation.matrices, window.acc)
                    - np.array([0.0, 0.0, G]))
        return None, None

    # ------------------------------------------------------------
    #  OPIS DLA INTERFEJSU
    # ------------------------------------------------------------

    def describe(self) -> str:
        if not self.known:
            return "bez pozycji — animacja pokazuje sam obrót nadgarstka w miejscu"

        text = f"tor scałkowany z {self.acc_source}"

        # Który wariant zadziałał, decyduje o wiarygodności prędkości —
        # więc mówimy o tym wprost, a nie tylko w kodzie.
        if self.anchored:
            text += ", segment zaczyna się i kończy w spoczynku"
        else:
            text += (", segment ucięty w ruchu — prędkość początkowa "
                     "nieznana, v_max jest przybliżone")

        if self.drift_ratio > DRIFT_WARN:
            # Filtr usunął więcej, niż zostało na ekranie. Kształt toru
            # jest wtedy w większości dziełem filtru, nie pomiaru.
            text += (f", ale filtr usunął dryf większy od samego toru "
                     f"({self.drift_m * 100:.0f} cm) — zaznacz krótszy segment")
        return text


# ============================================================
#  CAŁKOWANIE I DRYF
# ============================================================

def _ends_at_rest(a_world: np.ndarray, sample_rate: float) -> bool:
    """Czy nadgarstek stoi na OBU końcach okna.

    To jest jedyne pytanie, na które trzeba odpowiedzieć, żeby wybrać
    sposób odjęcia dryfu — a odpowiedź decyduje o wyniku bardziej niż
    cokolwiek innego w tym module.

    Prędkości początkowej NIE DA SIĘ policzyć z przyspieszenia: całkowanie
    daje ją z dokładnością do stałej. Trzeba więc coś założyć, a każde
    założenie jest w innym przypadku fałszywe:

      • segment obejmuje całe uderzenie (tak zaznacza użytkownik) —
        nadgarstek stoi na obu końcach, więc v(0) = v(koniec) = 0 i całą
        resztę wolno uznać za dryf. Wynik jest wtedy DOKŁADNY.
      • segment wycięty w środku ciągłego ruchu — nadgarstek jedzie na
        obu końcach, założenie o spoczynku zawyżałoby prędkość dwukrotnie.
        Zostaje filtr średniej kroczącej: gorszy, ale nie kłamie tak.

    Rozstrzygamy tym, co widać: przy nadgarstku w spoczynku przyspieszenie
    RUCHU (grawitacja jest już odjęta) siedzi przy zerze. Patrzymy na
    fragment z każdej strony, a nie na pojedynczą próbkę — w ruchu
    harmonicznym przyspieszenie przechodzi przez zero w chwili największej
    prędkości i pojedyncza próbka wskazałaby wtedy „spoczynek”.
    """
    magnitude = np.linalg.norm(a_world, axis=1)
    peak = float(magnitude.max())
    if peak <= 1e-9:
        return True                      # nic się nie działo — spoczynek

    span = max(int(REST_SPAN_S * sample_rate), 3)
    if 2 * span >= len(magnitude):
        return False                     # okno krótsze niż dwa marginesy

    quiet = max(float(magnitude[:span].mean()),
                float(magnitude[-span:].mean()))
    return quiet <= REST_RATIO * peak


def _anchor_ends(velocity: np.ndarray) -> np.ndarray:
    """Zeruje prędkość na obu końcach, rozkładając poprawkę liniowo.

    Całkowanie startuje od zera, więc v[0] = 0 wychodzi samo. Cała
    niezerowa wartość na KOŃCU jest tym, co narosło z błędu zera
    akcelerometru — a że narastała równomiernie, odejmuje się ją rampą,
    nie skokiem.
    """
    ramp = np.linspace(0.0, 1.0, len(velocity))[:, None]
    return velocity - (velocity[0] + (velocity[-1] - velocity[0]) * ramp)


def _integrate(values: np.ndarray, seconds: np.ndarray) -> np.ndarray:
    """Całka skumulowana metodą trapezów, na NIERÓWNEJ siatce czasu.

    Trapezy, nie prostokąty: przy 100 Hz i sygnale kilkuhercowym prostokąt
    zaniża amplitudę o kilka procent na każdym całkowaniu, więc po dwóch
    krokach droga wychodzi krótsza od prawdziwej bez żadnego powodu.

    Odstępy między próbkami bywają nierówne (widywane 7.2–12.8 ms przy
    nominalnych 10 ms), więc krok bierze się z osi czasu, a nie ze
    średniej częstotliwości.
    """
    steps = 0.5 * (values[1:] + values[:-1]) * np.diff(seconds)[:, None]
    result = np.zeros_like(values)
    np.cumsum(steps, axis=0, out=result[1:])
    return result


class _DriftControl:
    """Filtr górnoprzepustowy trzymający całkowanie przy zerze.

    Odejmujemy średnią kroczącą: to, co zostaje, jest pozbawione składowej
    wolniejszej niż DRIFT_CUTOFF_HZ. Boxcar jest tu wystarczający i ma
    zaletę, której nie ma filtr rekurencyjny — jest symetryczny w czasie,
    więc NIE PRZESUWA FAZY. Filtr przesuwający fazę widać w animacji od
    razu: bryła jedzie spóźniona za wykresem 2D.

    Gdy okno filtru wychodzi szersze niż sam segment (krótkie zaznaczenie),
    boxcar degeneruje się do średniej z całości, a filtr — do odjęcia
    wartości średniej. To jest właściwe zachowanie, a nie awaria: na
    zaznaczeniu krótszym niż okres filtru nie ma czego odcinać poza stałą.
    """

    def __init__(self, window_samples: int):
        self._window = window_samples

    @classmethod
    def for_window(cls, window: RecordingWindow) -> "_DriftControl":
        return cls(max(int(round(window.sample_rate / DRIFT_CUTOFF_HZ)), 3))

    def highpass(self, values: np.ndarray) -> np.ndarray:
        return values - boxcar(values, self._window)

def _short_enough_to_anchor(window: RecordingWindow) -> bool:
    """Czy segment jest dość krótki, żeby zaufać samemu warunkowi spoczynku.

    Kotwica odejmuje z prędkości PROSTĄ, więc kasuje tylko dryf narastający
    równomiernie. Reszta — błąd orientacji, szum, pełzanie zera — zostaje
    i całkuje się w pozycji bez żadnej kontroli. Na jednym uderzeniu to nic;
    na całym nagraniu tor uciekał w linii prostej na 97 metrów przy
    rzeczywistym ruchu rzędu 45 cm.

    Próg (ANCHOR_MAX_S) jest czasowy i zmierzony — tabela przy stałej.
    """
    return window.duration <= ANCHOR_MAX_S

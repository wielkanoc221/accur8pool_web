"""Scena Plotly: kadr, bryła zegarka, fazy i gotowa figura.

Podział pracy z przeglądarką: tutaj powstają ślady STATYCZNE (cały tor
i jego rzut na podłogę) narysowane raz na zawsze oraz puste ślady
DYNAMICZNE, którym motion3d.js podmienia współrzędne co klatkę. Ich numery
wracają w `payload.dynamic`, więc przeglądarka nie musi ich zgadywać ani
szukać po nazwie — to jest kontrakt między tym plikiem a motion3d.js.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .constants import (
    AXIS_FRACTION,
    MAX_PHASE_GAP,
    MIN_SPAN_CM,
    NEUTRAL_COLOR,
    PLOT_FONT,
    WATCH_BOTTOM,
    WATCH_FRACTION,
    WATCH_RADIUS,
    WATCH_SIDE,
    WATCH_TOP,
)


# ============================================================
#  KADR
# ============================================================

@dataclass(frozen=True)
class SceneBounds:
    """Sześcian sceny w centymetrach, dobrany do zawartości.

    Bok to rozpiętość ruchu POWIĘKSZONA dokładnie o tyle, ile wystaje poza
    nadgarstek bryła zegarka razem z osiami urządzenia. Plotly przycina
    wszystko, co wypada poza `range`, a osie sięgają dalej niż sama bryła —
    bez tego marginesu chowałyby się pod ścianą sceny dokładnie
    w położeniach skrajnych, czyli tam, gdzie najwięcej widać.
    """

    center: np.ndarray
    side: float
    watch_size: float
    axis_length: float

    @classmethod
    def around(cls, positions_cm: np.ndarray, watch_scale: float) -> "SceneBounds":
        center = (positions_cm.max(axis=0) + positions_cm.min(axis=0)) / 2.0
        spread = float((positions_cm.max(axis=0) - positions_cm.min(axis=0)).max())

        # Bryła zegarka skaluje się do ruchu, ale nie w dół bez końca:
        # MIN_SPAN_CM jest podłogą ODNIESIENIA, żeby przy drobnym ruchu
        # (albo przy samym obrocie w miejscu) zegarek nie skurczył się razem
        # z nim do niewidocznego punktu.
        watch_size = max(spread, MIN_SPAN_CM) * WATCH_FRACTION * float(watch_scale)
        axis_length = watch_size * AXIS_FRACTION
        margin = max(axis_length, watch_size * WATCH_RADIUS) * 1.08

        # Bok wychodzi z samego ruchu i marginesu — bez własnej podłogi.
        # Podłoga na BOKU dawała odwrotny skutek niż zamierzony: przy ruchu
        # mniejszym od niej rozdmuchiwała kadr do stałego rozmiaru i to, co
        # miało być widoczne, malało do kilku pikseli pośrodku pustej sceny.
        return cls(center=center, side=spread + 2.0 * margin,
                   watch_size=watch_size, axis_length=axis_length)

    @property
    def ranges(self) -> dict:
        """Zakresy osi X, Y, Z — równe, żeby centymetr wszędzie znaczył tyle samo."""
        half = self.side / 2
        return {
            "x": (self.center[0] - half, self.center[0] + half),
            "y": (self.center[1] - half, self.center[1] + half),
            "z": (self.center[2] - half, self.center[2] + half),
        }


# ============================================================
#  BRYŁA ZEGARKA
# ============================================================

@dataclass(frozen=True)
class WatchGeometry:
    """Bryła zegarka w układzie URZĄDZENIA: wierzchołki, trójkąty, kolory.

    Prostopadłościan o proporcjach koperty zegarka: szerszy w osi Y (wzdłuż
    przedramienia), płaski w Z (od skóry w górę). Obracany jest
    w przeglądarce, bo obrót ośmiu wierzchołków to nic, a przesłanie ich
    dla każdej klatki byłoby kilkukrotnie większym JSON-em niż cała reszta.
    """

    vertices: list
    faces: list
    colors: list

    @classmethod
    def of(cls, size: float) -> "WatchGeometry":
        """`size` jest w jednostkach sceny (centymetry)."""
        hx, hy, hz = size * 0.45, size * 0.60, size * 0.16
        vertices = [
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
        colors = [WATCH_BOTTOM] * 2 + [WATCH_TOP] * 2 + [WATCH_SIDE] * 8
        return cls(vertices=vertices, faces=faces, colors=colors)


# ============================================================
#  FAZY
# ============================================================

def phase_spans(phases, window_seconds, lo, hi, frame_seconds) -> list:
    """Zakresy faz przeliczone z numerów wierszy CSV na numery klatek.

    Fazy w bazie są opisane numerami wierszy, bo taka jest oś X wykresu 2D.
    Animacja ma własny raster klatek, więc przejście prowadzi przez CZAS:
    numer wiersza → sekunda → numer klatki. Gdyby przeliczać wprost
    proporcją numerów, kolory rozjechałyby się wszędzie tam, gdzie
    próbkowanie nie jest idealnie równe — a nie jest.
    """
    spans = []
    for key, label, color, start, end in phases:
        start = max(int(start), lo)
        end = min(int(end), hi)
        if end <= start:
            continue

        t0 = float(window_seconds[start - lo])
        t1 = float(window_seconds[min(end - lo, len(window_seconds) - 1)])

        first = int(np.searchsorted(frame_seconds, t0, side="left"))
        last = int(np.searchsorted(frame_seconds, t1, side="right")) - 1
        first = max(0, min(first, len(frame_seconds) - 1))
        last = max(first, min(last, len(frame_seconds) - 1))

        spans.append({
            "phase": key,
            "label": label,
            "color": color or NEUTRAL_COLOR,
            "i0": first,
            "i1": last,
            "t0": round(t0, 4),
            "t1": round(t1, 4),
        })

    spans.sort(key=lambda span: span["i0"])
    _close_micro_gaps(spans)
    return spans


def _close_micro_gaps(spans) -> None:
    """Domyka mikroszczeliny między sąsiednimi fazami.

    Fazy zaznacza się przeciągnięciem po wykresie, więc koniec jednej
    i początek następnej rzadko wypadają na tym samym wierszu. Na wykresie
    2D to niewidoczne, ale w animacji podświetlenie gaśnie wtedy na jedną
    klatkę i wygląda to jak usterka. Szersze dziury zostają — taka przerwa
    to już świadoma decyzja, a nie niedokładność przeciągnięcia myszą.
    """
    for earlier, later in zip(spans, spans[1:]):
        gap = later["i0"] - earlier["i1"]
        if 1 < gap <= MAX_PHASE_GAP + 1:
            later["i0"] = earlier["i1"] + 1


def phase_per_frame(spans, frame_count: int) -> np.ndarray:
    """Numer fazy dla każdej klatki; -1 tam, gdzie żadna nie trwa."""
    per_frame = np.full(frame_count, -1, dtype=np.int64)
    for number, span in enumerate(spans):
        per_frame[span["i0"]:span["i1"] + 1] = number
    return per_frame


# ============================================================
#  FIGURA PLOTLY
# ============================================================

class FigureBuilder:
    """Składa figurę Plotly i spis śladów odświeżanych co klatkę.

    CAŁY tor jest szary i cienki — to tło, kontekst całego zaznaczonego
    ruchu. Kolorem podświetla się wyłącznie faza, która akurat trwa.
    Malowanie wszystkich faz naraz dawało tęczę, w której nie było widać,
    gdzie w tej chwili jest ręka — a o to w animacji chodzi.
    """

    def __init__(self, positions_cm: np.ndarray, bounds: SceneBounds,
                 watch_colors: list):
        self._positions = positions_cm
        self._bounds = bounds
        self._ranges = bounds.ranges
        self._watch_colors = watch_colors
        self._traces = []
        self._dynamic = {}

    def build(self):
        """Zwraca ({"data", "layout"}, indeksy śladów dynamicznych)."""
        self._add_static_traces()
        self._add_dynamic_traces()
        return {"data": self._traces, "layout": self._layout()}, self._dynamic

    # ------------------------------------------------------------
    #  ŚLADY
    # ------------------------------------------------------------

    def _add_static_traces(self) -> None:
        floor_z = round(self._ranges["z"][0], 3)

        # Rzut toru na podłogę sceny. Bez niego oko nie ma jak ocenić
        # głębokości i każdy łuk wygląda na płaski.
        self._add({
            "type": "scatter3d", "mode": "lines", "name": "rzut na podłogę",
            "x": self._axis(0), "y": self._axis(1),
            "z": [floor_z] * len(self._positions),
            "line": {"color": "rgba(148,163,184,0.22)", "width": 2},
            "hoverinfo": "skip", "showlegend": False,
        })
        self._add({
            "type": "scatter3d", "mode": "lines", "name": "tor ruchu",
            "x": self._axis(0), "y": self._axis(1), "z": self._axis(2),
            "line": {"color": "rgba(148,163,184,0.55)", "width": 3},
            "hoverinfo": "skip", "showlegend": False,
        })

    def _add_dynamic_traces(self) -> None:
        # Podświetlenie trwającej fazy — leży POD ogonem i bryłą, bo jest
        # tłem dla bieżącego ruchu, a nie jego wskaźnikiem.
        self._dynamic["phase"] = self._add({
            "type": "scatter3d", "mode": "lines", "name": "bieżąca faza",
            "x": [], "y": [], "z": [],
            "line": {"color": NEUTRAL_COLOR, "width": 7},
            "hoverinfo": "skip", "showlegend": False,
        })

        self._dynamic["trail"] = self._add({
            "type": "scatter3d", "mode": "lines", "name": "ostatnia chwila",
            "x": [], "y": [], "z": [],
            "line": {"color": "#38bdf8", "width": 6},
            "hoverinfo": "skip", "showlegend": False,
        })

        self._dynamic["watch"] = self._add({
            "type": "mesh3d", "name": "zegarek",
            "x": [], "y": [], "z": [], "i": [], "j": [], "k": [],
            "facecolor": self._watch_colors,
            "flatshading": True,
            "lighting": {"ambient": 0.62, "diffuse": 0.85, "specular": 0.18,
                         "roughness": 0.45, "fresnel": 0.1},
            "lightposition": {"x": 100, "y": 200, "z": 300},
            "hoverinfo": "skip", "showlegend": False,
        })

        self._dynamic["axes"] = [
            self._add({
                "type": "scatter3d", "mode": "lines", "name": name,
                "x": [], "y": [], "z": [],
                "line": {"color": color, "width": 5},
                "hoverinfo": "skip", "showlegend": False,
            })
            for color, name in (("#f87171", "oś X urządzenia"),
                                ("#4ade80", "oś Y urządzenia"),
                                ("#60a5fa", "oś Z urządzenia"))
        ]

    def _add(self, trace: dict) -> int:
        """Dokłada ślad i oddaje jego numer — numery są kontraktem z JS-em."""
        self._traces.append(trace)
        return len(self._traces) - 1

    def _axis(self, column: int) -> list:
        return [round(float(value), 3) for value in self._positions[:, column]]

    # ------------------------------------------------------------
    #  LAYOUT
    # ------------------------------------------------------------

    def _layout(self) -> dict:
        return {
            "font": {"family": PLOT_FONT, "color": "#94a3b8"},
            # Ciemna scena, bo animacja to jasny obiekt w ruchu na tle
            # nieruchomej siatki — na białym tle jedno i drugie ma ten sam
            # ciężar i tor gubi się w gridzie.
            "paper_bgcolor": "#0b1220",
            "plot_bgcolor": "#0b1220",
            "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
            "showlegend": False,
            "hovermode": False,
            "scene": {
                "xaxis": self._axis_layout("X [cm]", "x"),
                "yaxis": self._axis_layout("Y [cm]", "y"),
                "zaxis": self._axis_layout("Z — pion [cm]", "z"),
                # Równe zakresy osi + sześcian: centymetr w każdą stronę ma
                # na ekranie tę samą długość. Bez tego tor ruchu jest
                # rozciągnięty w osi, w której akurat było najmniej ruchu.
                "aspectmode": "cube",
                "camera": {"eye": {"x": 1.5, "y": -1.7, "z": 0.9}},
                "dragmode": "orbit",
            },
            "uirevision": "motion3d",   # obrót sceny przeżywa podmianę danych
        }

    def _axis_layout(self, title: str, key: str) -> dict:
        return {
            # Podpisy i cyfry na ciemnej scenie muszą być jaśniejsze niż na
            # jasnej karcie — #64748b, którym są opisane osie wykresu 2D,
            # daje tu ok. 3:1 i przy obrocie sceny znika w siatce.
            "title": {"text": title, "font": {"color": "#cbd5e1", "size": 11.5}},
            "tickfont": {"color": "#94a3b8", "size": 10.5},
            # Zaokrąglenie idzie do mikrometra, a nie do setnej centymetra:
            # przy `aspectmode: cube` Plotly rozciąga zakresy do sześcianu,
            # więc nierówne boki po zaokrągleniu zniekształcałyby tor.
            "range": [round(self._ranges[key][0], 4),
                      round(self._ranges[key][1], 4)],
            "backgroundcolor": "#0f172a",
            "gridcolor": "rgba(148,163,184,0.16)",
            "zerolinecolor": "rgba(148,163,184,0.35)",
            "color": "#94a3b8",
            "showspikes": False,
        }

"""Animacja 3D po stronie serwera: parametry żądania i pamięć podręczna.

Sama rekonstrukcja jest w pakiecie motion3d i nie wie nic o HTTP. Tutaj
zostaje to, co wie: jakie parametry przyjmujemy z adresu i dlaczego wynik
opłaca się trzymać.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import motion3d
from .segments import phase_tuples

# Klatki animacji 3D. `fps` NIE jest tempem docelowym, tylko GÓRNYM
# LIMITEM gęstości próbek (patrz motion3d.frames): klatka to zawsze
# prawdziwy wiersz CSV, a limit decyduje tylko o tym, czy przy szybkim
# zapisie brać co drugą albo co czwartą.
#
# Bez parametru w zapytaniu limitu NIE MA. Przeglądarka celowo go nie
# wysyła, bo odtwarzacz dobiera klatkę po czasie z zegara i chce widzieć
# to, co czujnik zmierzył — razem z nierównym odstępem między pomiarami.
# Rozmiaru odpowiedzi i tak pilnuje motion3d.MAX_FRAMES.
MIN_FPS = 5.0
MAX_FPS = 1000.0

# Skala bryły zegarka względem sceny. Szeroko, bo przy bardzo drobnym
# ruchu zegarek w domyślnym rozmiarze zasłania cały tor.
MIN_WATCH_SCALE = 0.2
MAX_WATCH_SCALE = 20.0


@dataclass(frozen=True)
class MotionOptions:
    """Parametry animacji przyjęte z adresu, już sprowadzone do zakresu."""

    fps: float = motion3d.NO_FPS_LIMIT
    smooth: bool = True
    watch_scale: float = 1.0

    @classmethod
    def from_query(cls, query) -> "MotionOptions":
        """Czyta ?fps=&smooth=&watch= — rzuca ValueError na śmieciach."""
        return cls(
            fps=(_clamp(_number(query, "fps", MIN_FPS), MIN_FPS, MAX_FPS)
                 if query.get("fps") else motion3d.NO_FPS_LIMIT),
            smooth=_flag(query, "smooth", True),
            watch_scale=_clamp(_number(query, "watch", 1.0),
                               MIN_WATCH_SCALE, MAX_WATCH_SCALE),
        )


def scene_for_segment(path: Path, segment, options: MotionOptions) -> dict:
    """Scena i klatki dla JEDNEGO segmentu, z pamięci podręcznej.

    Zakres bierze się WYŁĄCZNIE z segmentu w bazie: tor nadgarstka powstaje
    z modelu sztywnej dźwigni dopasowanego do TEGO ruchu, a taki model
    opisuje jedno uderzenie, nie kwadrans nagrania.
    """
    # Nazwy kluczy muszą się zgadzać z sygnaturą motion3d.build_motion —
    # lecą do niej jako **params.
    params = (
        ("lo", segment.start),
        ("hi", segment.end),
        ("phases", phase_tuples(segment)),
        ("fps", options.fps),
        ("smooth", options.smooth),
        ("watch_scale", options.watch_scale),
    )

    stat = path.stat()
    return _cached_scene(str(path), stat.st_mtime_ns, stat.st_size, params)


@lru_cache(maxsize=8)
def _cached_scene(path_str: str, mtime: int, size: int, params: tuple) -> dict:
    """Rekonstrukcja 3D dla jednego zakresu wierszy.

    Kluczem jest (plik, mtime, rozmiar, parametry), więc podmiana pliku
    unieważnia wpis sama z siebie — tak samo jak przy wczytywaniu serii.

    To jest odpowiedź na pytanie „czy generować animację raz na segment”:
    liczymy ją LENIWIE, przy pierwszym wejściu na zakładkę 3D, ale wynik
    zostaje tu i w pamięci przeglądarki. Każde kolejne przełączenie na ten
    sam segment nic już nie liczy. Liczenie z góry, przy tworzeniu każdego
    segmentu, kosztowałoby CPU także dla segmentów, których nikt nigdy nie
    obejrzy — a przeglądarka i tak pobiera je w tle zaraz po utworzeniu
    (motion3d.js), więc efekt dla użytkownika jest ten sam.

    maxsize=8: jeden wpis to kilkadziesiąt–kilkaset kB JSON-a.
    """
    recording = motion3d.prepare(Path(path_str))
    return motion3d.build_motion(recording, **dict(params))


def _number(query, name: str, default: float) -> float:
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _flag(query, name: str, default: bool) -> bool:
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    return raw not in ("0", "false", "no")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))

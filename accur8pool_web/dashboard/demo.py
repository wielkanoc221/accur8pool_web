"""Zestaw demonstracyjny — gotowy plik z zaznaczonymi uderzeniami.

Świeżo założone konto jest puste: wykres nie ma czego narysować, panel
segmentów nie ma czego pokazać, a zakładka 3D liczy się dla segmentu,
którego jeszcze nie ma. Demo zamyka tę dziurę — nowy użytkownik dostaje
JEDEN plik z rozrysowanymi uderzeniami i ich fazami, więc widzi działającą
aplikację, zanim wgra cokolwiek własnego.

Demo jest ZWYKŁYM zestawem danych tego użytkownika: własna kopia pliku
w jego drzewie, własne wpisy Segment i SubSegment. Nic tu nie jest tylko
do odczytu — można je poprawiać i usuwać jak każdy inny zestaw, a to, co
z nim zrobi jeden użytkownik, nie dotyka pozostałych. Kosztuje to kopię
pliku na konto, dlatego na demo nadaje się WYCINEK nagrania (kilka
uderzeń), a nie cała sesja treningowa.

Źródłem są dwa pliki w ACCUR8POOL_DEMO_DIR:

    demo.csv    plik JUŻ PRZYGOTOWANY (po transform_raw_df) — kopiuje się
                go wprost do prepared_data, bez liczenia czegokolwiek przy
                zakładaniu konta,
    demo.json   nazwa zestawu i segmenty wraz z fazami.

Oba robi `manage.py export_demo` z zestawu, który ktoś wcześniej opisał
w interfejsie — zaznaczanie uderzeń zostaje tam, gdzie się je widzi, a nie
w ręcznie pisanym JSON-ie. Brak tych plików znaczy „nie ma demo” i jest
w pełni dopuszczalnym stanem: rejestracja działa wtedy jak wcześniej.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction

from .models import Dataset, Segment, SubSegment
from .segments import RowRange
from .storage import DatasetStorage

logger = logging.getLogger(__name__)

# Katalog czytamy przy każdym użyciu przez tę stałą modułową, a nie wprost
# z settings — tak samo jak katalogi danych w views.py, żeby test podmieniał
# go jednym mock.patch.object.
DEMO_DIR = Path(
    getattr(settings, "ACCUR8POOL_DEMO_DIR",
            Path(__file__).resolve().parent / "demo_data")
)

MANIFEST_NAME = "demo.json"
DEFAULT_CSV_NAME = "demo.csv"
DEFAULT_DATASET_NAME = "demo.csv"


class DemoBroken(Exception):
    """Demo jest wgrane, ale nie da się go użyć.

    Odróżnione od zwykłego braku demo (load() zwraca wtedy None), bo to są
    dwie różne sytuacje: brak jest wyborem, uszkodzony manifest to literówka
    do poprawienia. Polecenia CLI mają ją pokazać, rejestracja — przemilczeć
    (patrz install_for).
    """


@dataclass(frozen=True)
class DemoPhase:
    phase: str
    start: int
    end: int


@dataclass(frozen=True)
class DemoSegment:
    start: int
    end: int
    phases: tuple[DemoPhase, ...] = ()


@dataclass(frozen=True)
class Demo:
    """Zawartość katalogu demo: nazwa zestawu, plik i segmenty."""

    name: str
    csv_path: Path
    segments: tuple[DemoSegment, ...] = ()


# ============================================================
#  ODCZYT
# ============================================================

def exists(demo_dir: Path = None) -> bool:
    """Czy w ogóle wgrano demo. Sam test obecności manifestu — bez czytania
    i bez wyjątków, żeby dało się nim taniej odsiać przypadek „nie ma demo”
    zanim ruszy się cokolwiek droższego."""
    return (Path(demo_dir or DEMO_DIR) / MANIFEST_NAME).exists()


def load(demo_dir: Path = None) -> Demo | None:
    """Opis demo albo None, gdy nikt go jeszcze nie wgrał.

    Manifest jest sprawdzany tutaj, w jednym miejscu, a nie przy zakładaniu
    każdego konta: błędny zakres ma się skończyć czytelnym wyjątkiem raz —
    przy `export_demo` albo `install_demo` — a nie tysiącem wpisów w logu.
    """
    demo_dir = Path(demo_dir or DEMO_DIR)
    manifest_path = demo_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoBroken(f"Nie udało się odczytać {manifest_path}: {exc}") from exc

    if not isinstance(manifest, dict):
        raise DemoBroken(f"{manifest_path}: oczekiwano obiektu JSON.")

    csv_path = demo_dir / str(manifest.get("file") or DEFAULT_CSV_NAME)
    if not csv_path.exists():
        raise DemoBroken(f"Brakuje pliku demo: {csv_path}")

    return Demo(
        name=_dataset_name(manifest.get("name")),
        csv_path=csv_path,
        segments=_parse_segments(manifest.get("segments") or []),
    )


def _dataset_name(name) -> str:
    """Nazwa, pod którą zestaw zobaczy użytkownik — zawsze z .csv, bo to ona
    trafia do adresu /dashboard/<filename>/ i na dysk."""
    name = str(name or DEFAULT_DATASET_NAME).strip() or DEFAULT_DATASET_NAME
    return name if name.lower().endswith(".csv") else name + ".csv"


def _parse_segments(raw) -> tuple[DemoSegment, ...]:
    if not isinstance(raw, list):
        raise DemoBroken("Pole 'segments' musi być listą.")
    return tuple(_parse_segment(item, number) for number, item in enumerate(raw, 1))


def _parse_segment(item, number: int) -> DemoSegment:
    if not isinstance(item, dict):
        raise DemoBroken(f"Segment {number}: oczekiwano obiektu JSON.")

    bounds = _parse_range(item, f"Segment {number}")
    return DemoSegment(
        start=bounds.start,
        end=bounds.end,
        phases=tuple(_parse_phase(phase, bounds, number)
                     for phase in item.get("phases") or []),
    )


def _parse_phase(item, segment_bounds: RowRange, number: int) -> DemoPhase:
    if not isinstance(item, dict):
        raise DemoBroken(f"Segment {number}: faza musi być obiektem JSON.")

    phase = item.get("phase")
    if phase not in dict(SubSegment.PHASE_CHOICES):
        raise DemoBroken(f"Segment {number}: nieznana faza {phase!r}.")

    bounds = _parse_range(item, f"Segment {number}, faza {phase}")
    # Ta sama reguła co w API: faza jest częścią uderzenia, więc nie może
    # z niego wystawać (views.SegmentPhaseView.put).
    inside = bounds.clipped_to(segment_bounds)
    if inside is None:
        raise DemoBroken(f"Segment {number}: faza {phase} leży poza segmentem.")

    return DemoPhase(phase=phase, start=inside.start, end=inside.end)


def _parse_range(item: dict, opis: str) -> RowRange:
    bounds, message = RowRange.parse(item)
    if message:
        raise DemoBroken(f"{opis}: {message}")
    return bounds


# ============================================================
#  INSTALACJA NA KONCIE
# ============================================================

def install_for(user, storage: DatasetStorage, demo: Demo = None) -> Dataset | None:
    """Zakłada użytkownikowi kopię demo. Zwraca zestaw albo None.

    Idempotentne przez sprawdzenie nazwy: konto, które demo już ma, dostaje
    None zamiast drugiej kopii. Usunięte demo NIE wraca — kasowanie zestawu
    jest decyzją użytkownika, a nie usterką do naprawienia przy następnym
    logowaniu; dlatego zakładamy je raz, przy tworzeniu konta.

    Cała reszta rejestracji jest ważniejsza niż demo, więc każdy problem
    kończy się wpisem w logu, a nie wywróconym zakładaniem konta: konto bez
    przykładowych danych jest do użycia, brak konta nie.
    """
    try:
        demo = demo or load()
        if demo is None:
            return None
        return _install(user, storage, demo)
    except Exception:
        logger.exception("Nie udało się założyć zestawu demo dla %s", user)
        return None


def _install(user, storage: DatasetStorage, demo: Demo) -> Dataset | None:
    # Pytamy o nazwę PO oczyszczeniu — to ona jest w bazie. Nazwa z manifestu
    # bywa inna (spacje, myślniki), więc porównanie z nią samą znaczyłoby
    # „nie ma demo” przy każdym uruchomieniu i drugą kopię przy każdym
    # install_demo.
    name = storage.canonical_filename(demo.name)
    if Dataset.objects.filter(owner=user, filename=name).exists():
        return None

    filename = storage.free_filename(name)
    path = storage.install_prepared(demo.csv_path, filename)

    try:
        with transaction.atomic():
            dataset = Dataset.objects.create(owner=user, filename=filename)
            _create_segments(dataset, demo.segments)
    except Exception:
        # Plik bez wpisu w bazie byłby zestawem, którego nie widać, a jego
        # nazwa blokowałaby kolejne podejście (DatasetStorage.free_filename).
        path.unlink(missing_ok=True)
        raise

    return dataset


def _create_segments(dataset: Dataset, segments: tuple[DemoSegment, ...]) -> None:
    for spec in segments:
        segment = Segment.objects.create(dataset=dataset,
                                         start=spec.start, end=spec.end)
        SubSegment.objects.bulk_create([
            SubSegment(segment=segment, phase=phase.phase,
                       start=phase.start, end=phase.end)
            for phase in spec.phases
        ])

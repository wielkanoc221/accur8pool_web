"""Przelicza podgląd zestawu demonstracyjnego dla strony głównej.

    python manage.py export_home_preview

Strona główna rysuje wykres z PRAWDZIWEGO zestawu demonstracyjnego, ale nie
czyta w tym celu demo.csv — plik ma kilkanaście tysięcy wierszy, a strona ma
się otwierać natychmiast i nie może zależeć od tego, czy demo w ogóle jest
wgrane. Zamiast tego trzymamy jego wycinek: kilkaset punktów na serię plus
opis uderzeń, wstawiany wprost w HTML.

To polecenie jest jedynym sposobem, w jaki ten wycinek powstaje. Uruchom je
po każdej zmianie zestawu demonstracyjnego (czyli po `export_demo`) —
inaczej strona główna dalej pokazuje poprzednie nagranie.

Dwie rozdzielczości, dokładnie z tego samego powodu, dla którego ma je
aplikacja:

    przebieg   cały plik zdecymowany metodą min–max, żeby zmieścił się na
               ekranie i nie zgubił przy tym krótkich szczytów,
    wycinki    każdy segment osobno, próbkowany równomiernie — po
               przybliżeniu jednego uderzenia z przebiegu zostałoby
               kilkanaście punktów i kształt uderzenia by zniknął.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from dashboard import demo as demo_module

# Serie pokazywane na stronie głównej, w kolejności rysowania. Kolory są
# z colorway wykresu 2D (dashboard/charts.py), żeby ta sama wielkość miała
# na obu ekranach ten sam kolor.
SERIES = [
    ("acc_magnitude", "Przyspieszenie (moduł)", "#2a78d6"),
    ("gyr_magnitude", "Prędkość kątowa (moduł)", "#eb6834"),
    ("roll", "Roll", "#1baf7a"),
]

# Kubełki min–max na cały przebieg. 230 kubełków to do 460 punktów na
# serię — mniej, niż ma pikseli szerokości podgląd, a plik zostaje
# w granicach kilkudziesięciu kilobajtów.
BUCKETS = 230

# Próbki na jeden segment. 72 wystarczą, żeby było widać narastanie,
# szczyt uderzenia i wyhamowanie.
DETAIL_SAMPLES = 72

# Zapas wokół segmentu, jako ułamek jego długości — żeby uderzenie nie
# zaczynało się dokładnie przy krawędzi wykresu.
DETAIL_MARGIN = 0.10

OUTPUT_NAME = "demo_preview.json"


class Command(BaseCommand):
    help = ("Przelicza podgląd zestawu demonstracyjnego pokazywany na "
            "stronie głównej (home/demo_preview.json).")

    def add_arguments(self, parser):
        parser.add_argument("--output", dest="output",
                            help="Ścieżka pliku wynikowego. Domyślnie "
                                 f"home/{OUTPUT_NAME}.")

    def handle(self, *args, **options):
        try:
            demo = demo_module.load()
        except demo_module.DemoBroken as exc:
            raise CommandError(str(exc)) from exc

        if demo is None:
            raise CommandError(
                "Nie ma zestawu demonstracyjnego — nie ma z czego zrobić "
                "podglądu. Najpierw `manage.py export_demo`.")

        rows, columns, values = self._read(demo.csv_path)
        payload = {
            "name": demo.name,
            "rows": rows,
            "columns": columns,
            "series": self._overview(values),
            "segments": self._segments(demo, values, rows),
        }

        output = Path(options.get("output")
                      or Path(__file__).resolve().parents[2] / OUTPUT_NAME)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")

        if options.get("verbosity", 1):
            self.stdout.write(self.style.SUCCESS(
                f"Zapisano {output} — {rows} wierszy, "
                f"{len(payload['segments'])} uderzeń, "
                f"{output.stat().st_size // 1024} kB."))

    # ------------------------------------------------------------
    #  ODCZYT
    # ------------------------------------------------------------

    def _read(self, path: Path):
        """Wybrane kolumny znormalizowane do 0–1 — tak samo jak Series.

        Czytamy modułem `csv`, a nie pandasem: potrzebne są trzy kolumny
        z jednego pliku, więc wczytywanie całej ramki byłoby nadmiarem,
        a polecenie ma działać także tam, gdzie pandas nie jest pod ręką.
        """
        with Path(path).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise CommandError(f"{path} jest pusty.")

            missing = [name for name, _, _ in SERIES if name not in header]
            if missing:
                raise CommandError(
                    f"{path}: brakuje kolumn {', '.join(missing)}. To ma być "
                    f"plik PRZYGOTOWANY, a nie surowy zapis z czujnika.")

            index = {name: header.index(name) for name, _, _ in SERIES}
            columns = {name: [] for name in index}
            rows = 0

            for row in reader:
                rows += 1
                for name, position in index.items():
                    columns[name].append(_number(row, position))

        if rows < 2:
            raise CommandError(f"{path}: za mało wierszy na podgląd.")

        return rows, len(header), {name: _normalized(values)
                                   for name, values in columns.items()}

    # ------------------------------------------------------------
    #  PRZELICZENIE
    # ------------------------------------------------------------

    def _overview(self, values):
        return [{
            "name": name,
            "label": label,
            "color": color,
            "points": [[position, round(value, 3)]
                       for position, value in _decimated(values[name], BUCKETS)],
        } for name, label, color in SERIES]

    def _segments(self, demo, values, rows):
        out = []
        for segment in demo.segments:
            margin = max(int((segment.end - segment.start) * DETAIL_MARGIN), 1)
            lo = max(segment.start - margin, 0)
            hi = min(segment.end + margin, rows - 1)

            out.append({
                "start": segment.start,
                "end": segment.end,
                "phases": [{"phase": phase.phase,
                            "start": phase.start,
                            "end": phase.end}
                           for phase in segment.phases],
                "detail": {
                    "lo": lo,
                    "hi": hi,
                    # Same wartości — indeksy odtwarza się z lo/hi, bo
                    # próbki są równomierne. Zapisanie ich razem z liczbami
                    # podwoiłoby rozmiar pliku bez żadnej nowej informacji.
                    "values": {name: _sampled(values[name], lo, hi,
                                              DETAIL_SAMPLES)
                               for name, _, _ in SERIES},
                },
            })
        return out


# ============================================================
#  LICZENIE
# ============================================================

def _number(row, position):
    try:
        return float(row[position])
    except (ValueError, IndexError):
        return math.nan


def _normalized(values):
    """Kolumna przeskalowana do 0–1 — dokładnie ta operacja, którą robi
    Series przed narysowaniem wykresu 2D. Dzięki temu podgląd wygląda tak
    samo jak prawdziwy dashboard."""
    present = [value for value in values if not math.isnan(value)]
    if not present:
        return [None] * len(values)

    low, high = min(present), max(present)
    span = high - low or 1.0
    return [None if math.isnan(value) else (value - low) / span
            for value in values]


def _decimated(values, buckets):
    """Min i max z każdego kubełka, w kolejności występowania.

    Ta sama zasada, co w series.minmax_indices: średnia z kubełka zjadłaby
    szczyt uderzenia, który trwa kilka próbek.
    """
    step = len(values) / buckets
    picked = []

    for bucket in range(buckets):
        start = int(bucket * step)
        stop = min(max(int((bucket + 1) * step), start + 1), len(values))
        chunk = [(position, values[position]) for position in range(start, stop)
                 if values[position] is not None]
        if not chunk:
            continue

        low = min(chunk, key=lambda point: point[1])
        high = max(chunk, key=lambda point: point[1])
        picked.extend(sorted({low, high}, key=lambda point: point[0]))

    return picked


def _sampled(values, lo, hi, count):
    """Równomierne próbki z zakresu [lo, hi]."""
    last = len(values) - 1
    out = []

    for step in range(count):
        position = min(max(lo + round((hi - lo) * step / (count - 1)), 0), last)
        value = values[position]
        out.append(None if value is None else round(value, 3))

    return out

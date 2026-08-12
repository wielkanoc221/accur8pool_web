"""Robi zestaw demonstracyjny z zestawu, który ktoś opisał w interfejsie.

    python manage.py export_demo ruch.csv --user ala --name "Demo — uderzenia"

Uderzenia i fazy zaznacza się na wykresie, a nie w pliku JSON: numery
wierszy dobiera się patrząc na sygnał. To polecenie jest mostem między
jednym a drugim — bierze GOTOWY zestaw wskazanego konta i zapisuje go
w katalogu demo (przygotowany CSV + demo.json z segmentami), skąd trafi na
każde nowo zakładane konto.

Kopiowany jest plik PRZYGOTOWANY, ten sam, który czyta aplikacja — dzięki
temu zakładanie konta nie uruchamia transformacji, a numery wierszy
w segmentach znaczą u nowego użytkownika dokładnie to samo, co u tego,
który je zaznaczał.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ... import demo
from ...models import Dataset
from ...segments import ordered_phases
from ...storage import count_records
from ...views import storage_for


class Command(BaseCommand):
    help = ("Zapisuje wskazany zestaw danych jako zestaw demonstracyjny "
            "dla nowych użytkowników.")

    def add_arguments(self, parser):
        parser.add_argument("filename",
                            help="Nazwa pliku zestawu, np. 'ruch.csv'.")
        parser.add_argument("--user", dest="username",
                            help="Login właściciela zestawu. Można pominąć, "
                                 "gdy plik o tej nazwie ma tylko jedno konto.")
        parser.add_argument("--name", dest="name",
                            help="Nazwa, pod którą demo zobaczą użytkownicy "
                                 f"(domyślnie {demo.DEFAULT_DATASET_NAME}).")
        parser.add_argument("--demo-dir", dest="demo_dir",
                            help="Katalog docelowy (domyślnie ACCUR8POOL_DEMO_DIR).")

    def handle(self, *args, **options):
        dataset = self._find_dataset(options["filename"], options["username"])
        source = storage_for(dataset.owner).path_for(dataset)
        if not source.exists():
            raise CommandError(f"Brak przygotowanej wersji pliku: {source}")

        demo_dir = Path(options["demo_dir"] or demo.DEMO_DIR)
        demo_dir.mkdir(parents=True, exist_ok=True)

        csv_path = demo_dir / demo.DEFAULT_CSV_NAME
        shutil.copyfile(source, csv_path)

        manifest = {
            "name": options["name"] or demo.DEFAULT_DATASET_NAME,
            "file": csv_path.name,
            "segments": _segments_manifest(dataset),
        }
        manifest_path = demo_dir / demo.MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

        # Odczytanie zapisanego demo z powrotem jest tanie, a wyłapuje tu —
        # przy jednym uruchomieniu — to, co inaczej wychodziłoby przy
        # zakładaniu kont, gdzie problemy z demo są tylko logowane.
        demo.load(demo_dir)

        self._report(manifest, csv_path, manifest_path, source)

    def _find_dataset(self, filename: str, username: str | None) -> Dataset:
        datasets = Dataset.objects.filter(filename=filename)
        if username:
            datasets = datasets.filter(owner__username=username)

        found = list(datasets[:2])
        if not found:
            raise CommandError(f"Nie znaleziono zestawu {filename!r}"
                               + (f" na koncie {username!r}." if username else "."))
        if len(found) > 1:
            raise CommandError(f"Zestaw {filename!r} ma kilku właścicieli — "
                               f"wskaż konto przez --user.")
        return found[0]

    def _report(self, manifest: dict, csv_path: Path, manifest_path: Path,
                source: Path) -> None:
        phases = sum(len(segment["phases"]) for segment in manifest["segments"])
        self.stdout.write(self.style.SUCCESS(
            f"Zapisano demo {manifest['name']!r}: "
            f"{len(manifest['segments'])} segmentów, {phases} faz, "
            f"{count_records(source)} wierszy."))
        self.stdout.write(f"  {csv_path}")
        self.stdout.write(f"  {manifest_path}")
        self.stdout.write("Nowe konta dostaną je automatycznie; istniejące — "
                          "poleceniem `manage.py install_demo`.")


def _segments_manifest(dataset: Dataset) -> list:
    """Segmenty w postaci, którą czyta demo.load().

    Kolejność bierze się z Meta.ordering modelu (po pozycji na osi czasu),
    a fazy — z ordered_phases, czyli tak samo jak w panelu. Numeracji nie
    zapisujemy: liczy się ją przy odczycie, po kolejności.
    """
    return [
        {
            "start": segment.start,
            "end": segment.end,
            "phases": [
                {"phase": sub.phase, "start": sub.start, "end": sub.end}
                for sub in ordered_phases(segment)
            ],
        }
        for segment in dataset.segments.prefetch_related("subsegments")
    ]

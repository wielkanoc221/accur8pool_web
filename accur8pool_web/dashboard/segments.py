"""Segmenty (uderzenia) i ich fazy — logika niezależna od HTTP.

Struktura: Dataset → Segment (uderzenie) → SubSegment (faza). Granice
wszędzie to numery wierszy CSV, tak samo jak oś X wykresu.

Widoki zajmują się tu wyłącznie tłumaczeniem na JSON i kody odpowiedzi;
reguły — zaokrąglanie zaznaczenia, przycinanie faz do uderzenia, kolejność
faz w panelu — siedzą w tym module i dają się sprawdzić bez żądania.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Segment, SubSegment

# Górna granica numeru wiersza przyjmowanego w granicach segmentu.
# To zakres PositiveIntegerField — bez tego sprawdzenia zbłąkane Infinity
# z JS-a przechodziłoby aż do bazy.
MAX_ROW_INDEX = 2_147_483_647


@dataclass(frozen=True)
class RowRange:
    """Zakres wierszy [start, end) — tak, jak zaznaczono go na wykresie."""

    start: int
    end: int

    @classmethod
    def parse(cls, payload: dict):
        """Waliduje {"start": .., "end": ..} → (RowRange, None) albo (None, błąd).

        Z przeglądarki przychodzą liczby zmiennoprzecinkowe (granice
        zaznaczenia na wykresie rzadko wypadają dokładnie na wierszu), więc
        zaokrąglamy „na zewnątrz”: start w dół, end w górę. Zaznaczenie
        zrobione od prawej do lewej ma start > end i po prostu je
        zamieniamy — dla użytkownika kierunek przeciągania nie powinien
        mieć znaczenia.
        """
        try:
            start = float(payload["start"])
            end = float(payload["end"])
        except (KeyError, TypeError, ValueError):
            return None, "Wymagane są liczbowe pola 'start' i 'end'."

        if not (math.isfinite(start) and math.isfinite(end)):
            return None, "Granice zakresu muszą być skończonymi liczbami."

        first = max(int(math.floor(min(start, end))), 0)
        last = int(math.ceil(max(start, end)))

        if last <= first:
            return None, "Zakres musi obejmować co najmniej jeden wiersz."
        if last > MAX_ROW_INDEX:
            return None, "Zakres wykracza poza dopuszczalny numer wiersza."

        return cls(first, last), None

    def clipped_to(self, other: "RowRange") -> "RowRange | None":
        """Część wspólna z innym zakresem albo None, gdy się nie stykają."""
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return RowRange(start, end) if end > start else None


def segment_range(segment: Segment) -> RowRange:
    return RowRange(segment.start, segment.end)


# ============================================================
#  ODCZYT DLA PRZEGLĄDARKI
# ============================================================

def segments_payload(dataset) -> list:
    """Segmenty datasetu z fazami, gotowe do wysłania jako JSON.

    Numer segmentu NIE jest w bazie — bierze się z kolejności na osi czasu
    (patrz docstring modelu Segment), więc liczymy go przy każdym odczycie.
    """
    segments = Segment.objects.filter(dataset=dataset).prefetch_related("subsegments")

    return [
        {
            "id": segment.pk,
            "number": number,
            "name": str(number),
            "start": segment.start,
            "end": segment.end,
            "length": segment.length,
            "phases": [
                {
                    "id": sub.pk,
                    "phase": sub.phase,
                    "label": sub.get_phase_display(),
                    "start": sub.start,
                    "end": sub.end,
                    "length": sub.length,
                }
                for sub in ordered_phases(segment)
            ],
        }
        for number, segment in enumerate(segments, start=1)
    ]


def phase_types_payload() -> list:
    """Nazwy i kolory faz. Idą razem z danymi, żeby przeglądarka nie musiała
    trzymać własnej kopii — modele są jedynym źródłem prawdy, wspólnym
    z animacją 3D."""
    return [
        {"key": key, "label": label, "color": SubSegment.PHASE_COLORS.get(key)}
        for key, label in SubSegment.PHASE_CHOICES
    ]


def ordered_phases(segment: Segment) -> list:
    """Fazy w ich naturalnej kolejności w uderzeniu, nie w zaznaczonej.

    Panel ma pokazywać przygotowanie → przymierzanie → uderzenie → po
    uderzeniu również wtedy, gdy zostały zaznaczone w innej kolejności albo
    gdy jedna z nich jest jeszcze pusta.
    """
    rank = {key: index for index, key in enumerate(SubSegment.PHASE_ORDER)}
    return sorted(segment.subsegments.all(),
                  key=lambda sub: rank.get(sub.phase, len(rank)))


def phase_tuples(segment: Segment) -> tuple:
    """Fazy w formacie, którego oczekuje motion3d.scene.phase_spans:
    (klucz, etykieta, kolor, start, koniec).

    Krotka, a nie lista, bo trafia do klucza cache animacji — poprawienie
    zakresu fazy ma unieważnić policzoną wcześniej scenę.
    """
    return tuple(
        (
            sub.phase,
            sub.get_phase_display(),
            SubSegment.PHASE_COLORS.get(sub.phase),
            sub.start,
            sub.end,
        )
        for sub in ordered_phases(segment)
    )


# ============================================================
#  ZMIANY
# ============================================================

def trim_phases_to_segment(segment: Segment) -> None:
    """Przycina fazy do granic uderzenia po zmianie jego zakresu.

    Zawężenie segmentu mogło wypchnąć fazy poza jego granice. Bez tego na
    wykresie zostałyby prostokąty faz wystające poza uderzenie, do którego
    należą. Faza, która wypadła całkowicie, znika.
    """
    bounds = segment_range(segment)

    for sub in segment.subsegments.all():
        trimmed = RowRange(sub.start, sub.end).clipped_to(bounds)

        if trimmed is None:
            sub.delete()
        elif (trimmed.start, trimmed.end) != (sub.start, sub.end):
            sub.start, sub.end = trimmed.start, trimmed.end
            sub.save(update_fields=["start", "end"])

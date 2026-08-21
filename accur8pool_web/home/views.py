"""Strona główna — jedyne wejście do aplikacji dla kogoś, kto jej nie zna.

Do tej pory goła domena przekierowywała wprost na dashboard, więc pierwszym
ekranem aplikacji był formularz logowania: bez słowa o tym, czym to jest,
jakich danych oczekuje i co się z nimi da zrobić. Ta strona zamyka tę dziurę
— opisuje projekt, pokazuje działający podgląd dashboardu i listy plików,
i dopiero stąd prowadzi do logowania albo rejestracji.

Widok jest CELOWO bezstanowy i publiczny: nie dotyka bazy, nie wymaga
logowania i nie zapisuje niczego. Jedyne dane, jakie wpuszcza do szablonu,
to podgląd zestawu demonstracyjnego wycięty raz z demo.csv (patrz
demo_preview.json) — dzięki temu wykres na stronie głównej rysuje PRAWDZIWY
przebieg z prawdziwymi uderzeniami, a nie wymyśloną krzywą, i nie kosztuje
przy tym ani jednego odczytu wielomegabajtowego CSV na żądanie.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from django.shortcuts import render

logger = logging.getLogger(__name__)

# Plik leży w katalogu aplikacji, a NIE w static/: jest wstawiany wprost
# w HTML przez {{ ...|json_script }}, więc nikt go nigdy nie pobiera
# osobnym żądaniem. Trzymanie go w static/ obiecywałoby adres, którego
# strona i tak nie używa, a w konfiguracji z manifestem (collectstatic)
# dokładałoby drugi tryb awarii do rzeczy, która ma tylko zdobić.
PREVIEW_PATH = Path(__file__).resolve().parent / "demo_preview.json"


@lru_cache(maxsize=1)
def demo_preview() -> dict | None:
    """Podgląd zestawu demo albo None, gdy pliku nie ma lub jest zepsuty.

    Brak podglądu nie jest awarią strony: sekcja demo pokazuje wtedy
    zaproszenie do założenia konta zamiast wykresu. Dlatego wyjątek kończy
    się wpisem w logu, a nie pięćsetką na stronie, która ma być pierwszym,
    co ktoś zobaczy.

    Wynik jest zapamiętany na proces — treść zmienia się wyłącznie razem
    z wdrożeniem, więc czytanie go przy każdym wejściu byłoby czystym
    kosztem.
    """
    try:
        preview = json.loads(PREVIEW_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Nie udało się wczytać podglądu demo: %s", PREVIEW_PATH)
        return None

    return preview if isinstance(preview, dict) else None


def _spaced(number) -> str | None:
    """Liczba z odstępem nierozdzielającym co trzy cyfry albo None."""
    if number is None:
        return None
    return f"{number:,}".replace(",", "\u00a0")


def home(request):
    """Strona główna — ta sama dla gościa i dla zalogowanego.

    Zalogowany NIE jest stąd przekierowywany na dashboard. Instrukcja
    obsługi jest potrzebna także po założeniu konta (a właściwie zwłaszcza
    wtedy), więc adres, pod którym leży, musi dać się otworzyć w każdej
    chwili. Różnicę robi sam przycisk w nagłówku: gość dostaje „Zaloguj
    się”, zalogowany — „Otwórz dashboard”.
    """
    preview = demo_preview()

    return render(request, "home.html", {
        "preview": preview,
        # Liczby z podglądu wchodzą do treści strony osobno, żeby dało się
        # je pokazać w HTML-u bez czekania na JavaScript — i żeby nie było
        # dwóch wersji tej samej liczby: opisowej w tekście i prawdziwej
        # na wykresie.
        "demo_rows": (preview or {}).get("rows"),
        # Ta sama liczba w postaci do czytania: 15 354, nie 15354. Wprost
        # z widoku, bo django.contrib.humanize nie jest w INSTALLED_APPS,
        # a strona główna nie jest powodem, żeby go tam dokładać.
        "demo_rows_label": _spaced((preview or {}).get("rows")),
        "demo_columns": (preview or {}).get("columns"),
        "demo_segments": len((preview or {}).get("segments") or []),
    })

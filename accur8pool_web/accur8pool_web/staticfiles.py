"""Backend plików statycznych, który przeżywa brak `collectstatic`.

Wersja z manifestem wpisuje w nazwę pliku skrót jego treści
(styles.a1b2c3.css). To jedyny sposób, żeby kazać przeglądarce trzymać
arkusz w pamięci podręcznej na rok i mimo to pokazać nową wersję zaraz po
wdrożeniu — dlatego na serwerze jest włączona.

Skrót bierze się jednak z katalogu STATIC_ROOT i ze spisu
(staticfiles.json), a jedno i drugie powstaje dopiero przy
`collectstatic`. Wszędzie tam, gdzie tego kroku nie ma — w testach, przy
`runserver`, przy `manage.py` uruchamianym prosto z repozytorium —
standardowy backend przewraca KAŻDE renderowanie szablonu, który używa
`{% static %}`:

    ValueError: Missing staticfiles manifest entry for 'css/styles.css'

czyli konfiguracja produkcyjna psuje testy, choć z samą aplikacją nie ma
nic nie tak.

Ta klasa zamienia obie twarde awarie w cichy odwrót do nazwy
nieskrótowanej: jest zebrany komplet plików — są adresy z odciskiem, nie
ma go — są zwykłe adresy i wszystko działa dalej.

Odwrót nie ukrywa błędnego wdrożenia. Obraz kontenera wykonuje
`collectstatic` w kroku budowania, więc na serwerze skróty są; gdyby
plików tam nie było, brak odcisku w adresie byłby najmniejszym problemem
— nie miałoby czego serwować.
"""

from whitenoise.storage import CompressedManifestStaticFilesStorage


class ResilientManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    # Nazwa spoza spisu nie jest błędem — schodzimy wtedy do hashed_name(),
    # które poniżej też ma swój odwrót.
    manifest_strict = False

    def hashed_name(self, name, content=None, filename=None):
        """Nazwa ze skrótem, a gdy nie ma z czego jej policzyć — bez skrótu.

        `super()` czyta plik, żeby policzyć skrót, i rzuca ValueError, gdy
        pliku nie ma w STATIC_ROOT. W trakcie `collectstatic` plik jest
        zawsze (właśnie się go kopiuje), więc na tej ścieżce zachowanie się
        nie zmienia — odwrót działa tylko tam, gdzie zbierania nie było.
        """
        try:
            return super().hashed_name(name, content, filename)
        except ValueError:
            return name

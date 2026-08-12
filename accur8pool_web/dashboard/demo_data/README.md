# Zestaw demonstracyjny

Ten katalog trzyma JEDEN przykładowy zestaw danych, który dostaje **każde
nowo zakładane konto** — żeby po rejestracji było co obejrzeć, zanim
użytkownik wgra własne nagranie.

Kopia jest osobna dla każdego użytkownika i jest zwykłym zestawem: da się
ją poprawiać i usuwać jak każdą inną. Dlatego na demo nadaje się **wycinek
nagrania** (kilka uderzeń), a nie cała sesja treningowa — plik mnoży się
przez liczbę kont.

## Zawartość

| plik        | co to jest                                                       |
|-------------|------------------------------------------------------------------|
| `demo.csv`  | plik **przygotowany** (po `transform_raw_df`), taki, jaki czyta aplikacja |
| `demo.json` | nazwa zestawu oraz segmenty (uderzenia) wraz z fazami            |

Brak `demo.json` znaczy „nie ma demo” — rejestracja działa wtedy normalnie,
konto powstaje puste.

## Jak przygotować demo

Uderzeń nie opisuje się ręcznie w JSON-ie. Zaznacza się je tam, gdzie się je
widzi — na wykresie — a potem eksportuje:

1. Zaloguj się na dowolne konto (może być własne konto robocze) i wgraj
   wybrany plik CSV.
2. Na wykresie zaznacz segmenty (uderzenia) i ich fazy tak, jak mają
   wyglądać w demo.
3. Wyeksportuj ten zestaw do tego katalogu:

   ```bash
   python manage.py export_demo ruch.csv --user ala --name "Demo — uderzenia"
   ```

   Polecenie kopiuje przygotowany CSV jako `demo.csv` i zapisuje `demo.json`
   z segmentami. Oba pliki wchodzą do repozytorium.

4. Konta zakładane od tej pory dostaną demo automatycznie. Istniejące konta
   obsłuż raz:

   ```bash
   python manage.py install_demo          # wszystkie konta bez demo
   python manage.py install_demo ala ola  # tylko wskazane
   ```

## Format `demo.json`

```json
{
  "name": "Demo — uderzenia.csv",
  "file": "demo.csv",
  "segments": [
    {
      "start": 1200,
      "end": 2400,
      "phases": [
        {"phase": "przygotowanie", "start": 1200, "end": 1500},
        {"phase": "uderzenie",     "start": 1500, "end": 1700}
      ]
    }
  ]
}
```

Granice to **numery wierszy CSV** — dokładnie to samo, co oś X wykresu.
Nazwy faz muszą pochodzić z `SubSegment.PHASE_CHOICES`
(`przygotowanie`, `przymierzanie`, `uderzenie`, `po_uderzeniu`).
Faza nie może wystawać poza swój segment; wystająca jest przycinana przy
odczycie, a leżąca całkiem poza nim — odrzucana z błędem.

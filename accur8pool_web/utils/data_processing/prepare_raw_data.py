"""Przygotowanie surowego zapisu z zegarka do postaci, na której się pracuje.

Jedno wejście (surowy CSV z czujników) i jedno wyjście (ten sam plik
z dołożonymi kolumnami pochodnymi: przefiltrowane sygnały, magnitudy,
jerk, roll i pitch). Aplikacja webowa czyta WYŁĄCZNIE wersję
przygotowaną, więc plik, którego nie da się tu przepuścić, w ogóle do
niej nie wchodzi.

Moduł ma dwie drogi wejścia:
  • prepare_raw_file_and_save — jeden plik; tędy idzie upload w aplikacji,
  • prepare_raw_data_and_save — cała partia plików z dysku (CLI na dole).

Wyjątki są rozdzielone po ETAPIE, na którym coś padło, bo w partii plików
różnica między „nie dało się odczytać” a „brakuje kolumn” decyduje o tym,
co powiedzieć użytkownikowi.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from pandas import DataFrame

from .const import (
    ACC_MAGNITUDE,
    ACC_X,
    ACC_Y,
    ACC_Z,
    GYR_MAGNITUDE,
    GYR_X,
    GYR_Y,
    GYR_Z,
    TIMESTAMP,
)
from .data_transformations import DataFrameTransformerBase, DataFrameTransformerV2


class FileReadException(Exception):
    """Pliku nie dało się wczytać jako CSV."""


class TransformException(Exception):
    """Transformacja przewróciła się w środku."""


class SaveException(Exception):
    """Wyniku nie dało się zapisać."""


class WrongColumnsException(Exception):
    """Zapis nie ma kolumn, bez których nie da się policzyć NICZEGO.

    To jedyny wyjątek, którego treść trafia wprost do użytkownika — niesie
    nazwy brakujących kolumn.
    """


# Bez tych kolumn transformacja nie ma z czego policzyć NICZEGO: magnitudy,
# jerk, roll i pitch biorą się z acc* i gyr*, a oś czasu z timestamp.
# Reszta czujników (magnetometr, linacc, wektor obrotu) jest opcjonalna —
# zapisy różnią się między wersjami zegarka i brak jednego z nich nie ma
# prawa unieważnić całego pliku.
REQUIRED_COLUMNS = (ACC_X, ACC_Y, ACC_Z, GYR_X, GYR_Y, GYR_Z, TIMESTAMP)

# Akcelerometry: granica 10 Hz. Powyżej niej w zapisie z nadgarstka nie ma
# już ruchu, tylko drgania czujnika i kwantyzacja.
COLUMNS_TO_FILTER_10_CUT_OFF = ["accx", "accy", "accz",
                                "linaccx", "linaccy", "linaccz"]

# Żyroskop i magnetometr: 5 Hz, bo są wyraźnie bardziej zaszumione.
#
# rot* CELOWO NIE MA na żadnej z tych list, choć kiedyś było.
#
# rotx/roty/rotz to trzy składowe KWATERNIONU, wiązane warunkiem |q| = 1
# razem z rotw. Filtr o tym warunku nie wie: przepuszczany składowa po
# składowej rozjeżdżał rot* z nietkniętym rotw, a filtfilt na Butterworcie
# przestrzeliwuje na szybkim zboczu, więc |rot| potrafiło wyjść poza 1 —
# czyli poza sinus połowy kąta, którego nie da się zinterpretować inaczej
# niż jako uszkodzenie. Rekonstrukcja orientacji w motion3d odrzucała wtedy
# dobrą kolumnę rotw i odtwarzała znak czwartej składowej z kinematyki.
#
# To był jedyny powód, dla którego animacja 3D musiała sięgać po plik
# surowy. Wektor obrotu jest wyjściem fuzji czujników, więc jest już
# wygładzony u źródła i nie ma czego z niego obcinać.
COLUMNS_TO_FILTER_5_CUT_OFF = ["gyrx", "gyry", "gyrz", "magx", "magy", "magz"]


def missing_required_columns(df: DataFrame) -> list[str]:
    """Kolumny z REQUIRED_COLUMNS, których w ramce nie ma — w kolejności
    z REQUIRED_COLUMNS, żeby komunikat dla użytkownika był powtarzalny."""
    present = set(df.columns)
    return [column for column in REQUIRED_COLUMNS if column not in present]


def read_csv(path) -> DataFrame:
    try:
        try:
            return pd.read_csv(path, engine="pyarrow")
        except ImportError:
            # pyarrow jest tylko przyspieszaczem — bez niego czytamy
            # domyślnym silnikiem pandas zamiast wywracać cały import.
            return pd.read_csv(path)
    except Exception as exc:
        raise FileReadException(exc)


def transform_raw_df(df: DataFrame) -> DataFrame:
    """Dokłada do ramki kolumny pochodne. Kolejność kroków jest istotna.

    add_time() idzie PRZED filtrami, a nie po nich. Filtr projektuje się
    względem częstotliwości próbkowania, a tę da się odczytać dopiero
    z gotowej osi czasu (DataFrameTransformerBase.sampling_rate). Wcześniej
    fs było wpisane na sztywno jako 100 Hz, więc przy zapisie 400 Hz
    deklarowana granica 10 Hz wychodziła w rzeczywistości 40 Hz. Na wynik
    pozostałych kroków kolejność nie wpływa — magnitudy nie zależą od
    czasu, a jerk i tak potrzebuje osi czasu i sam by ją dołożył.
    """
    try:
        transformer = _transformer_for(df)

        return (
            transformer(df)
            .dt_ms_to_sec()
            .add_time()
            .lowpass(columns=_present(df, COLUMNS_TO_FILTER_10_CUT_OFF), cutoff=10)
            .lowpass(columns=_present(df, COLUMNS_TO_FILTER_5_CUT_OFF), cutoff=5)
            .add_magnitude([ACC_X, ACC_Y, ACC_Z], ACC_MAGNITUDE)
            .add_magnitude([GYR_X, GYR_Y, GYR_Z], GYR_MAGNITUDE)
            .add_jerk([ACC_X, ACC_Y, ACC_Z], prefix="acc")
            .add_jerk([GYR_X, GYR_Y, GYR_Z], prefix="gyr")
            .add_roll()
            .add_pitch()
            .result()
        )
    except Exception as exc:
        raise TransformException(exc)


def _transformer_for(df: DataFrame):
    """Kolumna `csv_version` znaczy nowszy format zapisu — inny sens
    znacznika czasu i roll/pitch prosto z urządzenia."""
    return DataFrameTransformerV2 if "csv_version" in df.columns else DataFrameTransformerBase


def _present(df: DataFrame, columns: list[str]) -> list[str]:
    """Te z podanych kolumn, które faktycznie są w pliku.

    Listy do filtrowania opisują KOMPLET czujników, ale nie każdy zapis
    niesie magnetometr, linacc czy wektor obrotu. Bez tego przesiania brak
    jednej kolumny kończy się KeyError i cały plik zostaje bez wersji
    przygotowanej. Komplet naprawdę niezbędny opisuje REQUIRED_COLUMNS.
    """
    return [column for column in columns if column in df.columns]


def save_data(df: DataFrame, output_dir, filename: str) -> None:
    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_dir / filename, index=False)
    except Exception as exc:
        raise SaveException(exc)


def prepare_raw_file_and_save(input_path: Path, output_dir: Path,
                              filename: str = None) -> Path:
    """Przygotowuje JEDEN surowy plik i zapisuje go w output_dir.

    Tej samej ścieżki (odczyt → sprawdzenie kolumn → transformacja → zapis)
    używa upload w aplikacji webowej, gdzie plik przychodzi pojedynczo.
    Wyjątki lecą dalej — o tym, czy błąd tylko logujemy, czy przerywa
    całość, decyduje wołający.

    Komplet kolumn sprawdzamy TUTAJ, przed transformacją, bo w aplikacji
    webowej to jedyny moment, w którym da się powiedzieć użytkownikowi coś
    konkretnego: bez wersji przygotowanej plik nie wchodzi do systemu
    w ogóle, więc komunikat „brakuje kolumn accx, accy” jest jedyną
    informacją, jaką dostanie. Wyjątek z głębi transformacji niesie
    najwyżej KeyError z nazwą jednej kolumny.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    filename = filename or input_path.name

    df = read_csv(input_path)

    missing = missing_required_columns(df)
    if missing:
        raise WrongColumnsException("Brakuje wymaganych kolumn: " + ", ".join(missing))

    # Nazwa pliku źródłowego zostaje w danych — po scaleniu kilku nagrań
    # w jedną ramkę to jedyne, co mówi, z którego zapisu pochodzi wiersz.
    df["session_index"] = input_path.stem

    save_data(transform_raw_df(df), output_dir, filename)
    return output_dir / filename


def prepare_raw_data_and_save(input_paths: list[Path], output_dir: Path) -> None:
    """Cała partia plików. Błąd jednego pliku nie przerywa reszty — przy
    kilkuset nagraniach przerwanie na pierwszym uszkodzonym znaczyłoby
    zaczynanie od początku."""
    print(f"input_files: {len(input_paths)}")
    output_dir.mkdir(exist_ok=True, parents=True)

    for index, path in enumerate(input_paths, start=1):
        print(index, "/", len(input_paths))
        problem = _prepare_one_reporting_errors(path, output_dir)
        print(problem if problem else f"OK transformacja {path}")


def _prepare_one_reporting_errors(path: Path, output_dir: Path) -> str | None:
    """Zwraca opis problemu albo None, gdy plik przeszedł."""
    try:
        prepare_raw_file_and_save(path, output_dir)
    except FileReadException as exc:
        return f"ERROR blad odczytu pliku {path} {exc} "
    except WrongColumnsException as exc:
        return f"ERROR niepelny zestaw kolumn w pliku {path}: {exc}"
    except TransformException as exc:
        return f"ERROR blad transformacji pliku {path} {exc}"
    except SaveException as exc:
        return f"ERROR blad zapisu pliku {path} {exc} "
    except Exception as exc:
        return f"ERROR nieznany blad {exc}"
    return None


def get_csv_paths(input_dir) -> list[Path]:
    return list(Path(input_dir).rglob("*.csv"))


# ============================================================
#  URUCHAMIANIE Z KONSOLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Przygotowuje surowe zapisy .csv z zegarka.")
    parser.add_argument("--input_files", nargs="*", default=None,
                        help="Ścieżki surowych danych .csv")
    parser.add_argument("--input_dir", default=None,
                        help="Katalog z surowymi danymi .csv")
    parser.add_argument("--output_dir", required=True,
                        help="Folder zapisu przygotowanych plików")
    return parser.parse_args()


def input_paths_from_args(args) -> list[Path]:
    """Pliki wprost albo cały katalog — ale nie jedno i drugie naraz."""
    if args.input_dir and args.input_files:
        raise ValueError("Podaj albo input_dir albo input_files, nie oba")
    if args.input_dir:
        return list(Path(args.input_dir).glob("*.csv"))
    if args.input_files:
        return [Path(path) for path in args.input_files]
    raise ValueError("Musisz podać input_dir albo input_files")


def main() -> None:
    args = parse_args()
    try:
        prepare_raw_data_and_save(input_paths_from_args(args), Path(args.output_dir))
    except Exception as exc:
        print(exc)
    finally:
        # Skrypt bywa uruchamiany podwójnym kliknięciem w Windows — bez tego
        # okno konsoli znika razem z komunikatem o błędzie.
        input("exit...")


if __name__ == "__main__":
    main()

import argparse
from pathlib import Path
import pandas as pd
from pandas import DataFrame
from .const import *

from .data_transformations import DataFrameTransformerBase, DataFrameTransformerV2


class FileReadException(Exception):
    pass


class TransformException(Exception):
    pass


class SaveException(Exception):
    pass


class WrongColumnsException(Exception):
    pass


def transform_raw_df(df: DataFrame) -> pd.DataFrame:
    try:
        COLUMNS_TO_FILTER_10_CUT_OFF = ['accx', 'accy', 'accz', 'linaccx', 'linaccy', 'linaccz', ]
        COLUMNS_TO_FILTER_5_CUT_OFF = ['rotx', 'roty', 'rotz', 'gyrx', 'gyry', 'gyrz', 'magx', 'magy', 'magz']
        if 'csv_version' in df.columns:
            transformer = DataFrameTransformerV2

        else:
            transformer = DataFrameTransformerBase
        return (
            transformer(df)
            .dt_ms_to_sec()
            .lowpass(columns=COLUMNS_TO_FILTER_10_CUT_OFF, cutoff=10)
            .lowpass(columns=COLUMNS_TO_FILTER_5_CUT_OFF, cutoff=5)
            .add_magnitude([ACC_X, ACC_Y, ACC_Z], ACC_MAGNITUDE)
            .add_magnitude([GYR_X, GYR_Y, GYR_Z], GYR_MAGNITUDE)
            .add_time()
            .add_jerk([ACC_X, ACC_Y, ACC_Z], prefix='acc')
            .add_jerk([GYR_X, GYR_Y, GYR_Z], prefix='gyr')
            .add_roll()
            .add_pitch()
            .result()
        )
    except Exception as e:
        raise TransformException(e)


def check_columns(df: pd.DataFrame):
    try:
        reuqired_columns = {'accx', 'accy', 'accz', 'gyrx', 'gyry', 'gyrz', 'magx', 'magy', 'magz', 'linaccx',
                            'linaccy',
                            'linaccz', 'rotx', 'roty', 'rotz', 'timestamp'}
        columns = set(df.columns)
    except Exception as e:
        raise WrongColumnsException(e)
    return reuqired_columns.issubset(columns)


def save_data(df: DataFrame, output_dir, filename):
    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        save_path = output_dir / filename
        df.to_csv(save_path, index=False)
    except Exception as e:
        raise SaveException(e)


def get_csv_paths(input_dir):
    input_dir = Path(input_dir)
    paths = list(input_dir.rglob('*.csv'))
    return paths


def read_csv(path):
    try:
        try:
            df = pd.read_csv(path, engine="pyarrow")
        except ImportError:
            # pyarrow jest tylko przyspieszaczem — bez niego czytamy
            # domyślnym silnikiem pandas zamiast wywracać cały import.
            df = pd.read_csv(path)

    except Exception as e:
        raise FileReadException(e)

    return df


def prepare_raw_file_and_save(input_path: Path, output_dir: Path, filename: str = None) -> Path:
    """Przygotowuje JEDEN surowy plik i zapisuje go w output_dir.

    Wydzielone z pętli `prepare_raw_data_and_save`, bo tej samej ścieżki
    (odczyt → transformacja → zapis) używa upload w aplikacji webowej,
    gdzie plik przychodzi pojedynczo. Wyjątki lecą dalej — o tym, czy
    błąd tylko logujemy, czy przerywa całość, decyduje wołający.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    filename = filename or input_path.name

    df = read_csv(input_path)
    df['session_index'] = input_path.stem
    transformed = transform_raw_df(df)
    save_data(transformed, output_dir, filename)
    return output_dir / filename


def prepare_raw_data_and_save(input_paths: list[Path], output_dir: Path):
    print(f'input_files: {len(input_paths)}')
    output_dir.mkdir(exist_ok=True, parents=True)
    for index, path in enumerate(input_paths, start=1):
        try:
            print(index, '/', len(input_paths))
            prepare_raw_file_and_save(path, output_dir)

        except FileReadException as e:
            print(f'ERROR blad odczytu pliku {path} {e} ')
        except TransformException as e:
            print(f'ERROR blad transformacji pliku {path} {e}')

        except SaveException as e:
            print(f'ERROR blad zapisu pliku {path} {e} ')

        except Exception as e:
            print(f'ERROR nieznany blad {e}')
        else:
            print(f'OK transformacja {path}')


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--input_files',
        nargs="*",
        help='Ścieżki surowych danych .csv'
        , default=None
    )
    parser.add_argument(
        "--input_dir",
        help="Katalog z surowymi danymi .csv",
        default=None
    )

    parser.add_argument(
        "--output_dir",
        required=True,
        help="Folder zapisu przygotowanych plików",
        default=r'.\prepared'
    )

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    input_paths = []
    output_dir = args.output_dir
    output_dir = Path(output_dir)
    try:
        if args.input_dir and args.input_files:
            raise ValueError("Podaj albo input_dir albo input_files, nie oba")

        if args.input_dir:
            input_paths = list(Path(args.input_dir).glob("*.csv"))

        elif args.input_files:
            input_paths = [Path(p) for p in args.input_files]

        else:
            raise ValueError("Musisz podać input_dir albo input_files")

        prepare_raw_data_and_save(input_paths, output_dir)

    except Exception as e:
        print(e)
    finally:
        input('exit...')

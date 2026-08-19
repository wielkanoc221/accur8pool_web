import shutil
import tempfile
from collections import Counter
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from . import demo, motion3d, views
from .motion3d import constants as motion3d_constants, scene
from .models import Dataset, Segment, SubSegment

User = get_user_model()

# ============================================================
#  ANIMACJA 3D
#
#  Dane syntetyczne o ZNANEJ kinematyce: nadgarstek wykonuje ruch
#  sinusoidalny 1.2 Hz o amplitudzie 12 cm, obracając się przy tym
#  wokół osi Y. Wiemy więc dokładnie, jaką drogę i jaką prędkość
#  maksymalną powinna zwrócić rekonstrukcja — jeśli któryś krok
#  (fuzja, obrót do układu świata, całkowanie, filtr HP) zostanie
#  zepsuty, liczby się rozjadą.
# ============================================================

FS_IMU = 400.0      # Hz
FREQ = 1.2          # Hz — częstotliwość wahnięcia
AMPL = 0.12         # m — amplituda ruchu wzdłuż X świata


def _world_to_device(v, ang):
    """Obrót wektorów ze świata do układu urządzenia, obrót wokół osi Y.

    Cała scena testowa kręci się wyłącznie wokół jednej osi, więc macierz
    wpisujemy wprost zamiast ciągnąć scipy tylko dla wygenerowania danych
    wejściowych — biblioteki do testów nie ma w requirements i nie ma
    powodu, żeby była.
    """
    c, s = np.cos(ang), np.sin(ang)
    return np.column_stack([c * v[:, 0] - s * v[:, 2],
                            v[:, 1],
                            s * v[:, 0] + c * v[:, 2]])


def imu_csv(seconds=8.0, with_lin=True, with_rot=True, with_mag=False,
            csv_version=None):
    """Ramka w formacie zapisu z zegarka: acc/gyr/rot/linacc + timestamp.

    Poszczególne grupy kolumn są opcjonalne, bo zapisy różnią się między
    wersjami — i właśnie na tym potykało się przygotowanie danych.

    `csv_version` przełącza na format V2: kolumna `csv_version` decyduje
    w transform_raw_df o wyborze transformera, a `timestamp` jest wtedy
    BEZWZGLĘDNYM znacznikiem w nanosekundach, nie odstępem w ms."""
    n = int(FS_IMU * seconds)
    t = np.arange(n) / FS_IMU

    ang = 0.6 * np.sin(2 * np.pi * FREQ * t)
    gyr = np.column_stack([np.zeros(n), np.gradient(ang, 1 / FS_IMU), np.zeros(n)])

    pos = np.column_stack([AMPL * np.sin(2 * np.pi * FREQ * t), np.zeros(n), np.zeros(n)])
    a_world = np.column_stack(
        [np.gradient(np.gradient(pos[:, k], 1 / FS_IMU), 1 / FS_IMU) for k in range(3)])

    lin = _world_to_device(a_world, ang)
    acc = lin + _world_to_device(np.tile([0.0, 0.0, 9.80665], (n, 1)), ang)

    kolumny = {
        "accx": acc[:, 0], "accy": acc[:, 1], "accz": acc[:, 2],
        "gyrx": gyr[:, 0], "gyry": gyr[:, 1], "gyrz": gyr[:, 2],
        "timestamp": np.full(n, 1000.0 / FS_IMU),
    }
    if with_lin:
        kolumny.update({"linaccx": lin[:, 0], "linaccy": lin[:, 1], "linaccz": lin[:, 2]})
    if with_rot:
        # rotation vector leci wolniej (50 Hz) i jest forward-fillowany.
        # Składowe xyz kwaternionu obrotu wokół Y to (0, sin(kąt/2), 0);
        # rotw celowo nie ma — motion3d liczy je z pozostałych trzech.
        krok = int(FS_IMU / 50)
        pod = (np.arange(n) // krok) * krok
        kolumny.update({
            "rotx": np.zeros(n),
            "roty": np.sin(ang[pod] / 2),
            "rotz": np.zeros(n),
        })
    if with_mag:
        # Pole ziemskie (~50 µT na północ) obracane razem z urządzeniem.
        mag = _world_to_device(np.tile([22.0, 0.0, 44.0], (n, 1)), ang)
        kolumny.update({"magx": mag[:, 0], "magy": mag[:, 1], "magz": mag[:, 2]})
    if csv_version is not None:
        kolumny["timestamp"] = 1.7e18 + np.arange(n) * (1e9 / FS_IMU)
        kolumny["csv_version"] = np.full(n, csv_version)

    naglowek = ",".join(kolumny)
    wiersze = np.column_stack(list(kolumny.values()))
    # .17g, a nie .8g: znacznik czasu V2 to epoka w nanosekundach (~1.7e18)
    # i przy ośmiu cyfrach znaczących wszystkie wiersze miałyby JEDNAKOWĄ
    # wartość — odstęp między próbkami zniknąłby w zaokrągleniu.
    return naglowek + "\n" + "\n".join(",".join(f"{v:.17g}" for v in w) for w in wiersze) + "\n"


class BaseDataTest(TestCase):
    """Zalogowany użytkownik i katalogi na pliki CSV poza drzewem projektu.

    views.DATA_DIR i views.PREPARED_DATA_DIR są liczone przy imporcie
    modułu, więc override_settings już na nie nie wpływa — trzeba podmienić
    same zmienne.
    """

    def setUp(self):
        super().setUp()
        self.tmp_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_root, True)

        self.tmp_dir = self.tmp_root / "raw_data"
        self.prepared_dir = self.tmp_root / "prepared_data"
        # prepared_data celowo NIE powstaje z góry — ma je zakładać upload.
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        for nazwa, katalog in (("DATA_DIR", self.tmp_dir),
                               ("PREPARED_DATA_DIR", self.prepared_dir)):
            patch = mock.patch.object(views, nazwa, katalog)
            patch.start()
            self.addCleanup(patch.stop)

        # Konto testowe ma być PUSTE. Nowe konta dostają zestaw
        # demonstracyjny (dashboard/demo.py), więc bez odcięcia katalogu demo
        # każdy tutejszy test o listach zestawów zależałby od tego, czy ktoś
        # wgrał demo do repozytorium.
        patch = mock.patch.object(demo, "DEMO_DIR", self.tmp_root / "bez-demo")
        patch.start()
        self.addCleanup(patch.stop)

        self.user = User.objects.create_user("ala", password="tajne-haslo-123")
        self.client.force_login(self.user)

    def upload(self, nazwa, tresc=None):
        """Zapisuje CSV tam, gdzie szuka go widok, i rejestruje Dataset.

        Plik ląduje w OBU drzewach. Aplikacja czyta wyłącznie
        prepared_data i zestaw bez wersji przygotowanej jest dla niej
        niewidoczny, więc test, który zapisałby tylko surowy plik,
        dostawałby wszędzie 404 — niezależnie od tego, co sprawdza.

        Treść jest w obu miejscach ta sama: te testy nie sprawdzają
        transformacji (od tego są UploadTests), tylko zachowanie widoków
        na gotowym pliku.
        """
        for katalog in (self.tmp_dir, self.prepared_dir):
            user_dir = katalog / str(self.user.pk)
            user_dir.mkdir(parents=True, exist_ok=True)
            (user_dir / nazwa).write_text(imu_csv() if tresc is None else tresc,
                                          encoding="utf-8")
        return Dataset.objects.create(owner=self.user, filename=nazwa)


class Motion3DSupportTests(TestCase):

    def test_wymaga_timestampu_i_orientacji(self):
        self.assertTrue(motion3d.supports(["gyrx", "gyry", "gyrz", "timestamp"]))
        self.assertTrue(motion3d.supports(["rotx", "roty", "rotz", "timestamp"]))
        self.assertFalse(motion3d.supports(["gyrx", "gyry", "gyrz"]))
        self.assertFalse(motion3d.supports(["accx", "accy", "accz", "timestamp"]))


def uderzenie_csv(seconds=0.8, v_max=1.5, margines=0.3, bias=0.0, fs=400.0):
    """Zapis JEDNEGO uderzenia: spoczynek → ruch → spoczynek.

    Tak wygląda segment, który użytkownik zaznacza na wykresie, i tylko na
    takim da się odtworzyć prędkość DOKŁADNIE: całkowanie daje ją
    z dokładnością do stałej, a spoczynek na końcach tę stałą ustala.

    Ruch idzie wzdłuż X, bez obrotu (R = I), więc przyspieszenie
    urządzenia jest zarazem przyspieszeniem świata i prawdę widać wprost.
    `bias` to stały błąd zera akcelerometru — ma NIE wpływać na wynik.
    """
    n_ruch, n_margines = int(fs * seconds), int(fs * margines)
    n = n_ruch + 2 * n_margines
    t = np.arange(n) / fs

    predkosc = np.zeros(n)
    predkosc[n_margines:n_margines + n_ruch] = v_max * np.sin(
        np.pi * (np.arange(n_ruch) / fs) / seconds) ** 2

    droga_m = np.trapezoid(predkosc, t)
    przyspieszenie = np.gradient(predkosc, t) + bias

    kolumny = {
        "accx": przyspieszenie, "accy": np.zeros(n), "accz": np.full(n, 9.80665),
        "gyrx": np.zeros(n), "gyry": np.zeros(n), "gyrz": np.zeros(n),
        "timestamp": np.full(n, 1000.0 / fs),
        "linaccx": przyspieszenie, "linaccy": np.zeros(n), "linaccz": np.zeros(n),
        "rotx": np.zeros(n), "roty": np.zeros(n), "rotz": np.zeros(n),
    }
    wiersze = np.column_stack(list(kolumny.values()))
    tekst = (",".join(kolumny) + "\n" +
             "\n".join(",".join(f"{v:.17g}" for v in w) for w in wiersze) + "\n")
    return tekst, n, droga_m * 100.0


class PredkoscTests(BaseDataTest):
    """v_max na segmencie od spoczynku do spoczynku.

    Wcześniej filtr średniej kroczącej zaniżał tu prędkość o 33–50%:
    prędkość pojedynczego uderzenia jest jednostronnym garbem, a jego
    średnia krocząca to spory ułamek szczytu, więc odjęcie jej ścinało
    połowę sygnału. Odjęcie tego, co narosło z błędu zera (rampa między
    końcami), tego nie robi.
    """

    def zapisz(self, nazwa, tresc):
        path = self.tmp_dir / nazwa
        path.write_text(tresc, encoding="utf-8")
        return path

    def _rekonstrukcja(self, nazwa, **kwargs):
        tresc, n, droga_cm = uderzenie_csv(**kwargs)
        prep = motion3d.prepare(self.zapisz(nazwa, tresc))
        return motion3d.build_motion(prep, 0, n - 1)["meta"], droga_cm

    def test_predkosc_szczytowa_jest_dokladna(self):
        meta, droga_cm = self._rekonstrukcja("ud.csv")

        self.assertAlmostEqual(meta["v_max"], 1.5, delta=0.05)
        self.assertAlmostEqual(meta["path_cm"], droga_cm, delta=1.0)

    def test_blad_zera_akcelerometru_nie_zmienia_wyniku(self):
        """Stały bias znika przy odejmowaniu średniej przyspieszenia —
        bo na segmencie od spoczynku do spoczynku ∫a dt = Δv = 0, więc
        średnia PRAWDZIWEGO przyspieszenia jest zerem."""
        czysty, _ = self._rekonstrukcja("czysty.csv")
        obciazony, _ = self._rekonstrukcja("bias.csv", bias=1.0)

        self.assertAlmostEqual(obciazony["v_max"], czysty["v_max"], delta=0.02)
        self.assertAlmostEqual(obciazony["path_cm"], czysty["path_cm"], delta=1.0)

    def test_spoczynek_na_koncach_jest_rozpoznany(self):
        meta, _ = self._rekonstrukcja("spokoj.csv")
        self.assertIn("spoczynku", meta["label"])

    def test_wycinek_ze_srodka_ruchu_nie_udaje_spoczynku(self):
        """Zaznaczenie ucięte w ruchu MUSI wrócić na filtr średniej.

        Prędkości początkowej nie ma tam z czego wziąć, a założenie
        „stoi na końcach” zawyżyłoby ją dwukrotnie. Interfejs ma o tym
        powiedzieć wprost, zamiast podawać liczbę bez zastrzeżenia.
        """
        prep = motion3d.prepare(self.zapisz("ciagly.csv", imu_csv(seconds=8.0)))
        okres = int(FS_IMU / FREQ)
        meta = motion3d.build_motion(prep, int(FS_IMU * 3),
                                     int(FS_IMU * 3) + okres * 3)["meta"]

        self.assertNotIn("spoczynku", meta["label"])
        self.assertIn("ucięty w ruchu", meta["label"])

    def test_dlugie_nagranie_nie_ucieka_w_linii_prostej(self):
        """Tor długiego nagrania ma zostać w kadrze, a nie odjechać.

        Warunek spoczynku na końcach odejmuje z prędkości PROSTĄ, więc
        kasuje tylko dryf narastający równomiernie. Prawdziwy dryf błądzi
        i po kilkunastu sekundach jego całka daje tor uciekający w jedną
        stronę na dziesiątki metrów przy ruchu rzędu pół metra. Powyżej
        ANCHOR_MAX_S wchodzi więc filtr, choćby końce stały.
        """
        tresc, n, _ = uderzenie_csv(seconds=0.8, margines=12.0)
        prep = motion3d.prepare(self.zapisz("dlugie.csv", tresc))
        wynik = motion3d.build_motion(prep, 0, n - 1)

        pozycje = np.array(wynik["payload"]["pos"])
        przesuniecie = float(np.linalg.norm(pozycje[-1] - pozycje[0]))
        meta = wynik["meta"]

        # Tor uciekający w linii prostej ma przesunięcie równe drodze.
        # Ruch, który gdzieś dochodzi i zostaje, ma je znacznie mniejsze.
        self.assertLess(przesuniecie, 0.5 * meta["path_cm"])
        self.assertNotIn("spoczynku", meta["label"])

        # I druga połowa tej samej sprawy: dryf ma być WIDOCZNY w liczbach,
        # nie tylko usunięty. Gałąź z kotwicą mierzyła wcześniej odjętą
        # rampę PRĘDKOŚCI i raportowała 1 cm przy torze uciekającym na
        # 97 metrów — liczba pilnująca wiarygodności milczała dokładnie
        # wtedy, gdy była potrzebna.
        self.assertGreater(meta["drift_cm"], 0.0)
        self.assertGreaterEqual(meta["drift_ratio"], 0.0)

    def test_filtr_nie_tnie_ruchu_tam_i_z_powrotem(self):
        """Filtr dryfu nie ma prawa zjadać ruchu, który mierzymy.

        Odtwarzamy sytuację z prowadnicy: jazda tam i z powrotem o okresie
        ok. 3 s, czyli w paśmie, w którym filtr dryfu jeszcze działa.
        Przy zbyt wysokim progu filtr ścinał amplitudę o połowę i DZIELIŁ
        pojedynczy przejazd na kilka — na ekranie wyglądało to jak
        zatrzymanie i zawrócenie w środku płynnego ruchu.

        Sprawdzamy jedno i drugie: amplitudę oraz liczbę punktów zwrotnych,
        której znamy prawdziwą wartość, bo sami zadaliśmy ruch.
        """
        fs, okres, amplituda_m = 100.0, 3.0, 0.30
        czas_s = okres * 4
        n = int(fs * czas_s)
        t = np.arange(n) / fs

        omega = 2 * np.pi / okres
        polozenie = amplituda_m * np.sin(omega * t)
        przyspieszenie = -amplituda_m * omega ** 2 * np.sin(omega * t)

        kolumny = {
            "accx": przyspieszenie, "accy": np.zeros(n), "accz": np.full(n, 9.80665),
            "gyrx": np.zeros(n), "gyry": np.zeros(n), "gyrz": np.zeros(n),
            "timestamp": np.full(n, 1000.0 / fs),
            "linaccx": przyspieszenie, "linaccy": np.zeros(n), "linaccz": np.zeros(n),
            "rotx": np.zeros(n), "roty": np.zeros(n), "rotz": np.zeros(n),
        }
        wiersze = np.column_stack(list(kolumny.values()))
        tresc = (",".join(kolumny) + "\n" +
                 "\n".join(",".join(f"{v:.17g}" for v in w) for w in wiersze) + "\n")

        prep = motion3d.prepare(self.zapisz("prowadnica.csv", tresc))
        pozycje = np.array(
            motion3d.build_motion(prep, 0, n - 1)["payload"]["pos"])

        wzdluz = pozycje[:, 0]
        rozpietosc = float(wzdluz.max() - wzdluz.min())
        prawda_cm = 2 * amplituda_m * 100

        # Filtr wolno, żeby trochę uszczknął, ale nie połowę.
        self.assertGreater(rozpietosc, 0.75 * prawda_cm,
                           "filtr dryfu zjada mierzony ruch")

        # Zadany ruch ma dokładnie jeden punkt zwrotny na pół okresu.
        # Więcej znaczy, że filtr pociął przejazd na kawałki.
        pochodna = np.gradient(wzdluz)
        zwroty = np.where(np.diff(np.sign(pochodna)) != 0)[0]
        odlegle = [i for k, i in enumerate(zwroty)
                   if k == 0 or (i - zwroty[k - 1]) > 0.3 * fs]

        self.assertLessEqual(len(odlegle), 2 * int(czas_s / okres) + 2,
                             "filtr dorobił punkty zwrotne, których nie było")

    def test_droga_nie_rosnie_od_zapasu_spoczynku(self):
        """Zapas nieruchomych próbek po bokach nie ma prawa wydłużyć toru.

        Filtr na POZYCJI dorysowywał tu zawracanie: przy 1.5 s zapasu
        z każdej strony droga wychodziła 101 cm zamiast 60.
        """
        krotki, droga_cm = self._rekonstrukcja("krotki.csv", margines=0.3)
        dlugi, _ = self._rekonstrukcja("dlugi.csv", margines=1.5)

        self.assertAlmostEqual(krotki["path_cm"], droga_cm, delta=1.0)
        self.assertAlmostEqual(dlugi["path_cm"], droga_cm, delta=1.0)


class WatchGeometryTests(TestCase):
    """Koperta zegarka ma PRAWDZIWY rozmiar, nie ułamek rozpiętości ruchu.

    O to chodzi w całej scenie: skoro tor jest w centymetrach, to zegarek
    też, i dopiero wtedy z obrazu widać, ile razy zamach był większy od
    urządzenia. Wcześniej bryła rosła razem z ruchem, więc każde nagranie
    wyglądało tak samo.
    """

    @staticmethod
    def _tor(rozpietosc_cm):
        pozycje = np.zeros((50, 3))
        pozycje[:, 0] = np.linspace(0.0, rozpietosc_cm, 50)
        return pozycje

    @staticmethod
    def _wymiary(geometry):
        wierzcholki = np.array(geometry.vertices)
        return tuple(float(wierzcholki[:, os].max() - wierzcholki[:, os].min())
                     for os in range(3))

    def _koperta(self, rozpietosc_cm, watch_scale=1.0):
        bounds = scene.SceneBounds.around(self._tor(rozpietosc_cm), watch_scale)
        return self._wymiary(scene.WatchGeometry.of(bounds.watch_scale)), bounds

    def test_rozmiar_nie_zalezy_od_rozpietosci_ruchu(self):
        drobny, _ = self._koperta(0.5)
        szeroki, _ = self._koperta(80.0)

        self.assertEqual(drobny, szeroki)
        for wymiar, oczekiwany in zip(drobny, motion3d_constants.WATCH_SIZE_CM):
            self.assertAlmostEqual(wymiar, oczekiwany, places=6)

    def test_udzial_w_scenie_maleje_przy_wiekszym_ruchu(self):
        """Sedno proporcji: ta sama koperta na większej scenie zajmuje mniej."""
        drobny, bounds_drobny = self._koperta(2.0)
        szeroki, bounds_szeroki = self._koperta(60.0)

        self.assertGreater(max(drobny) / bounds_drobny.side,
                           3 * max(szeroki) / bounds_szeroki.side)

    def test_reczne_powiekszenie_skaluje_wszystkie_boki_rowno(self):
        pojedyncza, _ = self._koperta(10.0)
        potrojna, _ = self._koperta(10.0, watch_scale=3.0)

        for jeden, trzy in zip(pojedyncza, potrojna):
            self.assertAlmostEqual(trzy, 3 * jeden, places=6)

    def test_kadr_miesci_koperte_takze_przy_ruchu_zerowym(self):
        koperta, bounds = self._koperta(0.0)
        self.assertGreater(bounds.side, max(koperta))


class WatchMeshTests(TestCase):
    """Siatka okrągłej koperty — sprawdzona ZANIM zobaczy ją Plotly.

    Mesh3d nie protestuje przeciwko niczemu: dziurawa bryła po prostu
    prześwituje, a odwrotnie nawinięty trójkąt zostaje czarny przy
    `flatshading`. Jedno i drugie widać dopiero na ekranie i wygląda jak
    usterka animacji, nie jak błąd w geometrii — dlatego jest tu.
    """

    def setUp(self):
        self.geometry = scene.WatchGeometry.of(1.0)
        self.vertices = np.array(self.geometry.vertices)
        self.faces = np.array(self.geometry.faces)
        self.segments = motion3d_constants.WATCH_SEGMENTS

    def _edges(self):
        for a, b, c in self.faces:
            yield from ((a, b), (b, c), (c, a))

    def test_kolor_na_kazdy_trojkat(self):
        self.assertEqual(len(self.geometry.colors), len(self.faces))
        self.assertEqual(len(self.faces), 4 * self.segments)
        self.assertEqual(len(self.vertices), 2 + 2 * self.segments)

    def test_indeksy_wskazuja_istniejace_wierzcholki(self):
        self.assertGreaterEqual(self.faces.min(), 0)
        self.assertLess(self.faces.max(), len(self.vertices))
        for face in self.faces:
            self.assertEqual(len(set(face)), 3, "trójkąt zdegenerowany")

    def test_bryla_jest_zamknieta(self):
        """Każda krawędź należy do dokładnie dwóch trójkątów — inaczej
        w bryle jest dziura albo zostaje szczelina na zamknięciu okręgu."""
        licznik = Counter(tuple(sorted(edge)) for edge in self._edges())
        self.assertEqual(set(licznik.values()), {2})

    def test_nawiniecie_jest_spojne(self):
        """Każda krawędź przebiegana raz w jedną, raz w drugą stronę."""
        skierowane = Counter(self._edges())
        for (start, end), ile in skierowane.items():
            self.assertEqual(ile, 1)
            self.assertEqual(skierowane[(end, start)], 1)

    def test_normalne_wychodza_na_zewnatrz(self):
        """Bez tego Plotly oświetla ścianę od środka i zostaje ona czarna."""
        for a, b, c in self.faces:
            normalna = np.cross(self.vertices[b] - self.vertices[a],
                                self.vertices[c] - self.vertices[a])
            # Środek bryły jest w zerze, więc centroid ściany wskazuje
            # kierunek „na zewnątrz" dla tej ściany.
            centroid = (self.vertices[a] + self.vertices[b] + self.vertices[c]) / 3.0
            self.assertGreater(float(np.dot(normalna, centroid)), 0.0)

    def test_obwod_lezy_na_okregu(self):
        obwod = self.vertices[2:]
        promienie = np.hypot(obwod[:, 0], obwod[:, 1])
        self.assertTrue(np.allclose(
            promienie, motion3d_constants.WATCH_DIAMETER_CM / 2.0))
        self.assertTrue(np.allclose(
            np.abs(self.vertices[:, 2]),
            motion3d_constants.WATCH_THICKNESS_CM / 2.0))

    def test_promien_zgadza_sie_z_najdalszym_punktem(self):
        """WATCH_RADIUS_CM wyznacza margines kadru — gdyby był za mały,
        koperta wystawałaby poza ścianę sceny i Plotly by ją przyciął."""
        self.assertAlmostEqual(float(np.linalg.norm(self.vertices, axis=1).max()),
                               motion3d_constants.WATCH_RADIUS_CM, places=9)

    def test_znacznik_jest_ciagly_i_wysrodkowany_na_y(self):
        """Bez znacznika obrót walca wokół własnej tarczy jest niewidoczny."""
        oznaczone = [wedge for wedge in range(self.segments)
                     if scene.WatchGeometry._is_marked(wedge, self.segments)]

        self.assertTrue(oznaczone)
        self.assertEqual(oznaczone, list(range(oznaczone[0], oznaczone[-1] + 1)),
                         "znacznik rozpadł się na kawałki")

        katy = [360.0 * (wedge + 0.5) / self.segments for wedge in oznaczone]
        self.assertAlmostEqual(float(np.mean(katy)), 90.0, places=6)
        self.assertLessEqual(max(katy) - min(katy),
                             motion3d_constants.WATCH_MARK_DEGREES)


class Motion3DRekonstrukcjaTests(BaseDataTest):

    def zapisz(self, nazwa, tresc=None):
        path = self.tmp_dir / nazwa
        path.write_text(imu_csv() if tresc is None else tresc, encoding="utf-8")
        return path

    def test_odtwarza_znana_kinematyke(self):
        prep = motion3d.prepare(self.zapisz("ruch.csv"))
        self.assertEqual(motion3d.describe(prep)["source"], "fused")

        # Okno o długości dokładnie jednego okresu, w środku nagrania
        okres = int(FS_IMU / FREQ)
        lo = int(FS_IMU * 3)
        meta = motion3d.build_motion(prep, lo, lo + okres)["meta"]

        # Droga w jednym okresie = 4 * amplituda, v_max = 2*pi*f*A.
        # Filtr HP zjada kilka procent amplitudy — stąd tolerancja.
        self.assertAlmostEqual(meta["path_cm"], 4 * AMPL * 100, delta=6.0)
        self.assertAlmostEqual(meta["v_max"], 2 * np.pi * FREQ * AMPL, delta=0.12)

    def test_odtwarza_os_ruchu_a_nie_tylko_jego_dlugosc(self):
        """Ruch jest zadany WZDŁUŻ X ŚWIATA — i tam ma wyjść.

        Sama droga może się zgadzać przy torze wygiętym w dowolną stronę,
        więc bez tego testu „48 cm” nie znaczy, że narysowano ten ruch,
        który zmierzono. Poprzedni model (sztywna dźwignia) wykładał się
        dokładnie tutaj: wymuszał łuk na sferze, więc rozrzucał ruch na
        wszystkie trzy osie niezależnie od tego, co pokazał czujnik.
        """
        prep = motion3d.prepare(self.zapisz("osie.csv"))
        okres = int(FS_IMU / FREQ)
        lo = int(FS_IMU * 3)

        pozycje = np.array(
            motion3d.build_motion(prep, lo, lo + okres)["payload"]["pos"])
        rozrzut = pozycje.max(axis=0) - pozycje.min(axis=0)      # [cm]

        # Ruch tam i z powrotem o amplitudzie AMPL => rozpiętość 2 * AMPL.
        self.assertAlmostEqual(rozrzut[0], 2 * AMPL * 100, delta=2.0)

        # Poprzeczne osie mają zostać puste. Próg jest ułamkiem ruchu
        # głównego, a nie liczbą bezwzględną — inaczej test przechodziłby
        # sam z siebie, gdyby rekonstrukcja przestała cokolwiek rysować.
        self.assertLess(max(rozrzut[1], rozrzut[2]), 0.10 * rozrzut[0])

    def test_tor_nie_jest_uwieziony_na_sferze(self):
        """Nadgarstek musi móc zmieniać odległość od środka ruchu.

        Model dźwigni trzymał tor na sferze o stałym promieniu co do
        ostatniej cyfry — po tym najłatwiej poznać, że wrócił.
        """
        prep = motion3d.prepare(self.zapisz("sfera.csv"))
        okres = int(FS_IMU / FREQ)
        lo = int(FS_IMU * 3)

        pozycje = np.array(
            motion3d.build_motion(prep, lo, lo + okres)["payload"]["pos"])
        promien = np.linalg.norm(pozycje - pozycje.mean(axis=0), axis=1)

        self.assertGreater(promien.max() - promien.min(), 0.5 * promien.max())

    def test_scena_ma_komplet_sladow_i_klatek(self):
        prep = motion3d.prepare(self.zapisz("scena.csv"))
        wynik = motion3d.build_motion(prep, 1200, 2400)

        # 2 statyczne (rzut, tor) + 6 animowanych (faza, ogon, zegarek,
        # trzy osie) + punkt nadgarstka
        self.assertEqual(len(wynik["figure"]["data"]), 9)
        self.assertEqual(len(wynik["payload"]["pos"]), wynik["meta"]["frames"])
        self.assertEqual(len(wynik["payload"]["t"]), wynik["meta"]["frames"])
        # Bryła zegarka: dwa środki krążków + dwa obwody
        self.assertEqual(len(wynik["payload"]["verts"]),
                         2 + 2 * motion3d_constants.WATCH_SEGMENTS)

        # Indeksy śladów animowanych są kontraktem z motion3d.js
        dyn = wynik["payload"]["dynamic"]
        self.assertEqual(set(dyn), {"phase", "trail", "watch", "axes", "marker"})
        self.assertEqual(len(dyn["axes"]), 3)

        # Kwaterniony muszą być znormalizowane, inaczej bryła w JS się rozjedzie
        normy = np.linalg.norm(np.array(wynik["payload"]["quat"]), axis=1)
        self.assertTrue(np.allclose(normy, 1.0, atol=1e-3))

    def test_fazy_zaznaczaja_podzakres_klatek(self):
        prep = motion3d.prepare(self.zapisz("fazy.csv"))
        wynik = motion3d.build_motion(prep, 1200, 2400, phases=(
            ("uderzenie", "Uderzenie", "#ef4444", 1600, 2000),
        ))

        span = wynik["payload"]["spans"][0]
        self.assertEqual(span["phase"], "uderzenie")
        self.assertEqual(span["color"], "#ef4444")

        faza = np.array(wynik["payload"]["phase"])
        self.assertTrue((faza == 0).any())
        self.assertTrue((faza == -1).any())
        # Faza zajmuje tę samą część okna, którą zaznaczono w wierszach CSV
        self.assertAlmostEqual((faza == 0).mean(), (2000 - 1600) / (2400 - 1200),
                               delta=0.05)

    def test_bez_faz_nic_nie_jest_podswietlone(self):
        prep = motion3d.prepare(self.zapisz("bez_faz.csv"))
        wynik = motion3d.build_motion(prep, 1200, 2400)

        self.assertEqual(wynik["payload"]["spans"], [])
        self.assertTrue(all(nr == -1 for nr in wynik["payload"]["phase"]))
        # statystyki liczą się wtedy z całego okna, a nie z niczego
        self.assertGreater(wynik["meta"]["path_cm"], 0.0)

    def test_bez_rotation_vectora_wchodzi_samo_gyro(self):
        prep = motion3d.prepare(self.zapisz("bez_rot.csv", imu_csv(with_rot=False)))

        self.assertEqual(motion3d.describe(prep)["source"], "gyro")
        self.assertIn("żyroskopu", motion3d.build_motion(prep, 1200, 2400)["meta"]["label"])

    def test_bez_linacc_pozycja_z_akcelerometru(self):
        prep = motion3d.prepare(self.zapisz("bez_lin.csv", imu_csv(with_lin=False)))

        self.assertIn("acc*", motion3d.build_motion(prep, 1200, 2400)["meta"]["label"])

    def test_bez_orientacji_blad_zamiast_wyjatku(self):
        prep = motion3d.prepare(self.zapisz("bez_imu.csv", "accx,timestamp\n" +
                                            "".join(f"{i},2.5\n" for i in range(500))))

        self.assertFalse(motion3d.describe(prep)["ok"])
        with self.assertRaises(motion3d.Motion3DError):
            motion3d.build_motion(prep, 100, 400)

    def test_za_krotki_i_za_dlugi_zakres(self):
        prep = motion3d.prepare(self.zapisz("zakres.csv"))

        with self.assertRaises(motion3d.Motion3DError):
            motion3d.build_motion(prep, 100, 103)

        # Limit zamiast pliku na 200 tys. wierszy — testujemy strażnika,
        # nie wydajność pandas
        with mock.patch.object(motion3d, "MAX_ROWS", 100):
            with self.assertRaises(motion3d.Motion3DError):
                motion3d.build_motion(prep, 0, 1000)

    def test_fps_jest_gornym_limitem_a_nie_tempem(self):
        prep = motion3d.prepare(self.zapisz("fps.csv"))
        okno = int(FS_IMU * 2)

        # fps=0 znosi limit — klatka to każdy wiersz pliku
        pelne = motion3d.build_motion(prep, 400, 400 + okno, fps=0)["meta"]
        self.assertEqual(pelne["stride"], 1)
        self.assertEqual(pelne["frames"], okno)

        # Z limitem 25 przy zapisie 400 Hz wchodzi co szesnasta próbka
        rzadkie = motion3d.build_motion(prep, 400, 400 + okno, fps=25)["meta"]
        self.assertEqual(rzadkie["stride"], 16)
        self.assertLess(rzadkie["frames"], pelne["frames"])
        # Ostatnia próbka wchodzi zawsze, więc czas obu wersji jest ten sam
        self.assertAlmostEqual(rzadkie["duration"], pelne["duration"], places=4)


class Motion3DApiTests(BaseDataTest):
    """Endpoint animacji przyjmuje WYŁĄCZNIE ?segment=<id>.

    Zakres bierze się z segmentu w bazie, nigdy z widocznego fragmentu
    wykresu — patrz docstring views.api_dataset_motion3d.
    """

    def setUp(self):
        super().setUp()
        self.dataset = self.upload("ruch.csv")
        self.segment = Segment.objects.create(dataset=self.dataset, start=1200, end=2400)

    def url(self, params=""):
        return reverse("api_dataset_motion3d", args=["ruch.csv"]) + params

    def test_zwraca_scene_i_klatki_dla_segmentu(self):
        response = self.client.get(self.url(f"?segment={self.segment.pk}"))
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(len(body["figure"]["data"]), 9)
        self.assertEqual(len(body["payload"]["t"]), body["meta"]["frames"])
        self.assertEqual(body["meta"]["source"], "fused")
        # Zakres animacji to dokładnie zakres segmentu
        self.assertEqual((body["meta"]["lo"], body["meta"]["hi"]), (1200, 2400))

    def test_fazy_segmentu_trafiaja_do_sceny(self):
        SubSegment.objects.create(segment=self.segment, phase=SubSegment.PHASE_STRIKE,
                                  start=1600, end=2000)

        body = self.client.get(self.url(f"?segment={self.segment.pk}")).json()
        spans = body["payload"]["spans"]

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["label"], "Uderzenie")
        # Kolor idzie z modelu, wspólny z prostokątami na wykresie 2D
        self.assertEqual(spans[0]["color"],
                         SubSegment.PHASE_COLORS[SubSegment.PHASE_STRIKE])

    def test_bez_parametru_fps_nie_gina_probki(self):
        # motion3d.js celowo nie wysyła fps — odtwarzacz dobiera klatkę po
        # czasie i ma pokazać każdą próbkę, którą zmierzył czujnik.
        body = self.client.get(self.url(f"?segment={self.segment.pk}")).json()
        self.assertEqual(body["meta"]["stride"], 1)
        self.assertEqual(body["meta"]["frames"], 2400 - 1200)

        # Podany fps nadal działa jako górny limit
        rzadkie = self.client.get(
            self.url(f"?segment={self.segment.pk}&fps=25")).json()
        self.assertEqual(rzadkie["meta"]["stride"], 16)

    def test_bez_segmentu_to_400_z_komunikatem(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_smieciowy_numer_segmentu_to_400(self):
        self.assertEqual(self.client.get(self.url("?segment=abc")).status_code, 400)

    def test_nieznany_segment_to_404(self):
        self.assertEqual(
            self.client.get(self.url(f"?segment={self.segment.pk + 999}")).status_code,
            404,
        )

    def test_dashboard_wlacza_zakladke_3d(self):
        response = self.client.get(reverse("dashboard", args=["ruch.csv"]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["motion3d_ready"])

    def test_plik_bez_imu_nie_wlacza_zakladki(self):
        self.upload("plaski.csv", "a,b\n" + "".join(f"{i},{i}\n" for i in range(50)))
        response = self.client.get(reverse("dashboard", args=["plaski.csv"]))
        self.assertFalse(response.context["motion3d_ready"])

    def test_za_dlugi_zakres_to_422_z_komunikatem(self):
        with mock.patch.object(motion3d, "MAX_ROWS", 100):
            response = self.client.get(self.url(f"?segment={self.segment.pk}"))

        self.assertEqual(response.status_code, 422)
        self.assertIn("error", response.json())

    def test_nie_da_sie_animowac_cudzego_pliku(self):
        User.objects.create_user("bob", password="tajne-haslo-123")
        self.client.force_login(User.objects.get(username="bob"))

        self.assertEqual(
            self.client.get(self.url(f"?segment={self.segment.pk}")).status_code, 404)


class SegmentApiTests(BaseDataTest):
    """Lista segmentów — to z niej bierze się wybór uderzenia w zakładce 3D."""

    def setUp(self):
        super().setUp()
        self.dataset = self.upload("ruch.csv")

    def url(self, sufiks=""):
        return reverse("api_segments", args=["ruch.csv"]) + sufiks

    def test_lista_niesie_kolory_faz(self):
        body = self.client.get(self.url()).json()

        kolory = {t["key"]: t["color"] for t in body["phase_types"]}
        self.assertEqual(kolory, SubSegment.PHASE_COLORS)

    def test_nowy_segment_wraca_z_numerem_i_zakresem(self):
        response = self.client.post(self.url(), {"start": 1200, "end": 2400},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 201)

        segmenty = response.json()["segments"]
        self.assertEqual(len(segmenty), 1)
        self.assertEqual(segmenty[0]["name"], "1")
        self.assertEqual((segmenty[0]["start"], segmenty[0]["end"]), (1200, 2400))
        self.assertEqual(segmenty[0]["phases"], [])


# ============================================================
#  UPLOAD: raw_data + prepared_data
# ============================================================

class UploadTests(BaseDataTest):
    """Upload zapisuje plik w DWÓCH drzewach: surowy i przygotowany.

    Wersja przygotowana jest WARUNKIEM przyjęcia pliku — bez niej nie ma
    ani wpisu w bazie, ani pliku surowego na dysku.
    """

    def wyslij(self, nazwa, tresc):
        plik = SimpleUploadedFile(nazwa, tresc.encode("utf-8"), content_type="text/csv")
        return self.client.post(reverse("api_upload_dataset"), {"file": plik})

    def sciezki(self, nazwa):
        return (self.tmp_dir / str(self.user.pk) / nazwa,
                self.prepared_dir / str(self.user.pk) / nazwa)

    def test_upload_tworzy_surowy_i_przygotowany_plik(self):
        response = self.wyslij("ruch.csv", imu_csv(seconds=4.0, with_mag=True))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["prepared"])

        raw_path, prepared_path = self.sciezki("ruch.csv")
        self.assertTrue(raw_path.exists())
        self.assertTrue(prepared_path.exists())

        # Surowy plik zostaje bajt w bajt taki, jaki przyszedł…
        raw = pd.read_csv(raw_path)
        self.assertNotIn("acc_magnitude", raw.columns)

        # …a przygotowany ma kolumny dokładane przez transform_raw_df.
        prepared = pd.read_csv(prepared_path)
        for kolumna in ("acc_magnitude", "gyr_magnitude", "time",
                        "jerk_accx", "roll", "pitch", "session_index"):
            self.assertIn(kolumna, prepared.columns)
        self.assertEqual(len(prepared), len(raw))

    def test_wektor_obrotu_przechodzi_nietkniety(self):
        # rot* to składowe kwaternionu, wiązane warunkiem |q| = 1. Filtr
        # przepuszczany składowa po składowej łamał ten warunek i to był
        # jedyny powód, dla którego animacja 3D musiała czytać plik surowy.
        # Reszta czujników ma zostać przefiltrowana — inaczej ten test
        # przechodziłby także wtedy, gdyby filtrowania nie było w ogóle.
        self.wyslij("ruch.csv", imu_csv(seconds=4.0, with_mag=True))
        raw_path, prepared_path = self.sciezki("ruch.csv")
        raw, prepared = pd.read_csv(raw_path), pd.read_csv(prepared_path)

        for kolumna in ("rotx", "roty", "rotz"):
            self.assertTrue(np.allclose(prepared[kolumna], raw[kolumna]),
                            f"{kolumna} zostało zmienione przez transformację")

        for kolumna in ("accx", "gyry"):
            self.assertFalse(np.allclose(prepared[kolumna], raw[kolumna]),
                             f"{kolumna} nie zostało przefiltrowane")

    def test_filtr_bierze_tempo_zapisu_z_pliku(self):
        # Zapis testowy idzie 400 Hz. Przy zaszytym na sztywno fs = 100 Hz
        # granica 10 Hz wychodziła w rzeczywistości 40 Hz, więc filtr
        # zostawiał wielokrotnie więcej wysokich częstotliwości, niż
        # deklarował. Sprawdzamy to na samym transformerze, bo w gotowym
        # pliku widać już tylko skutek.
        from data_processing.data_transformations import DataFrameTransformerBase

        df = pd.DataFrame({"timestamp": np.full(2000, 1000.0 / FS_IMU),
                           "accx": np.zeros(2000)})
        transformer = DataFrameTransformerBase(df).dt_ms_to_sec().add_time()

        self.assertAlmostEqual(transformer.sampling_rate(), FS_IMU, delta=1.0)

    def test_brak_opcjonalnych_czujnikow_nie_blokuje_przygotowania(self):
        # Listy kolumn do filtrowania w transform_raw_df opisują KOMPLET
        # czujników, ale zapis bez magnetometru jest normalny. Wcześniej
        # kończyło się to KeyError-em i plik zostawał bez wersji
        # przygotowanej.
        response = self.wyslij("bez_mag.csv", imu_csv(seconds=4.0, with_mag=False))
        self.assertTrue(response.json()["prepared"])

        prepared = pd.read_csv(self.sciezki("bez_mag.csv")[1])
        self.assertNotIn("magx", prepared.columns)
        # Kolumny pochodne liczą się mimo braku magnetometru
        for kolumna in ("acc_magnitude", "gyr_magnitude", "time", "roll", "pitch"):
            self.assertIn(kolumna, prepared.columns)

    def test_sam_rdzen_acc_gyr_wystarczy(self):
        response = self.wyslij("rdzen.csv", imu_csv(seconds=4.0, with_lin=False,
                                                    with_rot=False, with_mag=False))
        self.assertTrue(response.json()["prepared"])

    def test_zapis_v2_idzie_swoim_transformerem(self):
        # csv_version przełącza transform_raw_df na DataFrameTransformerV2:
        # timestamp jest bezwzględny (ns), a roll/pitch z urządzenia zostają
        # nietknięte — policzone lądują obok, z sufiksem _calculated.
        response = self.wyslij("v2.csv", imu_csv(seconds=4.0, with_mag=True, csv_version=2))
        self.assertTrue(response.json()["prepared"])

        prepared = pd.read_csv(self.sciezki("v2.csv")[1])
        self.assertIn("roll_calculated", prepared.columns)
        self.assertIn("pitch_calculated", prepared.columns)

        # Oś czasu wychodzi w sekundach mimo wejścia w nanosekundach.
        czas = prepared["time"].to_numpy()
        self.assertAlmostEqual(czas[-1], 4.0, delta=0.05)
        self.assertTrue(np.all(np.diff(czas) > 0))

    def test_plik_bez_wymaganych_kolumn_jest_odrzucany(self):
        # Aplikacja czyta wyłącznie wersję przygotowaną, więc plik, z
        # którego nie da się jej policzyć, nie wchodzi do systemu wcale.
        response = self.wyslij("plaski.csv", "a,b\n" + "".join(f"{i},{i}\n"
                                                               for i in range(50)))

        self.assertEqual(response.status_code, 422)
        # Komunikat ma powiedzieć, CZEGO brakuje — to jedyna wskazówka,
        # jaką użytkownik dostanie o swoim pliku.
        self.assertIn("accx", response.json()["error"])

        # Ani wpisu w bazie, ani śladu na dysku — w tym surowego pliku,
        # bo bez wpisu nikt by go już nie posprzątał.
        self.assertFalse(Dataset.objects.filter(owner=self.user).exists())
        raw_path, prepared_path = self.sciezki("plaski.csv")
        self.assertFalse(raw_path.exists())
        self.assertFalse(prepared_path.exists())

    def test_po_odrzuceniu_nazwa_zostaje_wolna(self):
        # Odrzucony upload nie ma prawa zająć nazwy: poprawiony plik
        # powinien wejść jako „ruch.csv”, a nie „ruch (2).csv”.
        self.wyslij("ruch.csv", "a,b\n1,2\n")
        response = self.wyslij("ruch.csv", imu_csv(seconds=4.0, with_mag=True))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "ruch.csv")

    def test_kolejny_plik_o_tej_samej_nazwie_nie_nadpisuje_przygotowanego(self):
        tresc = imu_csv(seconds=4.0, with_mag=True)
        self.wyslij("ruch.csv", tresc)
        response = self.wyslij("ruch.csv", tresc)

        nazwa = response.json()["name"]
        self.assertEqual(nazwa, "ruch (2).csv")

        raw_path, prepared_path = self.sciezki(nazwa)
        self.assertTrue(raw_path.exists())
        self.assertTrue(prepared_path.exists())

    def test_liczba_rekordow_jest_z_wersji_przygotowanej(self):
        response = self.wyslij("ruch.csv", imu_csv(seconds=4.0, with_mag=True))

        prepared = pd.read_csv(self.sciezki("ruch.csv")[1])
        self.assertEqual(response.json()["records"], len(prepared))


class PreparedDataReadTests(BaseDataTest):
    """Wersja przygotowana jest JEDYNYM źródłem danych dla aplikacji."""

    def setUp(self):
        super().setUp()
        plik = SimpleUploadedFile("ruch.csv",
                                  imu_csv(seconds=4.0, with_mag=True).encode("utf-8"),
                                  content_type="text/csv")
        odpowiedz = self.client.post(reverse("api_upload_dataset"), {"file": plik})
        self.assertTrue(odpowiedz.json()["prepared"])

        self.dataset = Dataset.objects.get(owner=self.user, filename="ruch.csv")
        self.raw_path = self.tmp_dir / str(self.user.pk) / "ruch.csv"
        self.prepared_path = self.prepared_dir / str(self.user.pk) / "ruch.csv"

    def kolumny_liczbowe(self, path):
        return len(pd.read_csv(path).select_dtypes(include=["number"]).columns)

    def test_wykres_bierze_kolumny_z_wersji_przygotowanej(self):
        # Wersja przygotowana ma kolumny pochodne, więc jest ich WIĘCEJ niż
        # w surowej — po tej liczbie widać, który plik trafił na wykres.
        self.assertGreater(self.kolumny_liczbowe(self.prepared_path),
                           self.kolumny_liczbowe(self.raw_path))

        response = self.client.get(reverse("dashboard", args=["ruch.csv"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["columns_count"],
                         self.kolumny_liczbowe(self.prepared_path))

    def test_bez_wersji_przygotowanej_zestawu_nie_widac(self):
        # Zestawy sprzed tej zmiany (albo takie, którym ktoś skasował plik
        # przygotowany) mają zniknąć z interfejsu, a nie degradować się do
        # surowego pliku. Surowy leży nietknięty — to kopia źródłowa.
        self.prepared_path.unlink()
        self.assertTrue(self.raw_path.exists())

        # Nie ma go na liście…
        self.assertEqual(self.client.get(reverse("api_datasets")).json(), [])

        # …ani pod własnym adresem.
        response = self.client.get(reverse("dashboard", args=["ruch.csv"]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["dataset"])
        self.assertIn("ruch.csv", response.context["error"])

    def test_bez_wersji_przygotowanej_api_odmawia(self):
        self.prepared_path.unlink()
        segment = Segment.objects.create(dataset=self.dataset, start=200, end=1000)

        for url in (reverse("api_dataset_range", args=["ruch.csv"]) + "?x0=0&x1=500",
                    reverse("api_dataset_motion3d", args=["ruch.csv"])
                    + f"?segment={segment.pk}",
                    reverse("api_segments", args=["ruch.csv"])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_animacja_3d_liczy_sie_z_wersji_przygotowanej(self):
        segment = Segment.objects.create(dataset=self.dataset, start=200, end=1000)
        url = (reverse("api_dataset_motion3d", args=["ruch.csv"])
               + f"?segment={segment.pk}")

        # Surowy plik znika — animacja ma się policzyć mimo to. Wcześniej
        # to ona była jedynym miejscem czytającym raw_data, bo filtr psuł
        # w wersji przygotowanej wektor obrotu.
        self.raw_path.unlink()

        body = self.client.get(url).json()
        self.assertEqual(body["meta"]["source"], "fused")
        # Oś czasu bierze się z kolumny `time` dołożonej przez transform —
        # rozpoznanej, a nie założonej (patrz motion3d._axis_from_column).
        self.assertTrue(body["meta"]["time_source"].startswith("time"))

    def test_zakres_w_pelnej_rozdzielczosci_tez_z_przygotowanej(self):
        response = self.client.get(
            reverse("api_dataset_range", args=["ruch.csv"]) + "?x0=0&x1=500")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["series"]),
                         self.kolumny_liczbowe(self.prepared_path))

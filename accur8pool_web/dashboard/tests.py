import shutil
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import motion3d, views
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


def imu_csv(seconds=8.0, with_lin=True, with_rot=True):
    """Ramka w formacie zapisu z zegarka: acc/gyr/rot/linacc + timestamp."""
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

    naglowek = ",".join(kolumny)
    wiersze = np.column_stack(list(kolumny.values()))
    return naglowek + "\n" + "\n".join(",".join(f"{v:.8g}" for v in w) for w in wiersze) + "\n"


class BaseDataTest(TestCase):
    """Zalogowany użytkownik i katalog na pliki CSV poza drzewem projektu.

    views.DATA_DIR jest liczone przy imporcie modułu, więc override_settings
    już na nie nie wpływa — trzeba podmienić samą zmienną.
    """

    def setUp(self):
        super().setUp()
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, True)

        patch = mock.patch.object(views, "DATA_DIR", self.tmp_dir)
        patch.start()
        self.addCleanup(patch.stop)

        self.user = User.objects.create_user("ala", password="tajne-haslo-123")
        self.client.force_login(self.user)

    def upload(self, nazwa, tresc=None):
        """Zapisuje CSV tam, gdzie szuka go widok, i rejestruje Dataset."""
        user_dir = self.tmp_dir / str(self.user.pk)
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

    def test_scena_ma_komplet_sladow_i_klatek(self):
        prep = motion3d.prepare(self.zapisz("scena.csv"))
        wynik = motion3d.build_motion(prep, 1200, 2400)

        # 2 statyczne (rzut, tor) + 6 animowanych (faza, ogon, zegarek,
        # trzy osie) + punkt nadgarstka
        self.assertEqual(len(wynik["figure"]["data"]), 9)
        self.assertEqual(len(wynik["payload"]["pos"]), wynik["meta"]["frames"])
        self.assertEqual(len(wynik["payload"]["t"]), wynik["meta"]["frames"])
        self.assertEqual(len(wynik["payload"]["verts"]), 8)  # bryła zegarka

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

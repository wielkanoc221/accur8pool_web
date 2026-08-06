from unittest import mock
from . import motion3d
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


def imu_csv(seconds=8.0, with_lin=True, with_rot=True):
    """Ramka w formacie zapisu z zegarka: acc/gyr/rot/linacc + timestamp."""
    from scipy.spatial.transform import Rotation as R

    n = int(FS_IMU * seconds)
    t = np.arange(n) / FS_IMU

    ang = 0.6 * np.sin(2 * np.pi * FREQ * t)
    rots = R.from_rotvec(np.column_stack([np.zeros(n), ang, np.zeros(n)]))
    gyr = np.column_stack([np.zeros(n), np.gradient(ang, 1 / FS_IMU), np.zeros(n)])

    pos = np.column_stack([AMPL * np.sin(2 * np.pi * FREQ * t), np.zeros(n), np.zeros(n)])
    a_world = np.column_stack(
        [np.gradient(np.gradient(pos[:, k], 1 / FS_IMU), 1 / FS_IMU) for k in range(3)])
    lin = rots.inv().apply(a_world)
    acc = lin + rots.inv().apply(np.array([0.0, 0.0, 9.80665]))

    kolumny = {
        "accx": acc[:, 0], "accy": acc[:, 1], "accz": acc[:, 2],
        "gyrx": gyr[:, 0], "gyry": gyr[:, 1], "gyrz": gyr[:, 2],
        "timestamp": np.full(n, 1000.0 / FS_IMU),
    }
    if with_lin:
        kolumny.update({"linaccx": lin[:, 0], "linaccy": lin[:, 1], "linaccz": lin[:, 2]})
    if with_rot:
        # rotation vector leci wolniej (50 Hz) i jest forward-fillowany
        krok = int(FS_IMU / 50)
        q = rots.as_quat()[(np.arange(n) // krok) * krok]
        kolumny.update({"rotx": q[:, 0], "roty": q[:, 1], "rotz": q[:, 2]})

    naglowek = ",".join(kolumny)
    wiersze = np.column_stack(list(kolumny.values()))
    return naglowek + "\n" + "\n".join(",".join(f"{v:.8g}" for v in w) for w in wiersze) + "\n"


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

        self.assertEqual(len(wynik["figure"]["data"]), 8)   # 3 statyczne + 5 animowanych
        self.assertEqual(len(wynik["payload"]["pos"]), wynik["meta"]["frames"])
        self.assertEqual(len(wynik["payload"]["verts"]), 8)  # bryła zegarka

        # Kwaterniony muszą być znormalizowane, inaczej bryła w JS się rozjedzie
        normy = np.linalg.norm(np.array(wynik["payload"]["quat"]), axis=1)
        self.assertTrue(np.allclose(normy, 1.0, atol=1e-3))

    def test_maska_uderzenia_zaznacza_podzakres(self):
        prep = motion3d.prepare(self.zapisz("hit.csv"))
        hit = np.array(
            motion3d.build_motion(prep, 1200, 2400, hit_lo=1600, hit_hi=2000)["payload"]["hit"])

        self.assertTrue(hit.any())
        self.assertFalse(hit.all())
        self.assertAlmostEqual(hit.mean(), (2000 - 1600) / (2400 - 1200), delta=0.05)

    def test_bez_podzakresu_nic_nie_jest_podswietlone(self):
        prep = motion3d.prepare(self.zapisz("bez_hit.csv"))
        wynik = motion3d.build_motion(prep, 1200, 2400)

        self.assertFalse(any(wynik["payload"]["hit"]))
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

    def test_liczba_klatek_wynika_z_fps(self):
        prep = motion3d.prepare(self.zapisz("fps.csv"))
        meta = motion3d.build_motion(prep, 400, 400 + int(FS_IMU * 2), fps=25)["meta"]

        self.assertEqual(meta["fps"], 25.0)
        self.assertEqual(meta["frames"], 50)


class Motion3DApiTests(BaseDataTest):

    def setUp(self):
        super().setUp()
        self.upload("ruch.csv", imu_csv())

    def url(self, params=""):
        return reverse("api_dataset_motion3d", args=["ruch.csv"]) + params

    def test_zwraca_scene_i_klatki(self):
        response = self.client.get(self.url("?x0=1200&x1=2400&hit0=1600&hit1=2000&fps=30"))
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(len(body["figure"]["data"]), 8)
        self.assertEqual(len(body["payload"]["t"]), body["meta"]["frames"])
        self.assertEqual(body["meta"]["source"], "fused")

    def test_zakres_bez_podswietlenia_tez_sie_serializuje(self):
        # Bez hit0/hit1 puste ślady zostawały tablicami numpy i JsonResponse
        # wywracał się na nich z 500 — to jest ten przypadek
        response = self.client.get(self.url("?x0=0&x1=2000&fps=30"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(response.json()["payload"]["hit"]))

    def test_dashboard_wlacza_zakladke_3d(self):
        response = self.client.get(reverse("dashboard", args=["ruch.csv"]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["motion3d_ready"])

    def test_plik_bez_imu_nie_wlacza_zakladki(self):
        self.upload("plaski.csv")
        response = self.client.get(reverse("dashboard", args=["plaski.csv"]))
        self.assertFalse(response.context["motion3d_ready"])

    def test_za_dlugi_zakres_to_422_z_komunikatem(self):
        with mock.patch.object(motion3d, "MAX_ROWS", 100):
            response = self.client.get(self.url("?x0=0&x1=2000"))

        self.assertEqual(response.status_code, 422)
        self.assertIn("error", response.json())

    def test_smieciowe_parametry_to_400(self):
        self.assertEqual(self.client.get(self.url("?x0=abc&x1=200")).status_code, 400)

    def test_nie_da_sie_animowac_cudzego_pliku(self):
        User.objects.create_user("bob", password="tajne-haslo-123")
        self.client.force_login(User.objects.get(username="bob"))

        self.assertEqual(self.client.get(self.url("?x0=0&x1=500")).status_code, 404)
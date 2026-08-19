"""Zestaw demonstracyjny i strona startowa.

Dwie rzeczy, których nie widać w testach widoków: co dostaje konto zaraz po
założeniu i dokąd prowadzi goły adres serwisu.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import demo, views
from .models import Dataset, Segment, SubSegment
from .tests import imu_csv

User = get_user_model()

MANIFEST = {
    "name": "demo_uderzenia.csv",
    "file": "demo.csv",
    "segments": [
        {
            "start": 1200,
            "end": 2400,
            "phases": [
                {"phase": SubSegment.PHASE_PREPARATION, "start": 1200, "end": 1500},
                {"phase": SubSegment.PHASE_STRIKE, "start": 1500, "end": 1700},
            ],
        },
        {"start": 3000, "end": 3800, "phases": []},
    ],
}


class DemoTestCase(TestCase):
    """Katalog demo i katalogi danych poza drzewem projektu.

    Konta zakładają się TU, w poszczególnych testach, a nie w setUp —
    zestaw demonstracyjny powstaje razem z użytkownikiem, więc moment
    utworzenia konta jest tym, co się sprawdza.
    """

    def setUp(self):
        super().setUp()
        self.tmp_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_root, True)

        self.demo_dir = self.tmp_root / "demo_data"
        self.prepared_dir = self.tmp_root / "prepared_data"

        for modul, nazwa, katalog in (
                (views, "DATA_DIR", self.tmp_root / "raw_data"),
                (views, "PREPARED_DATA_DIR", self.prepared_dir),
                (demo, "DEMO_DIR", self.demo_dir)):
            patch = mock.patch.object(modul, nazwa, katalog)
            patch.start()
            self.addCleanup(patch.stop)

    def write_demo(self, manifest=None, csv=None):
        """Zawartość katalogu demo — taka, jaką zostawia `export_demo`."""
        self.demo_dir.mkdir(parents=True, exist_ok=True)
        (self.demo_dir / "demo.csv").write_text(csv or imu_csv(), encoding="utf-8")
        (self.demo_dir / demo.MANIFEST_NAME).write_text(
            json.dumps(manifest if manifest is not None else MANIFEST,
                       ensure_ascii=False),
            encoding="utf-8")

    def create_user(self, username="ala"):
        return User.objects.create_user(username, password="tajne-haslo-123")


class DemoInstallTests(DemoTestCase):

    def test_nowe_konto_dostaje_plik_i_segmenty(self):
        self.write_demo()
        user = self.create_user()

        dataset = Dataset.objects.get(owner=user)
        self.assertEqual(dataset.filename, "demo_uderzenia.csv")
        self.assertTrue((self.prepared_dir / str(user.pk) / dataset.filename).exists())

        segments = list(dataset.segments.all())
        self.assertEqual([(s.start, s.end) for s in segments],
                         [(1200, 2400), (3000, 3800)])
        self.assertEqual(
            [(p.phase, p.start, p.end) for p in segments[0].subsegments.all()],
            [(SubSegment.PHASE_PREPARATION, 1200, 1500),
             (SubSegment.PHASE_STRIKE, 1500, 1700)])

    def test_demo_otwiera_sie_na_dashboardzie(self):
        self.write_demo()
        user = self.create_user()
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dataset"]["name"], "demo_uderzenia.csv")
        self.assertIsNotNone(response.context["graph_data"])

        # Segmenty demo mają być widoczne przez to samo API, co własne.
        body = self.client.get(
            reverse("api_segments", args=["demo_uderzenia.csv"])).json()
        self.assertEqual([s["number"] for s in body["segments"]], [1, 2])

    def test_kopia_jest_osobna_dla_kazdego_konta(self):
        self.write_demo()
        ala, ola = self.create_user("ala"), self.create_user("ola")

        Dataset.objects.filter(owner=ala).delete()

        self.assertFalse(Dataset.objects.filter(owner=ala).exists())
        self.assertTrue(Dataset.objects.filter(owner=ola).exists())
        self.assertTrue(
            (self.prepared_dir / str(ola.pk) / "demo_uderzenia.csv").exists())

    def test_bez_demo_konto_zostaje_puste(self):
        user = self.create_user()
        self.assertFalse(Dataset.objects.filter(owner=user).exists())

    def test_nazwa_zestawu_przechodzi_przez_te_sama_sanityzacje_co_upload(self):
        self.write_demo(manifest={"name": "Demo — uderzenia", "segments": []})
        user = self.create_user()

        dataset = Dataset.objects.get(owner=user)
        self.assertEqual(dataset.filename,
                         views.storage_for(user)
                         .canonical_filename("Demo — uderzenia.csv"))
        # …i ta sama nazwa nie zakłada drugiej kopii przy powtórzeniu.
        self.assertIsNone(demo.install_for(user, views.storage_for(user)))

    def test_zepsute_demo_nie_blokuje_zakladania_konta(self):
        self.write_demo(manifest={"segments": [
            {"start": 1, "end": 9,
             "phases": [{"phase": "backswing", "start": 2, "end": 3}]}]})

        with self.assertLogs("dashboard.demo", level="ERROR"):
            user = self.create_user()

        self.assertFalse(Dataset.objects.filter(owner=user).exists())

    def test_brak_pliku_csv_nie_zostawia_polowicznego_zestawu(self):
        self.write_demo()
        (self.demo_dir / "demo.csv").unlink()

        with self.assertLogs("dashboard.demo", level="ERROR"):
            user = self.create_user()

        self.assertFalse(Dataset.objects.filter(owner=user).exists())

    def test_drugi_raz_nie_zaklada_kopii(self):
        self.write_demo()
        user = self.create_user()

        self.assertIsNone(demo.install_for(user, views.storage_for(user)))
        self.assertEqual(Dataset.objects.filter(owner=user).count(), 1)

    def test_rejestracja_przez_formularz_daje_demo(self):
        self.write_demo()
        response = self.client.post(reverse("register"), {
            "username": "nowy",
            "email": "nowy@example.com",
            "password1": "tajne-haslo-123",
            "password2": "tajne-haslo-123",
        })
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(username="nowy")
        self.assertEqual(Dataset.objects.filter(owner=user).count(), 1)


class DemoManifestTests(DemoTestCase):
    """Czytanie manifestu — reguły są te same, co w API segmentów."""

    def load(self, manifest):
        self.write_demo(manifest=manifest)
        return demo.load()

    def test_brak_manifestu_to_brak_demo(self):
        self.assertIsNone(demo.load())
        self.assertFalse(demo.exists())

    def test_faza_przycina_sie_do_segmentu(self):
        source = self.load({
            "segments": [{
                "start": 100, "end": 200,
                "phases": [{"phase": SubSegment.PHASE_STRIKE,
                            "start": 50, "end": 150}],
            }],
        })
        self.assertEqual((source.segments[0].phases[0].start,
                          source.segments[0].phases[0].end), (100, 150))

    def test_nazwa_zestawu_dostaje_rozszerzenie(self):
        self.assertEqual(self.load({"name": "Przykład", "segments": []}).name,
                         "Przykład.csv")

    def test_odrzuca_nieznana_faze(self):
        with self.assertRaises(demo.DemoBroken):
            self.load({"segments": [{"start": 1, "end": 9, "phases": [
                {"phase": "backswing", "start": 2, "end": 3}]}]})

    def test_odrzuca_faze_poza_segmentem(self):
        with self.assertRaises(demo.DemoBroken):
            self.load({"segments": [{"start": 100, "end": 200, "phases": [
                {"phase": SubSegment.PHASE_STRIKE, "start": 10, "end": 20}]}]})

    def test_odrzuca_pusty_zakres(self):
        with self.assertRaises(demo.DemoBroken):
            self.load({"segments": [{"start": 100, "end": 100}]})


class DemoCommandTests(DemoTestCase):
    """`export_demo` i `install_demo` — obieg od zaznaczenia do nowego konta."""

    def setUp(self):
        super().setUp()
        self.author = self.create_user("autor")

        storage = views.storage_for(self.author)
        storage.prepared_dir.mkdir(parents=True, exist_ok=True)
        (storage.prepared_dir / "ruch.csv").write_text(imu_csv(), encoding="utf-8")

        self.dataset = Dataset.objects.create(owner=self.author, filename="ruch.csv")
        segment = Segment.objects.create(dataset=self.dataset, start=1200, end=2400)
        SubSegment.objects.create(segment=segment, phase=SubSegment.PHASE_STRIKE,
                                  start=1500, end=1700)

    def call(self, command, *args, **kwargs):
        from django.core.management import call_command
        from io import StringIO
        call_command(command, *args, stdout=StringIO(), **kwargs)

    def test_eksport_zapisuje_plik_i_segmenty(self):
        self.call("export_demo", "ruch.csv", "--user", "autor", "--name", "Demo")

        source = demo.load()
        self.assertEqual(source.name, "Demo.csv")
        self.assertEqual((source.segments[0].start, source.segments[0].end),
                         (1200, 2400))
        self.assertEqual(source.segments[0].phases[0].phase, SubSegment.PHASE_STRIKE)
        self.assertEqual(source.csv_path.read_text(encoding="utf-8"),
                         (self.prepared_dir / str(self.author.pk) / "ruch.csv")
                         .read_text(encoding="utf-8"))

    def test_eksport_odmawia_dla_nieznanego_zestawu(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.call("export_demo", "nie-ma.csv")

    def test_instalacja_obejmuje_istniejace_konta(self):
        # Konto założone ZANIM powstało demo — dokładnie ten przypadek,
        # dla którego jest to polecenie.
        stary = self.create_user("stary")
        self.call("export_demo", "ruch.csv", "--user", "autor", "--name", "Demo")

        self.call("install_demo")

        self.assertTrue(Dataset.objects.filter(owner=stary, filename="Demo.csv")
                        .exists())
        # Autor demo ma je teraz obok swojego zestawu — nazwy się nie gryzą.
        self.assertEqual(
            set(Dataset.objects.filter(owner=self.author)
                .values_list("filename", flat=True)),
            {"ruch.csv", "Demo.csv"})

    def test_instalacja_nie_dubluje_kopii(self):
        self.call("export_demo", "ruch.csv", "--user", "autor")
        self.call("install_demo")
        self.call("install_demo")

        self.assertEqual(
            Dataset.objects.filter(filename=demo.DEFAULT_DATASET_NAME).count(),
            User.objects.count())


class StronaStartowaTests(TestCase):
    """Goły adres serwisu prowadzi na dashboard; logowanie tylko wtedy, gdy
    naprawdę go brakuje."""

    def test_niezalogowany_idzie_z_dashboardu_na_logowanie(self):
        response = self.client.get("/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[0][0], reverse("dashboard"))
        self.assertIn(reverse("login"), response.redirect_chain[-1][0])
        self.assertTemplateUsed(response, "login.html")

    def test_po_zalogowaniu_wraca_na_dashboard(self):
        User.objects.create_user("ala", password="tajne-haslo-123")

        response = self.client.post(
            self.client.get("/", follow=True).redirect_chain[-1][0],
            {"username": "ala", "password": "tajne-haslo-123"}, follow=True)

        self.assertEqual(response.redirect_chain[-1][0], reverse("dashboard"))
        self.assertTemplateUsed(response, "dashboard.html")

    def test_zalogowany_trafia_wprost_na_dashboard(self):
        user = User.objects.create_user("ola", password="tajne-haslo-123")
        self.client.force_login(user)

        response = self.client.get("/", follow=True)
        self.assertEqual(response.redirect_chain, [(reverse("dashboard"), 302)])
        self.assertTemplateUsed(response, "dashboard.html")

    def test_zalogowany_nie_oglada_formularza_logowania(self):
        user = User.objects.create_user("ewa", password="tajne-haslo-123")
        self.client.force_login(user)

        response = self.client.get(reverse("login"))
        self.assertRedirects(response, reverse("dashboard"),
                             fetch_redirect_response=False)

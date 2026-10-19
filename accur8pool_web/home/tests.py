"""Testy strony głównej.

Strona jest statyczna, więc nie ma tu czego liczyć — sprawdzamy trzy
rzeczy, których zepsucie widać dopiero na produkcji:

  1. goła domena NIE przekierowuje już na logowanie (to była cała
     przyczyna, dla której ta strona powstała),
  2. rysuje się dla gościa i dla zalogowanego, w obu przypadkach
     z właściwym przyciskiem w nagłówku,
  3. podgląd zestawu demonstracyjnego jest wpuszczany do szablonu, a jego
     brak nie wywraca strony.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import views

User = get_user_model()


class HomePageTests(TestCase):

    def setUp(self):
        # Wynik jest zapamiętany na proces, więc test podmieniający ścieżkę
        # musi zacząć od czystego cache — inaczej zobaczyłby podgląd
        # wczytany przez poprzedni test.
        views.demo_preview.cache_clear()
        self.addCleanup(views.demo_preview.cache_clear)

    def test_gola_domena_pokazuje_strone_glowna(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "home.html")

    def test_gosc_dostaje_wejscie_do_logowania_i_rejestracji(self):
        response = self.client.get(reverse("home"))
        content = response.content.decode()

        self.assertIn(reverse("login"), content)
        self.assertIn(reverse("register"), content)

    def test_zalogowany_nie_jest_przekierowywany_i_dostaje_link_do_dashboardu(self):
        User.objects.create_user(username="ala", password="tajne-haslo-123")
        self.client.login(username="ala", password="tajne-haslo-123")

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(reverse("dashboard"), response.content.decode())

    def test_podglad_demo_trafia_do_szablonu(self):
        response = self.client.get(reverse("home"))
        preview = response.context["preview"]

        self.assertIsNotNone(preview, "Brakuje demo_preview.json w aplikacji home.")
        self.assertGreater(response.context["demo_rows"], 0)
        self.assertTrue(preview["series"])
        # Dane wchodzą w HTML przez json_script, więc muszą dać się
        # zserializować — pusty wynik znaczyłby pusty wykres na stronie.
        self.assertIn("demo-preview", response.content.decode())

    def test_podglad_pokrywa_sie_z_zestawem_demonstracyjnym(self):
        """Podgląd jest wycięty z demo.csv, więc musi opisywać ten sam
        zestaw — inaczej strona główna obiecywałaby co innego, niż dostanie
        ktoś po założeniu konta."""
        from dashboard import demo as demo_module

        preview = views.demo_preview()
        manifest = demo_module.load()
        if manifest is None:
            self.skipTest("Nie wgrano zestawu demonstracyjnego.")

        self.assertEqual(len(preview["segments"]), len(manifest.segments))
        for shown, real in zip(preview["segments"], manifest.segments):
            self.assertEqual(shown["start"], real.start)
            self.assertEqual(shown["end"], real.end)

    def test_brak_podgladu_nie_wywraca_strony(self):
        with mock.patch.object(views, "PREVIEW_PATH",
                               views.PREVIEW_PATH.with_name("nie-ma-mnie.json")):
            views.demo_preview.cache_clear()
            response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["preview"])
        self.assertEqual(response.context["demo_segments"], 0)

    def test_zepsuty_podglad_nie_wywraca_strony(self):
        broken = views.PREVIEW_PATH.with_name("zepsuty.json")
        broken.write_text("{to nie jest JSON", encoding="utf-8")
        self.addCleanup(broken.unlink, True)

        with mock.patch.object(views, "PREVIEW_PATH", broken):
            views.demo_preview.cache_clear()
            response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["preview"])

    def test_kolory_faz_zgadzaja_sie_z_modelem(self):
        """Kolory faz są powtórzone w home.js, bo strona główna nie pyta
        serwera o nic. Ten test pilnuje, żeby kopia nie rozjechała się
        z oryginałem — ta sama faza ma mieć ten sam kolor na stronie
        głównej i w aplikacji."""
        from dashboard.models import SubSegment

        script = (views.PREVIEW_PATH.parent / "static" / "js" / "home.js").read_text(
            encoding="utf-8")

        for phase, color in SubSegment.PHASE_COLORS.items():
            self.assertIn(phase, script)
            self.assertIn(color, script,
                          f"home.js nie zna koloru {color} fazy {phase}.")

    def test_podglad_jest_czytany_raz(self):
        views.demo_preview()
        first = views.demo_preview.cache_info().misses

        views.demo_preview()

        self.assertEqual(views.demo_preview.cache_info().misses, first)

    def test_podglad_jest_aktualny_wobec_zestawu_demonstracyjnego(self):
        """Zapisany podgląd musi być tym, co wypisze `export_home_preview`.

        Plik jest wynikiem polecenia, a nie źródłem — kto podmieni demo
        i zapomni je uruchomić, zostawia na stronie głównej wykres
        poprzedniego nagrania. Tę pomyłkę widać wyłącznie okiem, więc
        pilnuje jej test."""
        from dashboard import demo as demo_module

        if demo_module.load() is None:
            self.skipTest("Nie wgrano zestawu demonstracyjnego.")

        with tempfile.TemporaryDirectory() as tmp:
            swiezy = Path(tmp) / "demo_preview.json"
            call_command("export_home_preview", output=str(swiezy), verbosity=0)

            self.assertEqual(
                json.loads(swiezy.read_text(encoding="utf-8")),
                json.loads(views.PREVIEW_PATH.read_text(encoding="utf-8")),
                "home/demo_preview.json jest nieaktualny — uruchom "
                "`manage.py export_home_preview`.")

    def test_podglad_jest_poprawnym_jsonem_na_dysku(self):
        payload = json.loads(views.PREVIEW_PATH.read_text(encoding="utf-8"))

        self.assertIn("series", payload)
        self.assertIn("segments", payload)
        for series in payload["series"]:
            self.assertTrue(series["points"], f"Seria {series['name']} jest pusta.")

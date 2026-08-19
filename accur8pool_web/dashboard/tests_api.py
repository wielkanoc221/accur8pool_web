"""Endpointy API segmentów, zakresów i animacji — pełny obieg HTTP.

tests.py sprawdza rekonstrukcję ruchu i przygotowanie danych; tutaj są
same widoki: która metoda co robi, co odpowiada na śmieciowe wejście
i czego nie wolno zobaczyć bez logowania. Metody PATCH, PUT i DELETE
nie miały wcześniej żadnego pokrycia, a to one zmieniają stan w bazie.
"""

import json

from django.contrib.auth.models import User
from django.urls import reverse

from .models import Dataset, Segment, SubSegment
from .tests import BaseDataTest


class SegmentMutationTests(BaseDataTest):
    """Zmiany zakresów segmentów i faz oraz ich skutki uboczne."""

    def setUp(self):
        super().setUp()
        self.dataset = self.upload("ruch.csv")
        self.segment = Segment.objects.create(dataset=self.dataset,
                                              start=1200, end=2400)

    def seg_url(self, suffix=""):
        return reverse("api_segment_detail",
                       args=["ruch.csv", self.segment.pk]) + suffix

    def phase_url(self, phase):
        return reverse("api_segment_phase", args=["ruch.csv", self.segment.pk, phase])

    def test_patch_zmienia_zakres_i_przycina_fazy(self):
        SubSegment.objects.create(segment=self.segment,
                                  phase=SubSegment.PHASE_STRIKE,
                                  start=1300, end=2300)
        response = self.client.patch(self.seg_url(),
                                     json.dumps({"start": 1500, "end": 2000}),
                                     content_type="application/json")
        self.assertEqual(response.status_code, 200)

        segment = response.json()["segments"][0]
        self.assertEqual((segment["start"], segment["end"]), (1500, 2000))
        self.assertEqual((segment["phases"][0]["start"], segment["phases"][0]["end"]),
                         (1500, 2000))

    def test_patch_usuwa_faze_poza_zakresem(self):
        SubSegment.objects.create(segment=self.segment,
                                  phase=SubSegment.PHASE_STRIKE,
                                  start=1300, end=1400)
        self.client.patch(self.seg_url(), json.dumps({"start": 1500, "end": 2000}),
                          content_type="application/json")
        self.assertFalse(SubSegment.objects.exists())

    def test_put_fazy_przycina_do_segmentu(self):
        response = self.client.put(self.phase_url(SubSegment.PHASE_STRIKE),
                                   json.dumps({"start": 1000, "end": 1600}),
                                   content_type="application/json")
        self.assertEqual(response.status_code, 200)

        phase = response.json()["segments"][0]["phases"][0]
        self.assertEqual((phase["start"], phase["end"]), (1200, 1600))

    def test_put_odwroconego_zaznaczenia_tez_dziala(self):
        response = self.client.put(self.phase_url(SubSegment.PHASE_STRIKE),
                                   json.dumps({"start": 1600.7, "end": 1300.2}),
                                   content_type="application/json")
        phase = response.json()["segments"][0]["phases"][0]
        self.assertEqual((phase["start"], phase["end"]), (1300, 1601))

    def test_faza_poza_segmentem_to_400(self):
        response = self.client.put(self.phase_url(SubSegment.PHASE_STRIKE),
                                   json.dumps({"start": 10, "end": 20}),
                                   content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_nieznana_faza_to_400(self):
        response = self.client.put(self.phase_url("bzdura"),
                                   json.dumps({"start": 1300, "end": 1400}),
                                   content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_delete_fazy_i_segmentu(self):
        SubSegment.objects.create(segment=self.segment,
                                  phase=SubSegment.PHASE_STRIKE,
                                  start=1300, end=1400)
        self.assertEqual(
            self.client.delete(self.phase_url(SubSegment.PHASE_STRIKE)).status_code, 200)
        self.assertFalse(SubSegment.objects.exists())

        self.assertEqual(self.client.delete(self.seg_url()).status_code, 200)
        self.assertFalse(Segment.objects.exists())

    def test_zle_dane_zadania_to_400(self):
        response = self.client.patch(self.seg_url(), "{nie-json",
                                     content_type="application/json")
        self.assertEqual(response.status_code, 400)

        response = self.client.patch(self.seg_url(), json.dumps({"start": 5}),
                                     content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_niedozwolona_metoda_to_405(self):
        self.assertEqual(self.client.post(self.seg_url()).status_code, 405)
        self.assertEqual(
            self.client.get(reverse("api_upload_dataset")).status_code, 405)

    def test_cudzy_segment_to_404(self):
        response = self.client.delete(
            reverse("api_segment_detail", args=["ruch.csv", self.segment.pk + 99]))
        self.assertEqual(response.status_code, 404)

    def test_bez_logowania_api_nie_odpowiada_danymi(self):
        self.client.logout()
        for url in (reverse("api_datasets"),
                    reverse("api_segments", args=["ruch.csv"]),
                    reverse("api_dataset_range", args=["ruch.csv"])):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (302, 403))

    def test_zakres_zwraca_serie_i_granice(self):
        response = self.client.get(
            reverse("api_dataset_range", args=["ruch.csv"]) + "?x0=100&x1=500&buckets=50")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertLessEqual(body["lo"], 100)
        self.assertGreaterEqual(body["hi"], 500)
        self.assertTrue(body["series"])

    def test_smieciowe_parametry_zakresu_to_400(self):
        response = self.client.get(
            reverse("api_dataset_range", args=["ruch.csv"]) + "?x0=abc")
        self.assertEqual(response.status_code, 400)

    def test_parametry_animacji_dzialaja(self):
        url = reverse("api_dataset_motion3d", args=["ruch.csv"])
        body = self.client.get(
            f"{url}?segment={self.segment.pk}&fps=25&smooth=0&watch=2").json()
        self.assertEqual(body["meta"]["stride"], 16)
        self.assertFalse(body["meta"]["smoothed"])

        smieci = self.client.get(f"{url}?segment={self.segment.pk}&watch=abc")
        self.assertEqual(smieci.status_code, 400)

    def test_lista_zestawow_pokazuje_plik(self):
        data = self.client.get(reverse("api_datasets")).json()
        self.assertEqual([item["name"] for item in data], ["ruch.csv"])
        self.assertGreater(data[0]["records"], 0)


class DownloadTests(BaseDataTest):
    """Wydawanie plików ze strony „Pobieranie”.

    BaseDataTest.upload kładzie plik w OBU drzewach, więc domyślnie oba
    warianty istnieją — testy braku wersji surowej kasują ją same.
    """

    def setUp(self):
        super().setUp()
        self.dataset = self.upload("ruch.csv")

    def download_url(self, kind, filename="ruch.csv"):
        return reverse("api_dataset_download", args=[filename, kind])

    def test_lista_pokazuje_oba_warianty(self):
        data = self.client.get(reverse("api_downloads")).json()
        self.assertEqual([item["name"] for item in data], ["ruch.csv"])

        files = data[0]["files"]
        for kind in ("raw", "prepared"):
            with self.subTest(kind=kind):
                self.assertTrue(files[kind]["available"])
                self.assertGreater(files[kind]["size"], 0)

    def test_brak_wersji_surowej_nie_ukrywa_zestawu(self):
        """Zestaw demonstracyjny wchodzi bez surowego pliku — ma zostać
        na liście z jednym wariantem, a nie zniknąć."""
        (self.tmp_dir / str(self.user.pk) / "ruch.csv").unlink()

        files = self.client.get(reverse("api_downloads")).json()[0]["files"]
        self.assertFalse(files["raw"]["available"])
        self.assertIsNone(files["raw"]["url"])
        self.assertTrue(files["prepared"]["available"])

        self.assertEqual(self.client.get(self.download_url("raw")).status_code, 404)

    def test_pobranie_wydaje_plik_jako_zalacznik(self):
        for kind, nazwa in (("raw", "ruch.csv"), ("prepared", "ruch-przygotowany.csv")):
            with self.subTest(kind=kind):
                response = self.client.get(self.download_url(kind))
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'filename="{nazwa}"',
                              response["Content-Disposition"])
                self.assertIn("attachment", response["Content-Disposition"])
                self.assertTrue(b"".join(response.streaming_content))

    def test_zestaw_bez_wersji_przygotowanej_wciaz_do_pobrania(self):
        """Reszta aplikacji takiego zestawu nie widzi (resolve go odrzuca),
        ale przesłanego pliku nie ma powodu użytkownikowi zabierać."""
        (self.prepared_dir / str(self.user.pk) / "ruch.csv").unlink()

        self.assertEqual(self.client.get(self.download_url("raw")).status_code, 200)
        self.assertEqual(self.client.get(self.download_url("prepared")).status_code, 404)

    def test_nieznany_wariant_to_404(self):
        self.assertEqual(self.client.get(self.download_url("bzdura")).status_code, 404)

    def test_cudzy_plik_to_404(self):
        obcy = User.objects.create_user("bob", password="tajne-haslo-123")
        Dataset.objects.create(owner=obcy, filename="obcy.csv")
        katalog = self.prepared_dir / str(obcy.pk)
        katalog.mkdir(parents=True, exist_ok=True)
        (katalog / "obcy.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        response = self.client.get(self.download_url("prepared", "obcy.csv"))
        self.assertEqual(response.status_code, 404)

    def test_bez_logowania_nie_ma_pobierania(self):
        self.client.logout()
        for url in (reverse("api_downloads"), self.download_url("prepared")):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (302, 403))

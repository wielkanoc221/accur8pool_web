"""Zakłada zestaw demonstracyjny na ISTNIEJĄCYCH kontach.

    python manage.py install_demo            # wszystkie konta bez demo
    python manage.py install_demo ala ola    # tylko wskazane

Nowe konta dostają demo same (dashboard/signals.py). To polecenie jest dla
kont założonych wcześniej — i do powtórzenia po `export_demo`, gdy demo się
zmieniło. Konto, które demo już ma, jest pomijane; kto je skasował, dostanie
je z powrotem dopiero tutaj, świadomym uruchomieniem, a nie przy logowaniu.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ... import demo
from ...views import storage_for

User = get_user_model()


class Command(BaseCommand):
    help = "Zakłada zestaw demonstracyjny użytkownikom, którzy go nie mają."

    def add_arguments(self, parser):
        parser.add_argument("usernames", nargs="*",
                            help="Loginy kont. Bez nich — wszystkie konta.")
        parser.add_argument("--demo-dir", dest="demo_dir",
                            help="Katalog z demo (domyślnie ACCUR8POOL_DEMO_DIR).")

    def handle(self, *args, **options):
        # Wyjątek z load() leci tu do góry celowo: przy ręcznym uruchomieniu
        # zepsuty manifest ma się pokazać na ekranie, a nie w logu.
        try:
            source = demo.load(options["demo_dir"])
        except demo.DemoBroken as exc:
            raise CommandError(str(exc))

        if source is None:
            raise CommandError(
                f"Nie ma czego zakładać — brak {demo.MANIFEST_NAME} "
                f"w {options['demo_dir'] or demo.DEMO_DIR}. "
                f"Najpierw `manage.py export_demo`.")

        installed = skipped = 0
        for user in self._users(options["usernames"]):
            if demo.install_for(user, storage_for(user), source):
                installed += 1
                self.stdout.write(f"  + {user}")
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"Założono demo {source.name!r}: {installed} kont "
            f"(pominięto {skipped})."))

    def _users(self, usernames: list):
        if not usernames:
            return User.objects.all()

        users = list(User.objects.filter(username__in=usernames))
        missing = set(usernames) - {user.username for user in users}
        if missing:
            raise CommandError("Nie ma takich kont: " + ", ".join(sorted(missing)))
        return users

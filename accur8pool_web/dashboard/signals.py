"""Podpięcia do zdarzeń Django — na razie jedno: demo dla nowego konta.

Zestaw demonstracyjny zakłada się PRZY TWORZENIU UŻYTKOWNIKA, a nie przy
pierwszym wejściu na dashboard. Dzięki temu jest dokładnie jeden moment,
w którym może powstać, więc usunięte demo nie odradza się przy kolejnym
logowaniu, a widoki nie muszą przy każdym żądaniu pytać bazy, czy aby na
pewno ktoś już je dostał.

Sygnał, a nie dopisek w account.views.register: konta powstają też przez
`createsuperuser` i przez panel admina, a każde z nich ma dostać to samo.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL,
          dispatch_uid="dashboard.install_demo_dataset")
def install_demo_dataset(sender, instance, created, **kwargs):
    if not created:
        return

    # Import w środku funkcji: views ciąga za sobą pandas i cały moduł
    # wykresów, a apps.ready() nie jest miejscem na taki koszt — tym
    # bardziej że przy braku demo nic z tego nie będzie potrzebne.
    from . import demo

    if not demo.exists():
        return

    from . import views

    # install_for sam pilnuje, żeby żaden problem z demo nie wywrócił
    # zakładania konta — tutaj nie ma czego łapać.
    demo.install_for(instance, views.storage_for(instance))

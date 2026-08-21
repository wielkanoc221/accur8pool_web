"""
URL configuration for accur8pool_web project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),

    # Goła domena to STRONA GŁÓWNA — opis projektu, podgląd dashboardu
    # i instrukcja obsługi, dostępne bez logowania.
    #
    # Wcześniej stało tu przekierowanie na dashboard. Dla kogoś, kto ma
    # konto, było wygodne; dla każdego innego oznaczało, że pierwszym
    # (i jedynym) ekranem aplikacji jest formularz logowania — bez słowa
    # o tym, czym ta aplikacja jest i jakich danych oczekuje. Zalogowany
    # nic na tym nie traci: przycisk „Otwórz dashboard” stoi w nagłówku
    # strony głównej, a po samym zalogowaniu i tak ląduje na dashboardzie
    # (LOGIN_REDIRECT_URL).
    path("", include("home.urls")),

    path("", include("account.urls")),
    path('', include('dashboard.urls')),
]

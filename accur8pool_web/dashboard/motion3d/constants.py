"""Stałe rekonstrukcji ruchu 3D — w jednym miejscu, razem z powodem,
dla którego mają akurat taką wartość.

Liczba bez uzasadnienia jest w tym module bezużyteczna: przy każdej
z nich stoi obserwacja z prawdziwych danych, która ją ustawiła. Zmiana
wartości bez ponownego sprawdzenia tej obserwacji to zgadywanie.
"""

G = 9.80665

# ============================================================
#  KOLUMNY WEJŚCIOWE
#  Nazwy porównujemy po lowercase i bez spacji.
# ============================================================

ACC = ("accx", "accy", "accz")
GYR = ("gyrx", "gyry", "gyrz")
ROT = ("rotx", "roty", "rotz")
LIN = ("linaccx", "linaccy", "linaccz")
ROTW = "rotw"
TIME_COLUMNS = ("time", "timestamp")

# ============================================================
#  ROZMIAR OKNA I KLATEK
# ============================================================

# Minimum, żeby cokolwiek dało się policzyć i zróżniczkować.
MIN_ROWS = 8

# Górny limit wierszy w jednym oknie animacji. Nie chodzi o pamięć, tylko
# o sens: model dźwigni opisuje POJEDYNCZY ruch, a nie kwadrans nagrania,
# w którym łokieć zdążył zmienić położenie kilkaset razy.
MAX_ROWS = 200_000

# Sufit klatek na jedną odpowiedź. Powyżej wchodzi decymacja (co k-ta
# próbka) — jedyne miejsce, w którym z animacji wypadają całe pomiary.
# Segment normalnej długości nie dociera tu nigdy: 4000 klatek to
# czterdzieści sekund zapisu przy 100 Hz.
MAX_FRAMES = 4000

# Brak limitu klatek, gdy wołający nic nie poda: klatka = próbka z pliku.
NO_FPS_LIMIT = 0.0

# ============================================================
#  OŚ CZASU
# ============================================================

# Sensowny krok próbkowania w sekundach. Służy do rozpoznania jednostki
# osi czasu: dobra jednostka to ta, przy której typowy odstęp między
# próbkami wpada w ten przedział.
DT_MIN, DT_MAX = 1e-4, 1.0

# Dłuższa przerwa to luka w nagraniu (pauza, uśpiony czujnik), a nie
# próbka trwająca minutę. Skracamy ją, żeby animacja nie stała w miejscu,
# i meldujemy o tym w `meta`.
GAP_DT = 0.25

FALLBACK_FS = 100.0

# Jednostki osi czasu w kolejności prób — patrz TimeAxis.
TIME_UNITS = (("s", 1.0), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9))

# ============================================================
#  MODEL DŹWIGNI
# ============================================================

# Okno wygładzania prędkości kątowej i przyspieszenia przed dopasowaniem.
# Dźwignię wyznacza się z DRUGIEJ pochodnej ruchu, więc wchodzi tu szum
# czujnika pomnożony przez kwadrat częstotliwości. 30 ms to kompromis:
# przepuszcza wszystko, co w uderzeniu istotne (poniżej ~15 Hz), a ucina
# to, co i tak jest szumem kwantyzacji.
SMOOTH_S = 0.03

# Sufit na długość dźwigni. Powyżej metra nie ma już mowy o ruchu ręki —
# taki wynik świadczy o tym, że dopasowanie poszło w szum, a nie o długim
# ramieniu.
#
# Podłogi CELOWO NIE MA. Krótkie ramię to nie jest błąd, tylko obrót
# nadgarstka wokół siebie samego — ruch drobny, ale prawdziwy. Podłoga
# rozdmuchiwałaby go do swojej wysokości i zawyżała `path_cm`, czyli
# kłamała w jedynej liczbie, którą użytkownik odczytuje wprost. Fit,
# z którego nic nie wyszło, wychwytuje test na zerowy wektor w LeverArm.
LEVER_MAX = 0.90

# Regularyzacja Tichonowa jako ułamek śladu macierzy normalnej. Ratuje
# przypadek obrotu wokół jednej osi, w którym jeden kierunek d nie ma
# w danych żadnego pokrycia i bez tego wyszedłby z dzielenia przez zero.
LEVER_RIDGE = 1e-3

# Poniżej tego dopasowania mówimy wprost, że dźwignia tłumaczy zmierzone
# przyspieszenie słabo — ruch miał zapewne dużą składową przesunięcia,
# której z obrotu nie da się odtworzyć.
LEVER_FIT_WARN = 0.35

# Ile razy prędkość kątowa policzona z rotation vectora może rozminąć się
# z żyroskopem, zanim uznamy rotation vector za niezdatny i przejdziemy na
# całkowanie żyroskopu. 1.0 znaczy „błąd wielkości samego sygnału”, czyli
# przebieg, w którym nie ma już informacji — patrz OrientationSolver.
ROT_GYRO_MAX = 1.0

# ============================================================
#  SCENA
# ============================================================

NEUTRAL_COLOR = "#94a3b8"

# Bryła zegarka i osie urządzenia, jako ułamki boku sceny.
WATCH_FRACTION = 0.13
AXIS_FRACTION = 1.7
# Połowa przekątnej bryły z WatchGeometry: sqrt(0.45² + 0.60² + 0.16²).
WATCH_RADIUS = 0.77

# Kolory ścian bryły zegarka. Tarcza jest jasna, spód ciemny, boki
# pośrednie — dzięki temu widać ORIENTACJĘ samej bryły, nawet gdy osie
# urządzenia patrzą prosto w kamerę i skracają się do punktu.
WATCH_TOP = "#e2e8f0"
WATCH_SIDE = "#475569"
WATCH_BOTTOM = "#1e293b"

# Podłoga boku sceny w centymetrach. Sześcian musi mieć jakiś minimalny
# rozmiar, bo przy ręce stojącej w miejscu inaczej rozciąga sam szum na
# cały ekran.
MIN_SPAN_CM = 1.0

# Ile klatek przerwy między sąsiednimi fazami traktujemy jako
# niedokładność zaznaczenia, a nie jako celową dziurę. Przy 100 Hz trzy
# klatki to 30 ms — poniżej progu, w którym ktokolwiek celowo zostawiłby
# odstęp, przeciągając myszą po wykresie.
MAX_PHASE_GAP = 3

# Ten sam krój co reszta interfejsu (patrz charts.PLOT_FONT).
PLOT_FONT = ("Inter, system-ui, -apple-system, 'Segoe UI', Roboto, "
             "'Helvetica Neue', Arial, sans-serif")

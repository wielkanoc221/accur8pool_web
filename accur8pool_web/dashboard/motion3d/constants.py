"""Stałe rekonstrukcji ruchu 3D — w jednym miejscu, razem z powodem,
dla którego mają akurat taką wartość.

Liczba bez uzasadnienia jest w tym module bezużyteczna: przy każdej
z nich stoi obserwacja z prawdziwych danych, która ją ustawiła. Zmiana
wartości bez ponownego sprawdzenia tej obserwacji to zgadywanie.
"""

import math

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
#  CAŁKOWANIE TORU
# ============================================================

# Okno wygładzania prędkości kątowej. 30 ms to kompromis: przepuszcza
# wszystko, co w uderzeniu istotne (poniżej ~15 Hz), a ucina to, co i tak
# jest szumem kwantyzacji.
SMOOTH_S = 0.03

# Próg filtru górnoprzepustowego, który trzyma podwójne całkowanie przy
# zerze. Wszystko wolniejsze od tej częstotliwości uznajemy za dryf
# całkowania, a nie za ruch ręki.
#
# 0.22 Hz to 4.5 sekundy — DOBRANE POMIAREM, nie przyjęte z góry.
#
# Kalibracja poszła na zapisie ruchu po prowadnicy (tam i z powrotem,
# okres ok. 3 s). Długości prowadnicy nie znamy, ale znamy jej fizykę:
# ma DWA KOŃCE, więc punkty zwrotne muszą się skupiać w dwóch miejscach.
# Za wysoki próg ściąga je do środka, rozmywa i mnoży:
#
#   próg     punktów zwrotnych   skok      rozrzut końców
#   0.20 Hz        11           55.6 cm        12.1%
#   0.22 Hz        11           59.3 cm        10.9%   <- minimum
#   0.25 Hz        12           61.5 cm        14.4%
#   0.30 Hz        12           54.8 cm        15.1%
#   0.40 Hz        16           30.1 cm        24.7%   <- poprzednia wartość
#   0.60 Hz        30            8.8 cm        40.8%
#
# Przy 0.4 Hz filtr zjadał połowę skoku i WYMYŚLAŁ punkty zwrotne —
# szesnaście zamiast jedenastu — czyli tnie pojedynczy przejazd na kawałki.
# Na ekranie wygląda to jak zatrzymanie i zawrócenie w środku płynnego
# ruchu; stąd wzięło się zgłoszenie „w środku jakby się zatrzymuje”.
#
# Niżej niż 0.18 Hz nie schodzimy: prostoliniowość toru, który fizycznie
# jest prostą, spada tam z 99% do 94.6% — filtr przestaje nadążać za
# dryfem i zaczyna go wpuszczać do obrazu.
DRIFT_CUTOFF_HZ = 0.22

# Powyżej tego stosunku (usunięty dryf / rozpiętość toru) mówimy wprost,
# że kształt toru jest w większości dziełem filtru, a nie pomiaru.
# Wartość 0.5 znaczy „filtr usunął połowę tego, co zostało na ekranie”.
DRIFT_WARN = 0.5

# Ile z każdego końca segmentu oglądamy, szukając spoczynku, i jaki ułamek
# szczytowego przyspieszenia wolno tam zastać, żeby uznać to za spoczynek
# (patrz trajectory._ends_at_rest).
#
# 0.1 s przy 100 Hz to dziesięć próbek — dość, żeby nie dać się zmylić
# pojedynczemu przejściu przyspieszenia przez zero w chwili największej
# prędkości, i wciąż mało wobec zamachu trwającego ułamek sekundy.
#
# 15% szczytu: zaznaczenie zrobione myszą nigdy nie trafia dokładnie
# w moment zatrzymania, więc próg musi znieść trochę ruchu resztkowego.
# Wyżej zaczęłoby uznawać za spoczynek wycinek wyjęty ze środka zamachu
# i zawyżać prędkość dwukrotnie.
REST_SPAN_S = 0.10
REST_RATIO = 0.15

# Najdłuższy segment, na którym ufamy samemu warunkowi spoczynku (bez
# filtru na pozycji). Powyżej wchodzi filtr, nawet gdy końce stoją.
#
# Próg zmierzony na prawdziwym zapisie (99.8 Hz), oknami rosnącej długości
# zaczynającymi się w spoczynku — rozrzut toru z kotwicy kontra z filtru:
#
#     2 s      7 cm : 6 cm      zgodne
#     4 s     48 cm : 25 cm     zgodne
#     6 s     42 cm : 25 cm     zgodne
#     8 s     62 cm : 25 cm     kotwica zaczyna odjeżdżać
#    10 s    119 cm : 25 cm     odjechała
#    17 s   1145 cm : 25 cm     tor ucieka w linii prostej
#
# Kotwica odejmuje z prędkości PROSTĄ, więc kasuje tylko dryf narastający
# równomiernie. Prawdziwy dryf błądzi — jego endpoint bywa bliski zera
# (mierzone 0.021 m/s po 29 s), a mimo to całka po drodze daje dziesiątki
# metrów. Dlatego kryterium jest czasowe: nie da się go zastąpić pomiarem
# prędkości na końcu okna, bo ta prędkość o niczym nie świadczy.
ANCHOR_MAX_S = 5.0

# Ile razy prędkość kątowa policzona z rotation vectora może rozminąć się
# z żyroskopem, zanim uznamy rotation vector za niezdatny i przejdziemy na
# całkowanie żyroskopu. 1.0 znaczy „błąd wielkości samego sygnału”, czyli
# przebieg, w którym nie ma już informacji — patrz OrientationSolver.
ROT_GYRO_MAX = 1.0

# ============================================================
#  SCENA
# ============================================================

NEUTRAL_COLOR = "#94a3b8"

# Koperta zegarka: WALEC o okrągłej tarczy, bo taki jest zegarek, z
# którego biorą się nagrania. Średnica i grubość w centymetrach.
#
# To są CENTYMETRY SCENY, te same, w których liczony jest tor ruchu —
# więc bryła ma na ekranie prawdziwą proporcję do zamachu: przy szerokim
# ruchu jest mała, przy obrocie nadgarstka w miejscu wypełnia kadr.
#
# Wcześniej rozmiar był ułamkiem rozpiętości ruchu (0.13 × spread), przez
# co stosunek zegarka do kadru wychodził IDENTYCZNY na każdym nagraniu
# i z obrazu nie dawało się odczytać, jak duży był ruch. Na typowym
# segmencie (scena ~41 cm) stara formuła dawała zresztą 4.3–4.5 cm, czyli
# prawie dokładnie tyle, ile ma prawdziwy zegarek — dlatego po tej
# zmianie sam rozmiar prawie nie drgnął. Widać ją dopiero na skrajnych
# oknach: całe nagranie demo (scena 55 cm) dawało kopertę 5.8 cm.
WATCH_DIAMETER_CM = 4.4
WATCH_THICKNESS_CM = 1.2

# Na ile wycinków dzielimy obwód tarczy. 32 to próg, powyżej którego oko
# przestaje widzieć krawędzie wielokąta przy dowolnym przybliżeniu sceny,
# a koszt jest żaden: siatka idzie do przeglądarki RAZ (66 wierzchołków),
# potem lecą już same współrzędne, których liczba nie zależy od gęstości
# podziału.
WATCH_SEGMENTS = 32

# Prostopadłościan opisany na kopercie — w tych wymiarach mieści się cała
# bryła, cokolwiek by z nią zrobić obrotem.
WATCH_SIZE_CM = (WATCH_DIAMETER_CM, WATCH_DIAMETER_CM, WATCH_THICKNESS_CM)

# Średnica tarczy — jednostka odniesienia dla osi urządzenia.
WATCH_LENGTH_CM = WATCH_DIAMETER_CM

# Najdalszy punkt bryły od jej środka: krawędź tarczy. Dla walca to
# przeciwprostokątna promienia i połowy grubości — mniej niż połowa
# przekątnej prostopadłościanu, bo walec nie ma narożników.
WATCH_RADIUS_CM = math.hypot(WATCH_DIAMETER_CM / 2.0, WATCH_THICKNESS_CM / 2.0)

# Długość rysowanych osi urządzenia, w wielokrotności średnicy tarczy.
AXIS_FRACTION = 1.7

# Kolory bryły zegarka. Tarcza jasna, spód ciemny, bok pośredni — dzięki
# temu widać ORIENTACJĘ samej bryły, nawet gdy osie urządzenia patrzą
# prosto w kamerę i skracają się do punktu.
WATCH_TOP = "#e2e8f0"
WATCH_SIDE = "#475569"
WATCH_BOTTOM = "#1e293b"

# Znacznik „godziny 12" — ciemny wycinek tarczy i przylegający do niego
# kawałek boku, po stronie +Y (wzdłuż przedramienia, w stronę dłoni).
#
# Walec jest obrotowo symetryczny wokół własnej osi, więc BEZ TEGO obrót
# zegarka wokół tarczy byłby na ekranie niewidoczny — a to jedna z trzech
# osi ruchu nadgarstka. Prostopadłościan pokazywał ją sam swoim kształtem;
# okrągła koperta musi ją pokazać kolorem. Znacznik obejmuje tarczę
# I bok, żeby był widoczny niezależnie od tego, czy zegarek jest w danej
# chwili zwrócony do kamery tarczą, czy krawędzią.
#
# Szeroki na szósta część tarczy, a nie wąska kreska: zegarek zajmuje
# jakąś dziesiątą część kadru, więc kreska szerokości kilku stopni to na
# ekranie kilka pikseli i przy obrocie miga, zamiast go pokazywać.
# Faktyczna szerokość zaokrągla się do siatki WATCH_SEGMENTS.
WATCH_MARK_DEGREES = 60.0

# MIN_SPAN_CM (podłoga boku sceny) stała tu do czasu, gdy rozmiar bryły
# brał się z rozpiętości ruchu i przy ręce stojącej w miejscu schodził do
# zera. Teraz koperta ma stały rozmiar w centymetrach, więc margines
# sceny nigdy nie jest zerowy i podłoga nie ma czego ratować.

# Ile klatek przerwy między sąsiednimi fazami traktujemy jako
# niedokładność zaznaczenia, a nie jako celową dziurę. Przy 100 Hz trzy
# klatki to 30 ms — poniżej progu, w którym ktokolwiek celowo zostawiłby
# odstęp, przeciągając myszą po wykresie.
MAX_PHASE_GAP = 3

# Ten sam krój co reszta interfejsu (patrz charts.PLOT_FONT).
PLOT_FONT = ("Inter, system-ui, -apple-system, 'Segoe UI', Roboto, "
             "'Helvetica Neue', Arial, sans-serif")

/* ============================================================
   ACCUR8POOL — animacja ruchu 3D

   Ładuj PO dashboard.js (window.a8Chart) i PO segments.js
   (window.a8Segments), a PRZED fullscreen.js.

   ODTWARZANIE JEST STEROWANE ZEGAREM, NIE KLATKAMI
   ------------------------------------------------
   To jest sedno całego pliku. Każda klatka to prawdziwa próbka z CSV ze
   swoim ZMIERZONYM czasem — a czujnik próbkuje nierówno, więc klatki nie
   leżą w równym rastrze. Pętla odtwarzania pyta performance.now() o to,
   ile sekund minęło NAPRAWDĘ, i dopiero z tego wyszukuje numer klatki
   (frameForTime). Skutek: sekunda animacji to sekunda z pliku CSV,
   niezależnie od tego, czy przeglądarka rysuje 120 klatek na sekundę,
   czy 12. Naiwna pętla „narysuj następną klatkę przy każdym
   requestAnimationFrame” daje tempo zależne od monitora i od obciążenia
   karty — i to jest dokładnie ten błąd, przez który ruch nie zgadzał się
   z czasem.

   PODZIAŁ PRACY Z SERWEREM
   ------------------------
   Serwer liczy tor i orientację i tnie ruch na fazy — wraca gotowa scena
   Plotly, w której ślady statyczne (cały tor, jego rzut na podłogę) są
   narysowane raz na zawsze. Przeglądarka rusza co klatkę pięć śladów:
   ogon, bryłę zegarka i trzy osie urządzenia. Ich numery przychodzą
   w payload.dynamic — nie zgadujemy ich tutaj.

   KAMERA NALEŻY DO WIDZA, NIE DO DANYCH
   -------------------------------------
   Obrót i przybliżenie sceny są pamiętane po stronie przeglądarki
   i przeżywają podmianę segmentu, przeliczenie po zmianie faz, powrót
   na zakładkę oraz przeładowanie strony. Kamera z layoutu serwera jest
   tylko ustawieniem startowym dla kogoś, kto jeszcze nic nie wybrał.
   Szczegóły w sekcji KAMERA niżej.
   ============================================================ */

document.addEventListener('DOMContentLoaded', function () {

    const els = {
        tabs: Array.prototype.slice.call(document.querySelectorAll('.view-tab[data-view]')),
        panel2d: document.getElementById('view-2d'),
        panel3d: document.getElementById('view-3d'),

        plot: document.getElementById('graph3d'),
        state: document.getElementById('m3d-state'),
        stateText: document.getElementById('m3d-state-text'),
        label: document.getElementById('m3d-label'),

        source: document.getElementById('m3d-source'),
        resetView: document.getElementById('m3d-reset-view'),
        play: document.getElementById('m3d-play'),
        playLabel: document.getElementById('m3d-play-label'),
        loop: document.getElementById('m3d-loop'),
        speed: document.getElementById('m3d-speed'),
        seek: document.getElementById('m3d-seek'),
        clock: document.getElementById('m3d-clock'),
        phase: document.getElementById('m3d-phase'),

        statTime: document.getElementById('m3d-stat-time'),
        statPath: document.getElementById('m3d-stat-path'),
        statVmax: document.getElementById('m3d-stat-vmax')
    };

    // ============================================================
    //  ZAKŁADKI 2D / 3D
    // ============================================================

    let activeView = '2d';

    function resizePlot(el) {
        if (window.Plotly && el && el.data) Plotly.Plots.resize(el);
    }

    function showView(view) {
        if (view === activeView) return;
        activeView = view;

        els.tabs.forEach(tab => {
            const on = tab.dataset.view === view;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-selected', String(on));
        });

        if (els.panel2d) {
            els.panel2d.hidden = view !== '2d';
            els.panel2d.classList.toggle('is-active', view === '2d');
        }
        if (els.panel3d) {
            els.panel3d.hidden = view !== '3d';
            els.panel3d.classList.toggle('is-active', view === '3d');
        }

        // Plotly nie zna wymiarów kontenera, który był ukryty — bez tego
        // wykres po przełączeniu ma szerokość z momentu utworzenia.
        requestAnimationFrame(() => {
            resizePlot(document.getElementById('graph'));
            resizePlot(els.plot);
        });

        if (view === '3d') {
            ensureLoaded();
        } else {
            pause();
        }
    }

    els.tabs.forEach(tab => {
        tab.addEventListener('click', function () {
            if (tab.disabled) return;
            showView(tab.dataset.view);
        });
    });

    // Dalej wszystko dotyczy już samej sceny 3D. Bez niej (plik bez IMU,
    // brak wybranego zestawu danych) zostają same zakładki.
    function readJson(id, fallback) {
        const el = document.getElementById(id);
        if (!el) return fallback;
        try { return JSON.parse(el.textContent); } catch (err) { return fallback; }
    }

    /** Lokalna kopia — dashboard.html nie ładuje common.js, a nazwy
     *  segmentów trafiają do <option> przez innerHTML. */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    const datasetId = window.currentDatasetId;

    if (!els.plot || !datasetId || !readJson('motion3d-ready', false)) return;

    // ============================================================
    //  STAN
    // ============================================================

    const TRAIL_SECONDS = 0.4;   // jak długi ogon ciągnie się za nadgarstkiem

    const BRAK_SEGMENTOW =
        'Zaznacz uderzenie na wykresie 2D — animacja liczy się dla ' +
        'pojedynczego segmentu, nie dla całego nagrania.';

    let scene = null;        // {figure, payload, meta} — to, co gra teraz
    let plotted = false;     // czy Plotly.newPlot już się wykonał
    let loading = false;
    let lastKey = null;      // klucz ostatniego udanego żądania

    let playing = false;
    let clockStart = 0;      // performance.now() w chwili startu
    let timeAtStart = 0;     // pozycja w nagraniu w chwili startu [s]
    let currentTime = 0;     // pozycja w nagraniu [s]
    let lastFrame = -1;      // ostatnia NARYSOWANA klatka
    let lastPhase = null;    // numer podświetlonej fazy (null = nic nie rysowano)
    let raf = null;

    // Uderzenie trwa ułamek sekundy — obejrzane raz i w tempie 1× jest
    // mrugnięciem. Zapętlenie jest więc stanem domyślnym: ruch powtarza
    // się tak długo, jak długo się na niego patrzy, i dopiero z kilku
    // przebiegów widać, co ręka właściwie zrobiła.
    let looping = true;

    const cache = new Map();

    // ============================================================
    //  KAMERA
    //
    //  Ustawienie kamery jest STANEM UŻYTKOWNIKA, nie częścią danych.
    //  Serwer przysyła ją w każdej scenie, bo musi mieć jakieś
    //  ustawienie startowe, ale gdy widz raz obrócił albo przybliżył
    //  scenę, jego wybór wygrywa ze wszystkim, co przyjdzie później:
    //  z podmianą segmentu, z przeliczeniem po zmianie faz i z
    //  powrotem na zakładkę 3D. Porównywanie dwóch uderzeń polega na
    //  oglądaniu ich POD TYM SAMYM KĄTEM — animacja, która przy każdym
    //  przełączeniu wraca do widoku domyślnego, robi z tego zgadywankę.
    //
    //  Samo `uirevision` w layoucie tu nie wystarcza: trzyma widok przy
    //  Plotly.react, ale nie przeżywa przeładowania strony ani sytuacji,
    //  w której wykres trzeba postawić od nowa (Plotly.newPlot).
    //  Dlatego kamerę pamiętamy tutaj i wstawiamy do layoutu sami.
    // ============================================================

    const CAMERA_KEY = 'a8.motion3d.camera';

    let camera = null;         // ostatni widok wybrany przez użytkownika
    let cameraDefault = null;  // widok domyślny z pierwszej sceny — do resetu
    let cameraHooked = false;  // czy nasłuch na plotly_relayout już wisi
    let applyingCamera = false;

    function readCamera() {
        try {
            const raw = sessionStorage.getItem(CAMERA_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (err) {
            return null;   // tryb prywatny albo zablokowany magazyn
        }
    }

    function saveCamera(cam) {
        camera = cam;
        try {
            sessionStorage.setItem(CAMERA_KEY, JSON.stringify(cam));
        } catch (err) {
            /* Zapis może się nie udać i to nie jest powód, żeby przestać
               pamiętać widok w tej karcie — `camera` działa dalej. */
        }
    }

    camera = readCamera();

    /** Layout sceny z kamerą użytkownika w miejscu domyślnej. */
    function layoutWithCamera(layout) {
        const scene = Object.assign({}, layout.scene);
        if (!cameraDefault && scene.camera) cameraDefault = scene.camera;
        if (camera) scene.camera = camera;
        return Object.assign({}, layout, { scene: scene });
    }

    /** Zapamiętuje kamerę po każdym obrocie i przybliżeniu sceny. */
    function hookCamera() {
        if (cameraHooked || !els.plot.on) return;
        cameraHooked = true;

        els.plot.on('plotly_relayout', function (ev) {
            if (applyingCamera) return;
            // Plotly melduje kamerę raz jako klucz ze ścieżką, raz jako
            // zagnieżdżony obiekt — zależnie od tego, co ją zmieniło.
            // Gdy nie ma jej w zdarzeniu (np. samo przeskalowanie
            // kontenera), bierzemy bieżącą z layoutu wykresu: jest wtedy
            // ta sama, więc zapis niczego nie psuje.
            const cam =
                (ev && (ev['scene.camera'] || (ev.scene && ev.scene.camera))) ||
                (els.plot.layout && els.plot.layout.scene &&
                 els.plot.layout.scene.camera);
            if (cam) saveCamera(cam);
        });
    }

    function resetCamera() {
        camera = null;
        try { sessionStorage.removeItem(CAMERA_KEY); } catch (err) { /* jw. */ }
        if (!plotted || !cameraDefault) return;

        // Reset idzie przez relayout, a nie przez podanie kamery w react:
        // przy niezmienionym `uirevision` Plotly celowo IGNORUJE kamerę
        // z nowego layoutu, żeby nie kasować tego, co ustawił widz.
        // Relayout jest jawną zmianą i wygrywa — o to nam tutaj chodzi.
        applyingCamera = true;
        Promise.resolve(Plotly.relayout(els.plot, { 'scene.camera': cameraDefault }))
            .catch(err => console.error('Plotly 3D:', err))
            .then(function () { applyingCamera = false; });
    }

    // ============================================================
    //  MATEMATYKA
    // ============================================================

    /** Obrót wektora kwaternionem q = [w, x, y, z]. */
    function rotate(q, vx, vy, vz, out) {
        const w = q[0], x = q[1], y = q[2], z = q[3];
        const tx = 2 * (y * vz - z * vy);
        const ty = 2 * (z * vx - x * vz);
        const tz = 2 * (x * vy - y * vx);
        out[0] = vx + w * tx + (y * tz - z * ty);
        out[1] = vy + w * ty + (z * tx - x * tz);
        out[2] = vz + w * tz + (x * ty - y * tx);
        return out;
    }

    const scratch = [0, 0, 0];

    // ============================================================
    //  POBIERANIE
    // ============================================================

    /** Wybrane uderzenie albo null. Zakres animacji bierze się z segmentu
     *  i tylko z niego — patrz komentarz przy api_dataset_motion3d.
     *  Pozycja z podwójnego całkowania trzyma się na odcinku długości
     *  uderzenia; puszczona na całym nagraniu pokazuje ruch, którego nie było. */
    function currentRequest() {
        const wybor = els.source ? els.source.value : '';
        if (!wybor) return null;

        const seg = window.a8Segments ? window.a8Segments.byId(Number(wybor)) : null;
        if (!seg) return null;

        return { segment: seg.id, name: 'Segment ' + seg.name };
    }

    function requestUrl(req) {
        // Celowo BEZ parametru `fps`. Serwer oddaje wtedy każdą próbkę
        // z pliku jako osobną klatkę, a nie przepróbkowuje na okrągłe
        // 60 kl/s — animacja pokazuje to, co czujnik zmierzył, razem
        // z nierównym odstępem między pomiarami.
        const params = new URLSearchParams({ segment: String(req.segment) });
        return '/api/datasets/' + encodeURIComponent(datasetId) + '/motion3d/?' + params;
    }

    /** Komunikat zamiast sceny. Zawsze zdejmuje bieżącą animację —
     *  inaczej odtwarzacz chodziłby dalej po niewidocznym wykresie,
     *  a suwak pokazywał pozycję w nagraniu, którego już nie ma. */
    function setState(text, busy) {
        pause();
        scene = null;
        lastFrame = -1;
        lastPhase = null;
        currentTime = 0;

        if (els.stateText) els.stateText.textContent = text;
        if (els.state) {
            els.state.hidden = false;
            els.state.classList.toggle('is-busy', !!busy);
        }
        els.plot.hidden = true;
        setControlsEnabled(false);
    }

    function hideState() {
        if (els.state) els.state.hidden = true;
        els.plot.hidden = false;
    }

    function setControlsEnabled(on) {
        if (els.play) els.play.disabled = !on;
        if (els.seek) els.seek.disabled = !on;
    }

    async function load(force) {
        const req = currentRequest();
        if (!req) {
            setState(BRAK_SEGMENTOW, false);
            lastKey = null;
            return;
        }

        const url = requestUrl(req);

        if (!force && url === lastKey && scene) return;
        if (loading) return;

        if (cache.has(url)) {
            apply(cache.get(url), req);
            lastKey = url;
            return;
        }

        loading = true;
        setState('Liczenie ruchu 3D…', true);

        try {
            const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const body = await res.json().catch(() => ({}));

            if (!res.ok) {
                setState(body.error || ('Serwer zwrócił błąd ' + res.status), false);
                lastKey = null;
                return;
            }

            // Kilkanaście wpisów × kilkaset kB to już zauważalna pamięć,
            // a i tak wraca się zwykle do ostatnich kilku uderzeń.
            if (cache.size > 12) cache.clear();
            cache.set(url, body);

            apply(body, req);
            lastKey = url;

        } catch (err) {
            console.error('Animacja 3D:', err);
            setState('Nie udało się pobrać animacji 3D.', false);
            lastKey = null;
        } finally {
            loading = false;
        }
    }

    function ensureLoaded() {
        if (activeView !== '3d') return;
        load(false);
    }

    // ============================================================
    //  SCENA
    // ============================================================

    function apply(body, req) {
        pause();
        scene = body;
        lastFrame = -1;      // nowa scena — poprzedni numer klatki nic nie znaczy
        lastPhase = null;
        currentTime = 0;
        hideState();

        const meta = body.meta;
        const payload = body.payload;

        // Kamera z layoutu serwera jest tylko ustawieniem startowym —
        // gdy widz coś już wybrał, wchodzi jego widok.
        const layout = layoutWithCamera(body.figure.layout);

        const rysuj = plotted
            ? Plotly.react(els.plot, body.figure.data, layout)
            : Plotly.newPlot(els.plot, body.figure.data, layout, {
                  responsive: true,
                  displayModeBar: false
              });

        rysuj.then(function () {
            plotted = true;
            hookCamera();
            // Trójkąty bryły zegarka nie zmieniają się przez całą animację,
            // więc idą raz — co klatkę lecą już same współrzędne wierzchołków.
            return Plotly.restyle(els.plot, {
                i: [payload.faces.i],
                j: [payload.faces.j],
                k: [payload.faces.k]
            }, [payload.dynamic.watch]);
        }).then(function () {
            setControlsEnabled(true);
            seekTo(0);
        }).catch(err => console.error('Plotly 3D:', err));

        // ---- opisy ----
        // Podtytuł ma mówić wprost, co jest POMIAREM, a co rekonstrukcją.
        // Orientacja jest mierzona; tor nadgarstka powstaje z dopasowanej
        // dźwigni, więc razem z nim idzie jakość tego dopasowania i bok
        // sceny — bez nich nie da się ocenić, czy ogląda się centymetry,
        // czy milimetry, ani czy model w ogóle miał się o co zaczepić.
        if (els.label) {
            const czesci = [req.name, meta.label];
            if (meta.span_cm) czesci.push('scena ' + meta.span_cm.toFixed(1) + ' cm');
            if (meta.stride > 1) czesci.push('co ' + meta.stride + '. próbka');
            if (meta.gaps) czesci.push(meta.gaps + ' × przerwa w nagraniu (skrócona)');
            // Zgodność wektora obrotu z żyroskopem — tłumaczy szarpiącą
            // się bryłę zegarka, więc ma być widoczna, a nie tylko
            // w logach. Zastrzeżenia o pochodzeniu pliku już nie ma:
            // źródłem jest zawsze wersja przygotowana.
            if (meta.rot_vs_gyro !== null && meta.rot_vs_gyro > 0.35) {
                czesci.push('rozjazd z żyroskopem ' +
                            (meta.rot_vs_gyro * 100).toFixed(0) + '%');
            }
            els.label.textContent = czesci.join(' · ');
        }

        if (els.statTime) els.statTime.textContent = meta.duration.toFixed(2) + ' s';
        if (els.statPath) els.statPath.textContent = meta.path_cm.toFixed(1) + ' cm';
        if (els.statVmax) els.statVmax.textContent = meta.v_max.toFixed(2) + ' m/s';
    }

    /** Rysuje klatkę o podanym numerze. Cała praca na klatkę jest tutaj. */
    function drawFrame(nr) {
        if (!scene || nr === lastFrame) return;
        lastFrame = nr;

        const p = scene.payload;
        const pos = p.pos[nr];
        const q = p.quat[nr];
        const dyn = p.dynamic;
        const len = p.axis_len;

        // ---- bryła zegarka ----
        const vx = new Array(p.verts.length);
        const vy = new Array(p.verts.length);
        const vz = new Array(p.verts.length);
        for (let i = 0; i < p.verts.length; i++) {
            const v = p.verts[i];
            rotate(q, v[0], v[1], v[2], scratch);
            vx[i] = pos[0] + scratch[0];
            vy[i] = pos[1] + scratch[1];
            vz[i] = pos[2] + scratch[2];
        }

        // ---- trzy osie urządzenia ----
        const osie = [];
        const jednostki = [[len, 0, 0], [0, len, 0], [0, 0, len]];
        for (let a = 0; a < 3; a++) {
            const u = jednostki[a];
            rotate(q, u[0], u[1], u[2], scratch);
            osie.push({
                x: [pos[0], pos[0] + scratch[0]],
                y: [pos[1], pos[1] + scratch[1]],
                z: [pos[2], pos[2] + scratch[2]]
            });
        }

        // ---- ogon: ostatnie TRAIL_SECONDS toru ----
        // Liczone po CZASIE, nie po liczbie klatek — odstępy między
        // próbkami nie są równe, więc stała liczba klatek dawałaby ogon
        // raz dłuższy, raz krótszy.
        const od = frameForTime(p.t[nr] - TRAIL_SECONDS);
        const tx = [], ty = [], tz = [];
        for (let i = od; i <= nr; i++) {
            tx.push(p.pos[i][0]);
            ty.push(p.pos[i][1]);
            tz.push(p.pos[i][2]);
        }

        // Jedno wywołanie na całą klatkę. Plotly przy każdym restyle
        // przebudowuje scenę WebGL, więc pięć osobnych wywołań to pięć
        // przebudów zamiast jednej — i to widać jako szarpanie.
        Plotly.restyle(els.plot, {
            x: [tx, vx, osie[0].x, osie[1].x, osie[2].x],
            y: [ty, vy, osie[0].y, osie[1].y, osie[2].y],
            z: [tz, vz, osie[0].z, osie[1].z, osie[2].z]
        }, [dyn.trail, dyn.watch, dyn.axes[0], dyn.axes[1], dyn.axes[2]]);

        highlightPhase(p.phase[nr]);

        updateReadout(nr);
    }

    /** Podświetla odcinek toru należący do trwającej fazy.
     *
     *  Cały tor jest szary — kolor dostaje tylko ta faza, która właśnie
     *  się odbywa. Odrysowanie idzie WYŁĄCZNIE przy zmianie fazy, czyli
     *  kilka razy na całe uderzenie, a nie sześćdziesiąt razy na sekundę:
     *  podmiana koloru linii jest w Plotly znacznie droższa niż podmiana
     *  samych współrzędnych. */
    function highlightPhase(nrFazy) {
        if (nrFazy === lastPhase) return;
        lastPhase = nrFazy;

        const p = scene.payload;
        const span = nrFazy >= 0 ? p.spans[nrFazy] : null;

        if (!span) {
            Plotly.restyle(els.plot, { x: [[]], y: [[]], z: [[]] }, [p.dynamic.phase]);
            return;
        }

        const hx = [], hy = [], hz = [];
        for (let i = span.i0; i <= span.i1; i++) {
            hx.push(p.pos[i][0]);
            hy.push(p.pos[i][1]);
            hz.push(p.pos[i][2]);
        }

        Plotly.restyle(els.plot, {
            x: [hx], y: [hy], z: [hz], 'line.color': span.color
        }, [p.dynamic.phase]);
    }

    function updateReadout(nr) {
        const p = scene.payload;
        const meta = scene.meta;

        if (els.clock) {
            els.clock.textContent =
                p.t[nr].toFixed(2) + ' / ' + meta.duration.toFixed(2) + ' s';
        }

        if (els.phase) {
            const nrFazy = p.phase[nr];
            const span = nrFazy >= 0 ? p.spans[nrFazy] : null;
            els.phase.textContent = span ? span.label : '';
            els.phase.style.background = span ? span.color : 'transparent';
            els.phase.hidden = !span;
        }

        if (els.seek && document.activeElement !== els.seek) {
            els.seek.value = String(meta.frames > 1 ? nr / (meta.frames - 1) : 0);
        }
    }

    // ============================================================
    //  ODTWARZACZ
    //
    //  currentTime jest w SEKUNDACH NAGRANIA, a numer klatki wychodzi
    //  z niego przez wyszukiwanie po payload.t — patrz frameForTime.
    // ============================================================

    /** Ostatnia klatka, której czas nie wyprzedza podanej sekundy.
     *
     *  Klatki NIE leżą w równym rastrze — każda z nich to prawdziwa
     *  próbka z CSV ze swoim zmierzonym czasem, a czujnik próbkuje
     *  nierówno. Dlatego numeru nie da się policzyć mnożeniem przez fps
     *  i idzie tu wyszukiwanie binarne. Przy trzech tysiącach klatek to
     *  dwanaście porównań — mniej, niż kosztuje samo odczytanie zegara. */
    function frameForTime(sekundy) {
        if (!scene) return 0;
        const t = scene.payload.t;
        let lo = 0;
        let hi = t.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (t[mid] <= sekundy) lo = mid; else hi = mid - 1;
        }
        return lo;
    }

    function seekTo(sekundy) {
        if (!scene) return;
        currentTime = Math.max(0, Math.min(sekundy, scene.meta.duration));
        drawFrame(frameForTime(currentTime));
    }

    function speed() {
        const v = els.speed ? parseFloat(els.speed.value) : 1;
        return (isFinite(v) && v > 0) ? v : 1;
    }

    function tick(now) {
        if (!playing) return;

        const dlugosc = scene.meta.duration;
        let czas = timeAtStart + (now - clockStart) / 1000 * speed();

        if (czas >= dlugosc) {
            if (!looping) {
                // Koniec zakresu: ostatnia klatka i stop.
                seekTo(dlugosc);
                pause();
                return;
            }
            // Zawijamy ZEGAR, a nie tylko pozycję — inaczej po każdym
            // przebiegu animacja gubiłaby resztę z dzielenia i z czasem
            // rozjeżdżała się z nagraniem.
            timeAtStart = 0;
            clockStart = now;
            czas = 0;
        }

        seekTo(czas);
        raf = requestAnimationFrame(tick);
    }

    function play() {
        if (!scene || playing) return;

        // Odtwarzanie od samego końca zaczyna od początku — inaczej
        // przycisk „Odtwórz” po dobiegnięciu do końca nic nie robi.
        if (currentTime >= scene.meta.duration - 1e-6) currentTime = 0;

        playing = true;
        timeAtStart = currentTime;
        clockStart = performance.now();

        syncPlayButton();
        raf = requestAnimationFrame(tick);
    }

    function pause() {
        if (raf !== null) cancelAnimationFrame(raf);
        raf = null;
        playing = false;
        syncPlayButton();
    }

    function syncPlayButton() {
        if (!els.play) return;
        els.play.classList.toggle('is-playing', playing);
        const ikona = els.play.querySelector('[aria-hidden]');
        if (ikona) ikona.textContent = playing ? '⏸' : '▶';
        if (els.playLabel) els.playLabel.textContent = playing ? 'Pauza' : 'Odtwórz';
    }

    // ============================================================
    //  ZDARZENIA
    // ============================================================

    function syncLoopButton() {
        if (!els.loop) return;
        els.loop.classList.toggle('is-active', looping);
        els.loop.setAttribute('aria-pressed', String(looping));
    }

    if (els.play) {
        els.play.addEventListener('click', () => (playing ? pause() : play()));
    }

    if (els.loop) {
        syncLoopButton();
        els.loop.addEventListener('click', function () {
            looping = !looping;
            syncLoopButton();
        });
    }

    if (els.seek) {
        els.seek.addEventListener('input', function () {
            if (!scene) return;
            pause();
            seekTo(parseFloat(els.seek.value) * scene.meta.duration);
        });
    }

    if (els.speed) {
        // Zmiana tempa w trakcie nie może przeskoczyć animacji: zegar
        // startuje od nowa, ale od BIEŻĄCEJ pozycji w nagraniu.
        els.speed.addEventListener('change', function () {
            if (!playing) return;
            timeAtStart = currentTime;
            clockStart = performance.now();
        });
    }

    if (els.source) {
        els.source.addEventListener('change', function () {
            pause();
            load(true);
        });
    }

    // Skoro widok nie resetuje się już sam, musi być czym go cofnąć —
    // inaczej z mocno przybliżonej sceny nie ma jak wrócić.
    if (els.resetView) {
        els.resetView.addEventListener('click', resetCamera);
    }

    // Lista uderzeń w rozwijanym wyborze. Bierze się z segments.js, więc
    // nie ma drugiego żądania o te same dane.
    if (window.a8Segments && els.source) {
        window.a8Segments.onChange(function (stan) {
            const wybrane = els.source.value;
            const segmenty = stan.segments;

            els.source.disabled = segmenty.length === 0;

            if (!segmenty.length) {
                els.source.innerHTML = '<option value="">Brak zaznaczonych uderzeń</option>';
                if (activeView === '3d') setState(BRAK_SEGMENTOW, false);
                lastKey = null;
                return;
            }

            els.source.innerHTML = segmenty.map(seg =>
                '<option value="' + seg.id + '">Segment ' + escapeHtml(seg.name) +
                (seg.phases.length ? ' (' + seg.phases.length + ' faz)' : '') +
                '</option>'
            ).join('');

            // Utrzymujemy wybór użytkownika. Gdy jego segment zniknął albo
            // nic jeszcze nie było wybrane, bierzemy ten podświetlony na
            // wykresie, a w ostateczności pierwszy z listy.
            const nadalIstnieje = segmenty.some(s => String(s.id) === wybrane);
            els.source.value = nadalIstnieje
                ? wybrane
                : String(stan.activeId !== null && segmenty.some(s => s.id === stan.activeId)
                    ? stan.activeId
                    : segmenty[0].id);

            if (activeView === '3d' && els.source.value !== wybrane) load(true);
        });
    }

    // Spacja steruje odtwarzaniem, ale tylko gdy widać scenę i gdy nie
    // trwa pisanie w polu formularza.
    document.addEventListener('keydown', function (e) {
        if (e.code !== 'Space' || activeView !== '3d' || !scene) return;
        // BUTTON też jest wykluczony: spacja na ustawionym focusie i tak
        // wywołuje kliknięcie, więc obsłużenie jej tutaj przełączałoby
        // odtwarzanie dwa razy i wracało do stanu wyjściowego.
        const cel = e.target;
        if (cel && (cel.tagName === 'INPUT' || cel.tagName === 'SELECT' ||
                    cel.tagName === 'BUTTON' || cel.tagName === 'TEXTAREA' ||
                    cel.isContentEditable)) return;
        e.preventDefault();
        playing ? pause() : play();
    });

    // Wyjście z karty zatrzymuje animację — inaczej wraca się do niej
    // w losowym miejscu, bo requestAnimationFrame w tle nie chodzi,
    // a zegar ścienny owszem.
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) pause();
    });

    window.addEventListener('resize', function () {
        if (activeView === '3d') resizePlot(els.plot);
    });
});

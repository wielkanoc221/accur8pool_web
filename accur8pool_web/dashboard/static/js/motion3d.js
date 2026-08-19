/* ============================================================
   ACCUR8POOL — animacja ruchu 3D

   Ładuj PO common.js, PO dashboard.js (window.a8Chart) i PO segments.js
   (window.a8Segments), a PRZED fullscreen.js.

   ODTWARZANIE JEST STEROWANE ZEGAREM, NIE KLATKAMI
   ------------------------------------------------
   To jest sedno całego pliku. Każda klatka to prawdziwa próbka z CSV ze
   swoim ZMIERZONYM czasem — a czujnik próbkuje nierówno, więc klatki nie
   leżą w równym rastrze. Pętla odtwarzania pyta performance.now() o to,
   ile sekund minęło NAPRAWDĘ, i dopiero z tego wyszukuje numer klatki
   (MotionScene.frameForTime). Skutek: sekunda animacji to sekunda z pliku
   CSV, niezależnie od tego, czy przeglądarka rysuje 120 klatek na sekundę,
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

   KLASY W TYM PLIKU
   -----------------
     MotionScene       odpowiedź serwera + wyszukiwanie klatki po czasie
     CameraController  widok sceny: zapamiętanie, obrót, reset
     SceneRenderer     rysowanie pojedynczej klatki
     Player            zegar odtwarzania
     SceneLoader       pobieranie i pamięć podręczna scen
     Motion3DTab       zakładka: elementy, przyciski, klawiatura
   ============================================================ */

(function () {
    'use strict';

    const TRAIL_SECONDS = 0.4;   // jak długi ogon ciągnie się za nadgarstkiem

    const NO_SEGMENTS_MESSAGE =
        'Zaznacz uderzenie na wykresie 2D — animacja liczy się dla ' +
        'pojedynczego segmentu, nie dla całego nagrania.';

    const ROT_STEP = 15;                  // stopni na jedno kliknięcie
    const DEG = Math.PI / 180;

    // Strzałki wokół osi: ↑↓ = oś X, ←→ = oś Y, z Shiftem ←→ = oś Z.
    const ROTATION_KEYS = {
        ArrowUp:    ['x', -ROT_STEP],
        ArrowDown:  ['x',  ROT_STEP],
        ArrowLeft:  ['y', -ROT_STEP],
        ArrowRight: ['y',  ROT_STEP]
    };

    // ============================================================
    //  MATEMATYKA
    // ============================================================

    /** Obrót wektora kwaternionem q = [w, x, y, z]. Wynik ląduje w `out`,
     *  żeby nie zaśmiecać pamięci tablicą na każdy wierzchołek każdej
     *  klatki. */
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

    /** Obrót punktu wokół osi UKŁADU SCENY (nie osi ekranu). */
    function spin(axis, point, angle) {
        const c = Math.cos(angle), s = Math.sin(angle);
        if (axis === 'x') {
            return { x: point.x, y: point.y * c - point.z * s, z: point.y * s + point.z * c };
        }
        if (axis === 'y') {
            return { x: point.x * c + point.z * s, y: point.y, z: -point.x * s + point.z * c };
        }
        return { x: point.x * c - point.y * s, y: point.x * s + point.y * c, z: point.z };
    }

    // ============================================================
    //  SCENA JAKO DANE
    // ============================================================

    /** Odpowiedź serwera z wygodnym dostępem do klatek. */
    class MotionScene {
        constructor(body) {
            this.figure = body.figure;
            this.payload = body.payload;
            this.meta = body.meta;
        }

        get duration() {
            return this.meta.duration;
        }

        get frameCount() {
            return this.meta.frames;
        }

        /** Ostatnia klatka, której czas nie wyprzedza podanej sekundy.
         *
         *  Klatki NIE leżą w równym rastrze — każda z nich to prawdziwa
         *  próbka z CSV ze swoim zmierzonym czasem, a czujnik próbkuje
         *  nierówno. Dlatego numeru nie da się policzyć mnożeniem przez fps
         *  i idzie tu wyszukiwanie binarne. Przy trzech tysiącach klatek to
         *  dwanaście porównań — mniej, niż kosztuje samo odczytanie zegara. */
        frameForTime(seconds) {
            const times = this.payload.t;
            let lo = 0;
            let hi = times.length - 1;
            while (lo < hi) {
                const mid = (lo + hi + 1) >> 1;
                if (times[mid] <= seconds) lo = mid; else hi = mid - 1;
            }
            return lo;
        }

        timeAt(frame) {
            return this.payload.t[frame];
        }

        /** Faza trwająca w danej klatce albo null. */
        spanAt(frame) {
            const number = this.payload.phase[frame];
            return number >= 0 ? this.payload.spans[number] : null;
        }
    }

    // ============================================================
    //  KAMERA
    //
    //  Ustawienie kamery jest STANEM UŻYTKOWNIKA, nie częścią danych.
    //  Serwer przysyła ją w każdej scenie, bo musi mieć jakieś ustawienie
    //  startowe, ale gdy widz raz obrócił albo przybliżył scenę, jego wybór
    //  wygrywa ze wszystkim, co przyjdzie później: z podmianą segmentu,
    //  z przeliczeniem po zmianie faz i z powrotem na zakładkę 3D.
    //  Porównywanie dwóch uderzeń polega na oglądaniu ich POD TYM SAMYM
    //  KĄTEM — animacja, która przy każdym przełączeniu wraca do widoku
    //  domyślnego, robi z tego zgadywankę.
    //
    //  Samo `uirevision` w layoucie tu nie wystarcza: trzyma widok przy
    //  Plotly.react, ale nie przeżywa przeładowania strony ani sytuacji,
    //  w której wykres trzeba postawić od nowa (Plotly.newPlot). Dlatego
    //  kamerę pamiętamy tutaj i wstawiamy do layoutu sami.
    //
    //  ODTWARZANIE NIE MOŻE COFAĆ WIDOKU
    //  ---------------------------------
    //  Każda klatka animacji to Plotly.restyle, a restyle stawia scenę
    //  WebGL od nowa i ustawia w niej kamerę z `layout.scene.camera`.
    //  Plotly wpisuje tam wybór widza dopiero po ZAKOŃCZENIU gestu — po
    //  puszczeniu przycisku myszy albo po ustaniu przewijania. Przez cały
    //  czas trwania gestu w layoucie siedzi więc widok SPRZED obrotu,
    //  a odtwarzanie przywraca go sześćdziesiąt razy na sekundę: scena
    //  szarpie się z powrotem i ani obrócić, ani przybliżyć jej podczas
    //  animacji się nie da. Dlatego przed każdą klatką bierzemy kamerę
    //  prosto ze sceny WebGL i sami wpisujemy ją do layoutu — patrz pin().
    // ============================================================

    class CameraController {
        constructor(plotEl) {
            this.plot = plotEl;
            this.current = CameraController._readStored();
            this.fallback = null;    // widok domyślny z pierwszej sceny — do resetu
            this._hooked = false;
            this._applying = false;
            this._savedAt = 0;
            this._plotted = () => false;
        }

        /** Kamera działa dopiero na postawionym wykresie — tab mówi, kiedy. */
        whenPlotted(predicate) {
            this._plotted = predicate;
        }

        // ---- pamięć ----

        static get STORAGE_KEY() {
            return 'a8.motion3d.camera';
        }

        static _readStored() {
            try {
                const raw = sessionStorage.getItem(CameraController.STORAGE_KEY);
                return raw ? JSON.parse(raw) : null;
            } catch (err) {
                return null;   // tryb prywatny albo zablokowany magazyn
            }
        }

        save(camera) {
            this.current = camera;
            this._savedAt = Date.now();
            try {
                sessionStorage.setItem(CameraController.STORAGE_KEY, JSON.stringify(camera));
            } catch (err) {
                /* Zapis może się nie udać i to nie jest powód, żeby przestać
                   pamiętać widok w tej karcie — `current` działa dalej. */
            }
        }

        // ---- odczyt ----

        /** Kamera prosto ze sceny WebGL — jedyne źródło, które zna widok
         *  W TRAKCIE obracania i przybliżania. `plot.layout` dowiaduje się
         *  o nim dopiero po zakończeniu gestu. */
        live() {
            const full = this.plot._fullLayout;
            const gl = full && full.scene && full.scene._scene;
            if (!gl || typeof gl.getCamera !== 'function') return null;
            try {
                return gl.getCamera();
            } catch (err) {
                return null;   // wewnętrzne API Plotly — nie zakładamy, że jest
            }
        }

        /** Layout sceny z kamerą użytkownika w miejscu domyślnej. */
        layoutWith(layout) {
            const scene = Object.assign({}, layout.scene);
            if (!this.fallback && scene.camera) this.fallback = scene.camera;
            if (this.current) scene.camera = this.current;
            return Object.assign({}, layout, { scene: scene });
        }

        // ---- zapis widoku w trakcie animacji ----

        /** Przybija bieżący widok do layoutu wykresu PRZED odrysowaniem klatki.
         *
         *  To jest miejsce, które sprawia, że animacja nie kasuje obrotu ani
         *  przybliżenia — powód opisany w nagłówku sekcji. Wywoływane raz na
         *  klatkę, więc wszystko tutaj musi być tanie: jedno odczytanie
         *  macierzy kamery i dwa przypisania. Do sessionStorage schodzimy
         *  najwyżej dwa razy na sekundę, bo serializacja przy każdej klatce
         *  byłaby czystą stratą. */
        pin() {
            // Gdyby wewnętrzne API Plotly kiedyś zniknęło, zostaje ostatni
            // widok zapamiętany na zdarzeniu relayout — mniej dokładny (nie
            // zna gestu w trakcie), ale wciąż lepszy niż widok domyślny.
            const fromScene = this.live();
            const camera = fromScene || this.current;
            if (!camera) return;

            if (this.plot.layout && this.plot.layout.scene) {
                this.plot.layout.scene.camera = camera;
            }
            if (this.plot._fullLayout && this.plot._fullLayout.scene) {
                this.plot._fullLayout.scene.camera = camera;
            }

            if (!fromScene) return;
            const changed = !CameraController._same(camera, this.current);
            this.current = camera;
            if (changed && Date.now() - this._savedAt > 500) this.save(camera);
        }

        static _same(a, b) {
            if (!a || !b) return false;
            const parts = ['eye', 'up', 'center'];
            for (let i = 0; i < parts.length; i++) {
                const p = a[parts[i]], q = b[parts[i]];
                if (!p !== !q) return false;
                if (p && q && (p.x !== q.x || p.y !== q.y || p.z !== q.z)) return false;
            }
            return true;
        }

        /** Zapamiętuje kamerę po każdym obrocie i przybliżeniu sceny. */
        hook() {
            if (this._hooked || !this.plot.on) return;
            this._hooked = true;

            this.plot.on('plotly_relayout', event => {
                if (this._applying) return;
                // Plotly melduje kamerę raz jako klucz ze ścieżką, raz jako
                // zagnieżdżony obiekt — zależnie od tego, co ją zmieniło.
                // Gdy nie ma jej w zdarzeniu (np. samo przeskalowanie
                // kontenera), bierzemy bieżącą z layoutu wykresu: jest wtedy
                // ta sama, więc zapis niczego nie psuje.
                const camera =
                    (event && (event['scene.camera'] ||
                               (event.scene && event.scene.camera))) ||
                    (this.plot.layout && this.plot.layout.scene &&
                     this.plot.layout.scene.camera);
                if (camera) this.save(camera);
            });
        }

        // ---- zmiana widoku ----

        /** Wstawia kamerę do sceny.
         *
         *  Idzie przez relayout, a nie przez podanie kamery w react: przy
         *  niezmienionym `uirevision` Plotly celowo IGNORUJE kamerę z nowego
         *  layoutu, żeby nie kasować tego, co ustawił widz. Relayout jest
         *  jawną zmianą i wygrywa — o to nam tutaj chodzi. */
        apply(camera) {
            if (!this._plotted() || !camera) return;
            this._applying = true;
            Promise.resolve(Plotly.relayout(this.plot, { 'scene.camera': camera }))
                .catch(err => console.error('Plotly 3D:', err))
                .then(() => { this._applying = false; });
        }

        reset() {
            this.current = null;
            this._savedAt = 0;
            try {
                sessionStorage.removeItem(CameraController.STORAGE_KEY);
            } catch (err) { /* jw. */ }
            this.apply(this.fallback);
        }

        /** Obrót sceny wokół JEDNEJ osi.
         *
         *  Myszą scena obraca się swobodnie (`dragmode: orbit`), ale „obróć
         *  o kawałek wokół osi Y” myszą się nie da — wychodzi zawsze obrót
         *  wokół dwóch osi naraz. Przyciski i strzałki robią dokładnie jeden
         *  obrót wokół dokładnie jednej osi, więc dwa uderzenia da się
         *  ustawić w tym samym ujęciu. */
        rotate(axis, degrees) {
            if (!this._plotted()) return;

            // Podstawą jest widok Z EKRANU, a nie ostatnio zapisany: w trakcie
            // odtwarzania i zaraz po obrocie myszą to nie zawsze to samo.
            const camera = this.live() || this.current || this.fallback;
            if (!camera || !camera.eye) return;

            const center = camera.center || { x: 0, y: 0, z: 0 };
            const up = camera.up || { x: 0, y: 0, z: 1 };
            const angle = degrees * DEG;

            // Obracamy WEKTOR OD ŚRODKA SCENY DO OKA, a nie samo oko —
            // inaczej po przesunięciu sceny (pan) obrót wyrzuciłby ją poza kadr.
            const eye = spin(axis, {
                x: camera.eye.x - center.x,
                y: camera.eye.y - center.y,
                z: camera.eye.z - center.z
            }, angle);

            const rotated = Object.assign({}, camera, {
                center: center,
                eye: { x: center.x + eye.x, y: center.y + eye.y, z: center.z + eye.z },
                // Pion obraca się razem z okiem. Bez tego po kilku krokach
                // scena zaczyna się przewracać, bo kamera patrzy z góry,
                // a „górę” ma nadal tam, gdzie była na starcie.
                up: spin(axis, up, angle)
            });

            this.save(rotated);
            this.apply(rotated);
        }
    }

    // ============================================================
    //  RYSOWANIE KLATKI
    // ============================================================

    class SceneRenderer {
        constructor(plotEl, camera) {
            this.plot = plotEl;
            this.camera = camera;
            this.plotted = false;      // czy Plotly.newPlot już się wykonał

            this._scene = null;
            this._lastFrame = -1;      // ostatnia NARYSOWANA klatka
            this._lastPhase = null;    // numer podświetlonej fazy
            this._scratch = [0, 0, 0];
        }

        /** Stawia nową scenę. Zwraca obietnicę spełnioną, gdy da się rysować. */
        show(scene) {
            this._scene = scene;
            this._lastFrame = -1;      // nowa scena — poprzedni numer nic nie znaczy
            this._lastPhase = null;

            // Kamera z layoutu serwera jest tylko ustawieniem startowym —
            // gdy widz coś już wybrał, wchodzi jego widok.
            const layout = this.camera.layoutWith(scene.figure.layout);

            const drawing = this.plotted
                ? Plotly.react(this.plot, scene.figure.data, layout)
                : Plotly.newPlot(this.plot, scene.figure.data, layout, {
                      responsive: true,
                      displayModeBar: false,
                      // Kółko myszy przybliża scenę. Domyślnie Plotly włącza
                      // to dla 3D samo, ale pasek narzędzi jest schowany, więc
                      // zostaje jedyną drogą do przybliżenia — nie zostawiamy
                      // tego domyślnym ustawieniom.
                      scrollZoom: true
                  });

            return drawing.then(() => {
                this.plotted = true;
                this.camera.hook();
                // Trójkąty bryły zegarka nie zmieniają się przez całą
                // animację, więc idą raz — co klatkę lecą już same
                // współrzędne wierzchołków.
                const payload = scene.payload;
                return Plotly.restyle(this.plot, {
                    i: [payload.faces.i],
                    j: [payload.faces.j],
                    k: [payload.faces.k]
                }, [payload.dynamic.watch]);
            });
        }

        forget() {
            this._scene = null;
            this._lastFrame = -1;
            this._lastPhase = null;
        }

        /** Rysuje klatkę o podanym numerze. Zwraca false, gdy nic nie było do
         *  zrobienia. Cała praca na klatkę jest tutaj. */
        drawFrame(frame) {
            const scene = this._scene;
            if (!scene || frame === this._lastFrame) return false;
            this._lastFrame = frame;

            const payload = scene.payload;
            const position = payload.pos[frame];
            const quaternion = payload.quat[frame];

            const watch = this._watchVertices(payload, position, quaternion);
            const axes = this._deviceAxes(payload, position, quaternion);
            const trail = this._trail(scene, frame);

            // Widok widza wchodzi do layoutu ZANIM restyle postawi scenę od
            // nowa — inaczej ta sama przebudowa cofnęłaby obrót i przybliżenie
            // do stanu sprzed gestu. Szczegóły w sekcji KAMERA.
            this.camera.pin();

            // Jedno wywołanie na całą klatkę. Plotly przy każdym restyle
            // przebudowuje scenę WebGL, więc pięć osobnych wywołań to pięć
            // przebudów zamiast jednej — i to widać jako szarpanie.
            const dynamic = payload.dynamic;
            Plotly.restyle(this.plot, {
                x: [trail.x, watch.x, axes[0].x, axes[1].x, axes[2].x],
                y: [trail.y, watch.y, axes[0].y, axes[1].y, axes[2].y],
                z: [trail.z, watch.z, axes[0].z, axes[1].z, axes[2].z]
            }, [dynamic.trail, dynamic.watch,
                dynamic.axes[0], dynamic.axes[1], dynamic.axes[2]]);

            this._highlightPhase(scene, payload.phase[frame]);
            return true;
        }

        _watchVertices(payload, position, quaternion) {
            const count = payload.verts.length;
            const x = new Array(count), y = new Array(count), z = new Array(count);

            for (let i = 0; i < count; i++) {
                const vertex = payload.verts[i];
                rotate(quaternion, vertex[0], vertex[1], vertex[2], this._scratch);
                x[i] = position[0] + this._scratch[0];
                y[i] = position[1] + this._scratch[1];
                z[i] = position[2] + this._scratch[2];
            }
            return { x: x, y: y, z: z };
        }

        _deviceAxes(payload, position, quaternion) {
            const length = payload.axis_len;
            const units = [[length, 0, 0], [0, length, 0], [0, 0, length]];

            return units.map(unit => {
                rotate(quaternion, unit[0], unit[1], unit[2], this._scratch);
                return {
                    x: [position[0], position[0] + this._scratch[0]],
                    y: [position[1], position[1] + this._scratch[1]],
                    z: [position[2], position[2] + this._scratch[2]]
                };
            });
        }

        /** Ogon: ostatnie TRAIL_SECONDS toru.
         *
         *  Liczone po CZASIE, nie po liczbie klatek — odstępy między
         *  próbkami nie są równe, więc stała liczba klatek dawałaby ogon raz
         *  dłuższy, raz krótszy. */
        _trail(scene, frame) {
            const from = scene.frameForTime(scene.timeAt(frame) - TRAIL_SECONDS);
            const positions = scene.payload.pos;
            const x = [], y = [], z = [];

            for (let i = from; i <= frame; i++) {
                x.push(positions[i][0]);
                y.push(positions[i][1]);
                z.push(positions[i][2]);
            }
            return { x: x, y: y, z: z };
        }

        /** Podświetla odcinek toru należący do trwającej fazy.
         *
         *  Cały tor jest szary — kolor dostaje tylko ta faza, która właśnie
         *  się odbywa. Odrysowanie idzie WYŁĄCZNIE przy zmianie fazy, czyli
         *  kilka razy na całe uderzenie, a nie sześćdziesiąt razy na sekundę:
         *  podmiana koloru linii jest w Plotly znacznie droższa niż podmiana
         *  samych współrzędnych. */
        _highlightPhase(scene, phaseNumber) {
            if (phaseNumber === this._lastPhase) return;
            this._lastPhase = phaseNumber;

            const payload = scene.payload;
            const span = phaseNumber >= 0 ? payload.spans[phaseNumber] : null;

            if (!span) {
                Plotly.restyle(this.plot, { x: [[]], y: [[]], z: [[]] },
                               [payload.dynamic.phase]);
                return;
            }

            const x = [], y = [], z = [];
            for (let i = span.i0; i <= span.i1; i++) {
                x.push(payload.pos[i][0]);
                y.push(payload.pos[i][1]);
                z.push(payload.pos[i][2]);
            }

            Plotly.restyle(this.plot, {
                x: [x], y: [y], z: [z], 'line.color': span.color
            }, [payload.dynamic.phase]);
        }
    }

    // ============================================================
    //  ODTWARZACZ
    //
    //  currentTime jest w SEKUNDACH NAGRANIA, a numer klatki wychodzi z niego
    //  przez wyszukiwanie po payload.t — patrz MotionScene.frameForTime.
    // ============================================================

    class Player {
        constructor(renderer, options) {
            this.renderer = renderer;
            this.scene = null;

            this.playing = false;
            this.currentTime = 0;    // pozycja w nagraniu [s]

            // Uderzenie trwa ułamek sekundy — obejrzane raz i w tempie 1×
            // jest mrugnięciem. Zapętlenie jest więc stanem domyślnym: ruch
            // powtarza się tak długo, jak długo się na niego patrzy, i dopiero
            // z kilku przebiegów widać, co ręka właściwie zrobiła.
            this.looping = true;

            this._clockStart = 0;    // performance.now() w chwili startu
            this._timeAtStart = 0;   // pozycja w nagraniu w chwili startu [s]
            this._raf = null;

            this._speed = options.speed || (() => 1);
            this._onFrame = options.onFrame || function () {};
            this._onStateChange = options.onStateChange || function () {};
        }

        load(scene) {
            this.pause();
            this.scene = scene;
            this.currentTime = 0;
        }

        clear() {
            this.pause();
            this.scene = null;
            this.currentTime = 0;
        }

        seekTo(seconds) {
            if (!this.scene) return;
            this.currentTime = Math.max(0, Math.min(seconds, this.scene.duration));
            const frame = this.scene.frameForTime(this.currentTime);
            if (this.renderer.drawFrame(frame)) this._onFrame(frame);
        }

        play() {
            if (!this.scene || this.playing) return;

            // Odtwarzanie od samego końca zaczyna od początku — inaczej
            // przycisk „Odtwórz” po dobiegnięciu do końca nic nie robi.
            if (this.currentTime >= this.scene.duration - 1e-6) this.currentTime = 0;

            this.playing = true;
            this._restartClock();
            this._onStateChange();
            this._raf = requestAnimationFrame(now => this._tick(now));
        }

        pause() {
            if (this._raf !== null) cancelAnimationFrame(this._raf);
            this._raf = null;
            this.playing = false;
            this._onStateChange();
        }

        toggle() {
            this.playing ? this.pause() : this.play();
        }

        /** Zmiana tempa w trakcie nie może przeskoczyć animacji: zegar
         *  startuje od nowa, ale od BIEŻĄCEJ pozycji w nagraniu. */
        resyncClock() {
            if (this.playing) this._restartClock();
        }

        _restartClock() {
            this._timeAtStart = this.currentTime;
            this._clockStart = performance.now();
        }

        _tick(now) {
            if (!this.playing) return;

            const duration = this.scene.duration;
            let time = this._timeAtStart + (now - this._clockStart) / 1000 * this._speed();

            if (time >= duration) {
                if (!this.looping) {
                    // Koniec zakresu: ostatnia klatka i stop.
                    this.seekTo(duration);
                    this.pause();
                    return;
                }
                // Zawijamy ZEGAR, a nie tylko pozycję — inaczej po każdym
                // przebiegu animacja gubiłaby resztę z dzielenia i z czasem
                // rozjeżdżała się z nagraniem.
                this._timeAtStart = 0;
                this._clockStart = now;
                time = 0;
            }

            this.seekTo(time);
            this._raf = requestAnimationFrame(next => this._tick(next));
        }
    }

    // ============================================================
    //  POBIERANIE SCEN
    // ============================================================

    /** Błąd, którego treść przyszła OD SERWERA i nadaje się do pokazania
     *  wprost (np. „zakres jest za krótki”). Wszystko inne — zerwane
     *  połączenie, uszkodzona odpowiedź — dostaje własny komunikat, bo
     *  „Failed to fetch” nikomu nic nie mówi. */
    class SceneError extends Error {}

    class SceneLoader {
        constructor(datasetId) {
            this.datasetId = datasetId;
            this._cache = new Map();
            this._loading = false;
            this.lastKey = null;      // klucz ostatniego udanego żądania
        }

        get busy() {
            return this._loading;
        }

        url(segmentId) {
            // Celowo BEZ parametru `fps`. Serwer oddaje wtedy każdą próbkę
            // z pliku jako osobną klatkę, a nie przepróbkowuje na okrągłe
            // 60 kl/s — animacja pokazuje to, co czujnik zmierzył, razem
            // z nierównym odstępem między pomiarami.
            const params = new URLSearchParams({ segment: String(segmentId) });
            return '/api/datasets/' + encodeURIComponent(this.datasetId) +
                   '/motion3d/?' + params;
        }

        cached(url) {
            return this._cache.has(url) ? new MotionScene(this._cache.get(url)) : null;
        }

        /** Pobiera scenę spod adresu. Rzuca Error z komunikatem od serwera. */
        async fetchScene(url) {
            this._loading = true;
            try {
                const response = await fetch(url, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                const body = await response.json().catch(() => ({}));

                if (!response.ok) {
                    throw new SceneError(
                        body.error || ('Serwer zwrócił błąd ' + response.status));
                }

                // Kilkanaście wpisów × kilkaset kB to już zauważalna pamięć,
                // a i tak wraca się zwykle do ostatnich kilku uderzeń.
                if (this._cache.size > 12) this._cache.clear();
                this._cache.set(url, body);

                return new MotionScene(body);
            } finally {
                this._loading = false;
            }
        }
    }

    // ============================================================
    //  ZAKŁADKI 2D / 3D
    // ============================================================

    /** Przełącznik widoków. Działa też wtedy, gdy animacji nie ma z czego
     *  zbudować (plik bez IMU) — wtedy jest jedyną rzeczą, jaka z tego pliku
     *  zostaje. */
    class ViewTabs {
        constructor(els, onChange) {
            this.els = els;
            this.active = '2d';
            this._onChange = onChange || function () {};

            els.tabs.forEach(tab => {
                tab.addEventListener('click', () => {
                    if (!tab.disabled) this.show(tab.dataset.view);
                });
            });
        }

        show(view) {
            if (view === this.active) return;
            this.active = view;

            this.els.tabs.forEach(tab => {
                const on = tab.dataset.view === view;
                tab.classList.toggle('is-active', on);
                tab.setAttribute('aria-selected', String(on));
            });

            ViewTabs._togglePanel(this.els.panel2d, view === '2d');
            ViewTabs._togglePanel(this.els.panel3d, view === '3d');

            // Plotly nie zna wymiarów kontenera, który był ukryty — bez tego
            // wykres po przełączeniu ma szerokość z momentu utworzenia.
            requestAnimationFrame(() => {
                resizePlot(document.getElementById('graph'));
                resizePlot(this.els.plot);
            });

            this._onChange(view);
        }

        static _togglePanel(panel, on) {
            if (!panel) return;
            panel.hidden = !on;
            panel.classList.toggle('is-active', on);
        }
    }

    function resizePlot(el) {
        if (window.Plotly && el && el.data) Plotly.Plots.resize(el);
    }

    // ============================================================
    //  ZAKŁADKA 3D
    // ============================================================

    class Motion3DTab {
        constructor(elements, datasetId) {
            this.els = elements;

            this.camera = new CameraController(elements.plot);
            this.renderer = new SceneRenderer(elements.plot, this.camera);
            this.camera.whenPlotted(() => this.renderer.plotted);

            this.loader = new SceneLoader(datasetId);
            this.player = new Player(this.renderer, {
                speed: () => this._speed(),
                onFrame: frame => this._updateReadout(frame),
                onStateChange: () => this._syncPlayButton()
            });

            // Wejście na zakładkę 3D liczy scenę, wyjście zatrzymuje animację.
            this.tabs = new ViewTabs(elements, view => {
                if (view === '3d') this.ensureLoaded(); else this.player.pause();
            });

            this._bindControls();
            this._bindKeyboard();
            this._bindSegmentList();
        }

        get showing3d() {
            return this.tabs.active === '3d';
        }

        // ------------------------------------------------------------
        //  WCZYTYWANIE SCENY
        // ------------------------------------------------------------

        ensureLoaded() {
            if (this.showing3d) this.load(false);
        }

        /** Wybrane uderzenie albo null. Zakres animacji bierze się z segmentu
         *  i tylko z niego: model dźwigni opisuje POJEDYNCZY ruch, więc
         *  puszczony na całym nagraniu pokazywałby ruch, którego nie było. */
        _selectedSegment() {
            const chosen = this.els.source ? this.els.source.value : '';
            if (!chosen) return null;

            const segment = window.a8Segments
                ? window.a8Segments.byId(Number(chosen)) : null;
            return segment || null;
        }

        async load(force) {
            const segment = this._selectedSegment();
            if (!segment) {
                this.showState(NO_SEGMENTS_MESSAGE, false);
                this.loader.lastKey = null;
                return;
            }

            const url = this.loader.url(segment.id);
            const name = 'Segment ' + segment.name;

            if (!force && url === this.loader.lastKey && this.player.scene) return;
            if (this.loader.busy) return;

            const cached = this.loader.cached(url);
            if (cached) {
                this.apply(cached, name);
                this.loader.lastKey = url;
                return;
            }

            this.showState('Liczenie ruchu 3D…', true);

            try {
                this.apply(await this.loader.fetchScene(url), name);
                this.loader.lastKey = url;
            } catch (err) {
                if (err instanceof SceneError) {
                    this.showState(err.message, false);
                } else {
                    console.error('Animacja 3D:', err);
                    this.showState('Nie udało się pobrać animacji 3D.', false);
                }
                this.loader.lastKey = null;
            }
        }

        apply(scene, name) {
            this.player.load(scene);
            this.hideState();

            this.renderer.show(scene)
                .then(() => {
                    this._setControlsEnabled(true);
                    this.player.seekTo(0);
                })
                .catch(err => console.error('Plotly 3D:', err));

            this._showDescription(scene, name);
        }

        // ------------------------------------------------------------
        //  KOMUNIKATY I ODCZYTY
        // ------------------------------------------------------------

        /** Komunikat zamiast sceny. Zawsze zdejmuje bieżącą animację —
         *  inaczej odtwarzacz chodziłby dalej po niewidocznym wykresie,
         *  a suwak pokazywał pozycję w nagraniu, którego już nie ma. */
        showState(text, busy) {
            this.player.clear();
            this.renderer.forget();

            if (this.els.stateText) this.els.stateText.textContent = text;
            if (this.els.state) {
                this.els.state.hidden = false;
                this.els.state.classList.toggle('is-busy', !!busy);
            }
            this.els.plot.hidden = true;
            this._setControlsEnabled(false);
        }

        hideState() {
            if (this.els.state) this.els.state.hidden = true;
            this.els.plot.hidden = false;
        }

        _setControlsEnabled(on) {
            if (this.els.play) this.els.play.disabled = !on;
            if (this.els.seek) this.els.seek.disabled = !on;
        }

        /** Podtytuł mówi TYLKO to, co widz musi wiedzieć: które uderzenie
         *  jest odtwarzane. Parametry rekonstrukcji (model dźwigni, bok
         *  sceny, zgodność wektora obrotu z żyroskopem) to diagnostyka —
         *  czytelna dla dwóch osób w projekcie, a dla reszty ściana tekstu
         *  w miejscu, gdzie ma być nazwa. Idzie więc do atrybutu title
         *  (dymek pod kursorem): zostaje dostępna, ale nie zajmuje ekranu. */
        _showDescription(scene, name) {
            const meta = scene.meta;

            if (this.els.label) {
                this.els.label.textContent = name;
                this.els.label.title = Motion3DTab._diagnostics(meta).join(' · ');
            }

            if (this.els.statTime) {
                this.els.statTime.textContent = meta.duration.toFixed(2) + ' s';
            }
            if (this.els.statPath) {
                this.els.statPath.textContent = meta.path_cm.toFixed(1) + ' cm';
            }
            if (this.els.statVmax) {
                this.els.statVmax.textContent = meta.v_max.toFixed(2) + ' m/s';
            }
        }

        static _diagnostics(meta) {
            const details = [meta.label];
            if (meta.span_cm) details.push('scena ' + meta.span_cm.toFixed(1) + ' cm');
            if (meta.stride > 1) details.push('co ' + meta.stride + '. próbka');
            if (meta.gaps) details.push(meta.gaps + ' × przerwa w nagraniu (skrócona)');
            if (meta.rot_vs_gyro !== null && meta.rot_vs_gyro > 0.35) {
                details.push('rozjazd z żyroskopem ' +
                             (meta.rot_vs_gyro * 100).toFixed(0) + '%');
            }
            return details;
        }

        _updateReadout(frame) {
            const scene = this.player.scene;
            if (!scene) return;

            if (this.els.clock) {
                this.els.clock.textContent = scene.timeAt(frame).toFixed(2) + ' / ' +
                                             scene.duration.toFixed(2) + ' s';
            }

            if (this.els.phase) {
                const span = scene.spanAt(frame);
                this.els.phase.textContent = span ? span.label : '';
                this.els.phase.style.background = span ? span.color : 'transparent';
                this.els.phase.hidden = !span;
            }

            if (this.els.seek && document.activeElement !== this.els.seek) {
                this.els.seek.value = String(
                    scene.frameCount > 1 ? frame / (scene.frameCount - 1) : 0);
            }
        }

        _speed() {
            const value = this.els.speed ? parseFloat(this.els.speed.value) : 1;
            return (isFinite(value) && value > 0) ? value : 1;
        }

        // ------------------------------------------------------------
        //  PRZYCISKI
        // ------------------------------------------------------------

        _syncPlayButton() {
            const play = this.els.play;
            if (!play) return;

            play.classList.toggle('is-playing', this.player.playing);
            // Ikona to <svg><use href="#i-…"> — podmieniamy cel odnośnika.
            // href i xlink:href razem, bo starsze Safari czyta tylko drugi.
            const use = play.querySelector('.icon use');
            if (use) {
                const id = this.player.playing ? '#i-pause' : '#i-play';
                use.setAttribute('href', id);
                use.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', id);
            }
            if (this.els.playLabel) {
                this.els.playLabel.textContent = this.player.playing ? 'Pauza' : 'Odtwórz';
            }
        }

        _syncLoopButton() {
            if (!this.els.loop) return;
            this.els.loop.classList.toggle('is-active', this.player.looping);
            this.els.loop.setAttribute('aria-pressed', String(this.player.looping));
        }

        _bindControls() {
            if (this.els.play) {
                this.els.play.addEventListener('click', () => this.player.toggle());
            }

            if (this.els.loop) {
                this._syncLoopButton();
                this.els.loop.addEventListener('click', () => {
                    this.player.looping = !this.player.looping;
                    this._syncLoopButton();
                });
            }

            if (this.els.seek) {
                this.els.seek.addEventListener('input', () => {
                    if (!this.player.scene) return;
                    this.player.pause();
                    this.player.seekTo(parseFloat(this.els.seek.value) *
                                       this.player.scene.duration);
                });
            }

            if (this.els.speed) {
                this.els.speed.addEventListener('change', () => this.player.resyncClock());
            }

            if (this.els.source) {
                this.els.source.addEventListener('change', () => {
                    this.player.pause();
                    this.load(true);
                });
            }

            // Skoro widok nie resetuje się już sam, musi być czym go cofnąć —
            // inaczej z mocno przybliżonej sceny nie ma jak wrócić.
            if (this.els.resetView) {
                this.els.resetView.addEventListener('click', () => this.camera.reset());
            }

            this.els.rotate.forEach(button => {
                button.addEventListener('click', () => {
                    this.camera.rotate(button.dataset.rotAxis,
                                       parseFloat(button.dataset.rotDeg));
                });
            });

            // Wyjście z karty zatrzymuje animację — inaczej wraca się do niej
            // w losowym miejscu, bo requestAnimationFrame w tle nie chodzi,
            // a zegar ścienny owszem.
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) this.player.pause();
            });

            window.addEventListener('resize', () => {
                if (this.showing3d) resizePlot(this.els.plot);
            });
        }

        /** Spacja steruje odtwarzaniem, strzałki obracają scenę — ale tylko
         *  gdy widać scenę i gdy nie trwa pisanie w polu formularza. */
        _bindKeyboard() {
            document.addEventListener('keydown', e => {
                if (!this.showing3d || !this.player.scene) return;
                if (Motion3DTab._isTyping(e.target)) return;

                if (e.code === 'Space') {
                    // Tu BUTTON jest wykluczony dodatkowo: spacja na
                    // ustawionym focusie i tak wywołuje kliknięcie, więc
                    // obsłużenie jej jeszcze raz przełączałoby odtwarzanie
                    // dwukrotnie i wracało do stanu wyjściowego. Strzałki
                    // takiego problemu nie mają i celowo działają także po
                    // kliknięciu w przycisk obrotu.
                    if (e.target && e.target.tagName === 'BUTTON') return;
                    e.preventDefault();
                    this.player.toggle();
                    return;
                }

                const rotation = ROTATION_KEYS[e.key];
                if (!rotation || e.ctrlKey || e.altKey || e.metaKey) return;
                e.preventDefault();
                // Shift + strzałka w bok kręci sceną wokół pionu — to jest
                // obrót „samych osi X i Y”, bez pochylania widoku.
                const aroundZ = e.shiftKey && rotation[0] === 'y';
                this.camera.rotate(aroundZ ? 'z' : rotation[0], rotation[1]);
            });
        }

        static _isTyping(target) {
            return Boolean(target && (target.tagName === 'INPUT' ||
                                      target.tagName === 'SELECT' ||
                                      target.tagName === 'TEXTAREA' ||
                                      target.isContentEditable));
        }

        // ------------------------------------------------------------
        //  LISTA UDERZEŃ
        //
        //  Bierze się z segments.js, więc nie ma drugiego żądania o te same
        //  dane.
        // ------------------------------------------------------------

        _bindSegmentList() {
            if (!window.a8Segments || !this.els.source) return;

            window.a8Segments.onChange(state => {
                const previous = this.els.source.value;
                const segments = state.segments;

                this.els.source.disabled = segments.length === 0;

                if (!segments.length) {
                    this.els.source.innerHTML =
                        '<option value="">Brak zaznaczonych uderzeń</option>';
                    if (this.showing3d) this.showState(NO_SEGMENTS_MESSAGE, false);
                    this.loader.lastKey = null;
                    return;
                }

                this.els.source.innerHTML = segments.map(segment =>
                    '<option value="' + segment.id + '">Segment ' +
                    escapeHtml(segment.name) +
                    (segment.phases.length ? ' (' + segment.phases.length + ' faz)' : '') +
                    '</option>'
                ).join('');

                this.els.source.value = Motion3DTab._keepSelection(state, previous);

                if (this.showing3d && this.els.source.value !== previous) {
                    this.load(true);
                }
            });
        }

        /** Utrzymujemy wybór użytkownika. Gdy jego segment zniknął albo nic
         *  jeszcze nie było wybrane, bierzemy ten podświetlony na wykresie,
         *  a w ostateczności pierwszy z listy. */
        static _keepSelection(state, previous) {
            const segments = state.segments;
            if (segments.some(segment => String(segment.id) === previous)) return previous;

            const active = state.activeId !== null &&
                           segments.some(segment => segment.id === state.activeId);
            return String(active ? state.activeId : segments[0].id);
        }
    }

    // ============================================================
    //  MONTAŻ
    // ============================================================

    function readJson(id, fallback) {
        const el = document.getElementById(id);
        if (!el) return fallback;
        try { return JSON.parse(el.textContent); } catch (err) { return fallback; }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const els = {
            tabs: Array.prototype.slice.call(
                document.querySelectorAll('.view-tab[data-view]')),
            panel2d: document.getElementById('view-2d'),
            panel3d: document.getElementById('view-3d'),

            plot: document.getElementById('graph3d'),
            state: document.getElementById('m3d-state'),
            stateText: document.getElementById('m3d-state-text'),
            label: document.getElementById('m3d-label'),

            source: document.getElementById('m3d-source'),
            resetView: document.getElementById('m3d-reset-view'),
            rotate: Array.prototype.slice.call(
                document.querySelectorAll('[data-rot-axis]')),
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

        const datasetId = window.currentDatasetId;
        const ready = readJson('motion3d-ready', false);

        // Bez sceny 3D (plik bez IMU, brak wybranego zestawu) zostają same
        // zakładki — przełączanie widoków ma działać tak samo.
        if (!els.plot || !datasetId || !ready) {
            new ViewTabs(els);
            return;
        }

        new Motion3DTab(els, datasetId);
    });
})();

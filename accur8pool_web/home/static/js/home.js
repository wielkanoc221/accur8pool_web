/* ============================================================
   ACCUR8POOL — strona główna

   Trzy niezależne kawałki, każdy wyłączający się sam, gdy nie ma na
   stronie swojego elementu:

     1. menu na wąskim ekranie,
     2. podgląd dashboardu — wykres SVG z PRAWDZIWEGO wycinka zestawu
        demonstracyjnego (dane wstawia szablon jako #demo-preview),
     3. podgląd pracy z plikami — symulacja uploadu, która pokazuje
        prawdziwe komunikaty aplikacji, ale niczego nie wysyła.

   Bez bibliotek. Dashboard rysuje wykresy Plotly ze skryptu z CDN;
   strona główna ma być pierwszym, co ktoś zobaczy, więc nie czeka na
   200 kB obcego kodu tylko po to, żeby narysować pięć linii.

   Wykres jest w jednostce „numer wiersza CSV” — dokładnie tej samej,
   w której trzymane są granice segmentów i faz (patrz Segment w modelu).
   Dzięki temu nic tu nie wymaga przeliczania.
   ============================================================ */

(function () {
    'use strict';

    // Kopia SubSegment.PHASE_COLORS. Kolory faz są w modelu po stronie
    // serwera, bo używa ich i wykres, i scena 3D — tutaj powtarzamy je
    // po raz trzeci, świadomie: strona główna nie odpytuje serwera.
    var PHASES = {
        przygotowanie: {label: 'Przygotowanie', color: '#f59e0b'},
        przymierzanie: {label: 'Przymierzanie', color: '#8b5cf6'},
        uderzenie:     {label: 'Uderzenie',     color: '#ef4444'},
        po_uderzeniu:  {label: 'Po uderzeniu',  color: '#10b981'}
    };

    // Układ współrzędnych SVG. Wysokość jest stała, a szerokość rozciąga
    // się na kontener (preserveAspectRatio="none"); linie trzymają swoją
    // grubość dzięki vector-effect, więc rozciągnięcie ich nie zniekształca.
    var VIEW_W = 1000;
    var VIEW_H = 300;
    var PAD_Y = 14;

    function el(selector, root) {
        return (root || document).querySelector(selector);
    }

    function all(selector, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(selector));
    }

    function esc(text) {
        return String(text).replace(/[&<>"]/g, function (ch) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[ch];
        });
    }

    function spaced(number) {
        // Odstęp nierozdzielający — 15 354 nie może się złamać na końcu wiersza.
        return String(number).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    }

    // ============================================================
    //  MENU NA WĄSKIM EKRANIE
    // ============================================================

    (function menu() {
        var header = el('#site-header');
        var burger = el('#nav-burger');
        if (!header || !burger) { return; }

        burger.addEventListener('click', function () {
            var open = header.classList.toggle('is-open');
            burger.setAttribute('aria-expanded', open ? 'true' : 'false');
        });

        // Kliknięcie pozycji menu ma je zamknąć — inaczej rozwinięta lista
        // zasłania początek sekcji, do której właśnie przeskoczyliśmy.
        all('.site-nav a, .header-actions a', header).forEach(function (link) {
            link.addEventListener('click', function () {
                header.classList.remove('is-open');
                burger.setAttribute('aria-expanded', 'false');
            });
        });
    }());

    // ============================================================
    //  ZAKŁADKI SEKCJI DEMO
    // ============================================================

    (function tabs() {
        var buttons = all('[data-demo-tab]');
        if (!buttons.length) { return; }

        function show(name) {
            buttons.forEach(function (button) {
                var active = button.getAttribute('data-demo-tab') === name;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-selected', active ? 'true' : 'false');

                var panel = document.getElementById('panel-' + button.getAttribute('data-demo-tab'));
                if (panel) {
                    panel.classList.toggle('is-active', active);
                    panel.hidden = !active;
                }
            });
        }

        buttons.forEach(function (button) {
            button.addEventListener('click', function () {
                show(button.getAttribute('data-demo-tab'));
            });
        });
    }());

    // ============================================================
    //  PODGLĄD DASHBOARDU
    // ============================================================

    (function dashboardPreview() {
        var payload = document.getElementById('demo-preview');
        if (!payload) { return; }

        var data;
        try {
            data = JSON.parse(payload.textContent);
        } catch (error) {
            return;  // Zostaje komunikat zastępczy wpisany w szablonie.
        }
        if (!data || !data.series || !data.series.length) { return; }
        data.segments = data.segments || [];

        var slot = el('[data-plot="demo"]');
        var heroSlot = el('[data-plot="hero"]');
        var list = el('[data-demo-list]');
        var legendBox = el('[data-demo-legend]');
        var hint = el('[data-demo-hint]');
        var sideTitle = el('[data-demo-side-title]');
        var sideSub = el('[data-demo-side-sub]');
        var resetButton = el('[data-demo-reset]');

        var lastRow = Math.max((data.rows || 1) - 1, 1);
        var hidden = {};      // nazwa serii → ukryta w legendzie
        var selected = null;  // indeks segmentu albo null = cały przebieg

        // ------------------------------------------------------------
        //  RYSOWANIE
        // ------------------------------------------------------------

        function scaleX(row, lo, hi) {
            return ((row - lo) / (hi - lo || 1)) * VIEW_W;
        }

        function scaleY(value) {
            return PAD_Y + (1 - value) * (VIEW_H - 2 * PAD_Y);
        }

        /** Punkty serii w widocznym zakresie, jako pary [wiersz, wartość].
         *
         *  Przy wybranym segmencie źródłem jest gęstsza próbka zapisana
         *  razem z nim: przebieg całego pliku jest zdecymowany do rozmiaru
         *  ekranu, więc na pojedyncze uderzenie zostałoby z niego
         *  kilkanaście punktów i kształt uderzenia by zniknął. Aplikacja
         *  robi w tym miejscu to samo, tyle że dopytuje serwer.
         */
        function pointsFor(series, segment) {
            if (!segment || !segment.detail) {
                return series.points || [];
            }

            var detail = segment.detail;
            var values = (detail.values || {})[series.name];
            if (!values || !values.length) { return []; }

            var span = detail.hi - detail.lo;
            var last = values.length - 1;
            return values.map(function (value, index) {
                return [detail.lo + (span * index) / last, value];
            });
        }

        function bandsFor(segment, lo, hi) {
            /* Prostokąty faz, a gdy segment ich nie ma — jeden prostokąt
               na całe uderzenie. Bez tego segment bez opisanych faz byłby
               na wykresie niewidoczny. */
            var visible = (selected === null) ? data.segments : [segment];
            var out = [];

            visible.forEach(function (item) {
                if (!item || item.end < lo || item.start > hi) { return; }

                if (!item.phases || !item.phases.length) {
                    out.push({start: item.start, end: item.end, color: '#94a3b8', alpha: 0.16});
                    return;
                }
                item.phases.forEach(function (phase) {
                    var known = PHASES[phase.phase];
                    out.push({
                        start: phase.start,
                        end: phase.end,
                        color: known ? known.color : '#94a3b8',
                        alpha: selected === null ? 0.2 : 0.16
                    });
                });
            });

            return out;
        }

        function draw(target, options) {
            if (!target) { return; }

            var segment = (selected === null) ? null : data.segments[selected];
            var lo = segment ? segment.detail.lo : 0;
            var hi = segment ? segment.detail.hi : lastRow;
            var parts = [];

            // Siatka poziomo — cztery linie wystarczą za oś Y, której na
            // podglądzie i tak nie opisujemy (serie są znormalizowane).
            [0.25, 0.5, 0.75].forEach(function (level) {
                parts.push('<line x1="0" y1="' + scaleY(level).toFixed(1) +
                           '" x2="' + VIEW_W + '" y2="' + scaleY(level).toFixed(1) +
                           '" stroke="#eef2f7" stroke-width="1" vector-effect="non-scaling-stroke"/>');
            });

            bandsFor(segment, lo, hi).forEach(function (band) {
                var x = scaleX(Math.max(band.start, lo), lo, hi);
                var width = scaleX(Math.min(band.end, hi), lo, hi) - x;
                if (width <= 0) { return; }
                parts.push('<rect x="' + x.toFixed(1) + '" y="0" width="' + width.toFixed(1) +
                           '" height="' + VIEW_H + '" fill="' + band.color +
                           '" fill-opacity="' + band.alpha + '"/>');
            });

            data.series.forEach(function (series) {
                if (!options.hero && hidden[series.name]) { return; }

                var coords = [];
                pointsFor(series, segment).forEach(function (point) {
                    if (point[1] === null || point[0] < lo || point[0] > hi) { return; }
                    coords.push(scaleX(point[0], lo, hi).toFixed(1) + ',' + scaleY(point[1]).toFixed(1));
                });
                if (coords.length < 2) { return; }

                parts.push('<polyline points="' + coords.join(' ') +
                           '" fill="none" stroke="' + series.color +
                           '" stroke-width="' + (options.hero ? 1.2 : 1.5) +
                           '" stroke-linejoin="round" stroke-linecap="round"' +
                           ' vector-effect="non-scaling-stroke"/>');
            });

            // Podmiana całej zawartości zabiera też komunikat zastępczy
            // wpisany w szablonie — i o to chodzi: jest potrzebny wyłącznie
            // wtedy, gdy do narysowania nigdy nie doszło.
            target.innerHTML =
                '<svg viewBox="0 0 ' + VIEW_W + ' ' + VIEW_H + '" preserveAspectRatio="none"' +
                ' role="img" aria-label="Przebieg pomiarów z zestawu demonstracyjnego">' +
                parts.join('') + '</svg>';
            target.classList.add('has-plot');
        }

        function drawAll() {
            // Miniatura w nagłówku pokazuje ZAWSZE cały przebieg — nie
            // reaguje na wybór segmentu niżej, bo stoi nad nim na stronie
            // i jej zmiana wyglądałaby na usterkę.
            var keep = selected;
            selected = null;
            draw(heroSlot, {hero: true});
            selected = keep;

            draw(slot, {hero: false});
        }

        // ------------------------------------------------------------
        //  LEGENDA
        // ------------------------------------------------------------

        function renderLegend() {
            if (!legendBox) { return; }

            legendBox.innerHTML = data.series.map(function (series) {
                return '<button type="button" class="legend-item' +
                       (hidden[series.name] ? ' is-off' : '') +
                       '" data-series="' + esc(series.name) + '"' +
                       ' style="color:' + series.color + '"' +
                       ' aria-pressed="' + (hidden[series.name] ? 'false' : 'true') + '">' +
                       '<span class="dot"></span>' +
                       '<span style="color:var(--text-secondary)">' + esc(series.label) + '</span>' +
                       '</button>';
            }).join('');

            all('[data-series]', legendBox).forEach(function (button) {
                button.addEventListener('click', function () {
                    var name = button.getAttribute('data-series');
                    var visibleCount = data.series.filter(function (series) {
                        return !hidden[series.name];
                    }).length;

                    // Ostatniej widocznej serii nie da się zgasić — pusty
                    // wykres wygląda jak awaria podglądu, a nie jak wybór.
                    if (!hidden[name] && visibleCount <= 1) { return; }

                    hidden[name] = !hidden[name];
                    renderLegend();
                    drawAll();
                });
            });
        }

        // ------------------------------------------------------------
        //  LISTA SEGMENTÓW
        // ------------------------------------------------------------

        function phaseBar(segment) {
            var span = segment.end - segment.start || 1;
            return '<span class="seg-bar">' + (segment.phases || []).map(function (phase) {
                var known = PHASES[phase.phase];
                var width = ((phase.end - phase.start) / span) * 100;
                return '<i style="width:' + width.toFixed(1) + '%;background:' +
                       (known ? known.color : '#94a3b8') + '"></i>';
            }).join('') + '</span>';
        }

        function renderList() {
            if (!list) { return; }

            list.innerHTML = data.segments.map(function (segment, index) {
                return '<button type="button" class="seg-item' +
                       (selected === index ? ' is-active' : '') + '" data-segment="' + index + '">' +
                       '<span class="seg-no">' + (index + 1) + '</span>' +
                       '<span class="seg-meta">' +
                       '<b>Uderzenie ' + (index + 1) + '</b>' +
                       '<small>wiersze ' + spaced(segment.start) + '–' + spaced(segment.end) +
                       ' · ' + spaced(segment.end - segment.start) + ' próbek</small>' +
                       phaseBar(segment) +
                       '</span></button>';
            }).join('');

            all('[data-segment]', list).forEach(function (button) {
                button.addEventListener('click', function () {
                    var index = Number(button.getAttribute('data-segment'));
                    selected = (selected === index) ? null : index;
                    update();
                });
            });
        }

        function update() {
            renderList();
            drawAll();

            var segment = (selected === null) ? null : data.segments[selected];

            if (hint) {
                hint.textContent = segment
                    ? 'Uderzenie ' + (selected + 1) + ' — wiersze ' + spaced(segment.start) +
                      '–' + spaced(segment.end) + ', cztery fazy zaznaczone kolorem'
                    : 'Kliknij uderzenie po prawej, żeby przybliżyć jego zakres';
            }
            if (sideTitle) {
                sideTitle.textContent = segment ? 'Uderzenie ' + (selected + 1) : 'Segmenty';
            }
            if (sideSub) {
                sideSub.textContent = segment
                    ? 'Kliknij ponownie, aby wrócić do całego przebiegu'
                    : 'Zaznaczone uderzenia: ' + data.segments.length;
            }
        }

        if (resetButton) {
            resetButton.addEventListener('click', function () {
                selected = null;
                update();
            });
        }

        renderLegend();
        update();
    }());

    // ============================================================
    //  PODGLĄD PRACY Z PLIKAMI
    //
    //  Symulacja, nie makieta: przechodzi przez te same stany, co
    //  prawdziwy upload (wysyłka → przygotowanie danych → wynik)
    //  i kończy się dosłownym komunikatem z serwera. Odmowa przyjęcia
    //  pliku jest tu równie ważna jak sukces — to ona tłumaczy, po co
    //  jest lista wymaganych kolumn wyżej.
    // ============================================================

    (function filesPreview() {
        var body = el('[data-demo-files]');
        var status = el('[data-demo-status]');
        if (!body) { return; }

        var files = [
            {
                name: 'demo.csv',
                records: 15354,
                added: 'przy zakładaniu konta',
                raw: false,
                prepared: true,
                note: 'zestaw demonstracyjny'
            },
            {
                name: 'trening_2024-05-12.csv',
                records: 48219,
                added: '12.05.2024',
                raw: true,
                prepared: true
            }
        ];

        var busy = false;
        var uploadCount = 0;

        function render() {
            body.innerHTML = files.map(function (file) {
                return '<tr' + (file.fresh ? ' class="is-new"' : '') + '>' +
                    '<th scope="row"><span class="file-name">' +
                        '<svg class="icon"><use href="#i-file"></use></svg>' +
                        esc(file.name) + '</span>' +
                        (file.note ? '<small style="color:var(--text-muted)"> · ' +
                                     esc(file.note) + '</small>' : '') +
                    '</th>' +
                    '<td>' + spaced(file.records) + '</td>' +
                    '<td>' + esc(file.added) + '</td>' +
                    '<td>' +
                        '<span class="mini-btn' + (file.raw ? '' : ' is-off') + '">surowy</span> ' +
                        '<span class="mini-btn' + (file.prepared ? '' : ' is-off') + '">przygotowany</span>' +
                    '</td>' +
                    '<td><span class="file-actions">' +
                        '<span class="mini-btn">otwórz</span>' +
                        '<span class="mini-btn">usuń</span>' +
                    '</span></td>' +
                '</tr>';
            }).join('');
        }

        function say(kind, title, detail) {
            if (!status) { return; }
            status.hidden = false;
            status.className = 'upload-status is-' + kind;
            status.innerHTML =
                '<svg class="icon"><use href="#i-' +
                (kind === 'bad' ? 'alert' : kind === 'ok' ? 'info' : 'loader') +
                '"></use></svg><div><b>' + esc(title) + '</b>' +
                (detail ? '<span>' + esc(detail) + '</span>' : '') + '</div>';
        }

        function succeed() {
            uploadCount += 1;
            files.forEach(function (file) { file.fresh = false; });
            files.unshift({
                name: 'nagranie_' + uploadCount + '.csv',
                records: 12000 + uploadCount * 1337,
                added: 'przed chwilą',
                raw: true,
                prepared: true,
                fresh: true
            });
            render();
            say('ok', 'Dodano zestaw ' + files[0].name,
                'Na serwerze leżą teraz dwie wersje pliku: surowa i przygotowana. ' +
                'Zestaw jest gotowy do otwarcia w dashboardzie.');
            busy = false;
        }

        function reject() {
            say('bad', 'Brakuje wymaganych kolumn: gyrx, gyry, gyrz.',
                'Plik nie został dodany — dane muszą dać się przygotować, żeby dało ' +
                'się na nich pracować. Nie zostaje po nim ani pozycja na liście, ani plik na dysku.');
            busy = false;
        }

        all('[data-demo-upload]').forEach(function (button) {
            button.addEventListener('click', function () {
                if (busy) { return; }
                busy = true;

                var ok = button.getAttribute('data-demo-upload') === 'ok';
                say('busy', 'Wysyłanie pliku…', 'plik.csv');

                window.setTimeout(function () {
                    say('busy', 'Przygotowywanie danych…',
                        'filtracja, moduły, jerk, roll i pitch');
                    window.setTimeout(ok ? succeed : reject, 900);
                }, 700);
            });
        });

        render();
    }());
}());

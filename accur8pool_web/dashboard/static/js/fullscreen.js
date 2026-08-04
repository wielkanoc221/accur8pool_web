// Wykres na pełny ekran.
// Ładuj PO dashboard.js — korzysta z instancji Plotly utworzonej tam.
//
// Plotly nie skaluje się samo, gdy kontener zmienia rozmiar przez CSS
// (responsive: true reaguje tylko na resize okna). Dlatego po każdej
// zmianie układu wołamy Plotly.Plots.resize() ręcznie.

document.addEventListener('DOMContentLoaded', function () {
    const card = document.querySelector('.chart-card');
    const btn = document.getElementById('graph-fullscreen');
    const graph = document.getElementById('graph');

    // Brak wykresu (pusty stan) — schowaj przycisk zamiast zostawiać martwy
    if (!card || !btn) return;
    if (!graph) {
        btn.style.display = 'none';
        return;
    }

    let active = false;

    function resize() {
        // graph.data pojawia się dopiero po udanym Plotly.newPlot
        if (window.Plotly && graph.data) {
            Plotly.Plots.resize(graph);
        }
    }

    function setFullscreen(on) {
        active = on;

        card.classList.toggle('is-fullscreen', on);
        document.body.classList.toggle('graph-fullscreen', on);

        btn.setAttribute('aria-pressed', String(on));
        btn.querySelector('.fs-icon').textContent = on ? '✕' : '⛶';
        btn.querySelector('.fs-label').textContent = on ? 'Zamknij' : 'Pełny ekran';

        // Dwa przeliczenia: pierwsze po przemalowaniu, drugie po tym, jak
        // pasek adresu przeglądarki mobilnej skończy się chować (~250ms).
        requestAnimationFrame(resize);
        setTimeout(resize, 300);
    }

    btn.addEventListener('click', function () {
        setFullscreen(!active);
    });

    // Esc wychodzi z pełnego ekranu. Uwaga: mobile-nav.js też słucha Escape
    // (zamyka panele) — te dwie obsługi się nie gryzą, bo dotyczą innych
    // elementów, a w trybie pełnoekranowym panele i tak są ukryte.
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && active) {
            setFullscreen(false);
        }
    });

    // Obrót telefonu / zmiana rozmiaru okna
    window.addEventListener('resize', function () {
        if (active) resize();
    });

    window.addEventListener('orientationchange', function () {
        if (active) setTimeout(resize, 300);
    });

    // Udostępnij na zewnątrz — mobile-nav.js może dodać własny przycisk
    window.toggleGraphFullscreen = function () {
        setFullscreen(!active);
    };
});

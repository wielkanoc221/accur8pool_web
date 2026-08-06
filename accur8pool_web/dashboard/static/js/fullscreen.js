// Wykres na pełny ekran.
// Ładuj PO dashboard.js i motion3d.js — korzysta z instancji Plotly
// utworzonych tam.
//
// Obsługiwana jest KAŻDA karta oznaczona [data-fullscreen-card] (wykres 2D
// i scena 3D), bo obie żyją w tym samym miejscu układu i tylko jedna jest
// widoczna naraz.
//
// Plotly nie skaluje się samo, gdy kontener zmienia rozmiar przez CSS
// (responsive: true reaguje tylko na resize okna). Dlatego po każdej
// zmianie układu wołamy Plotly.Plots.resize() ręcznie.

document.addEventListener('DOMContentLoaded', function () {
    const cards = Array.prototype.slice.call(
        document.querySelectorAll('[data-fullscreen-card]'));
    if (!cards.length) return;

    let activeCard = null;

    function resize(card) {
        const plot = card.querySelector('.plot-area');
        // plot.data pojawia się dopiero po udanym Plotly.newPlot
        if (window.Plotly && plot && plot.data) {
            Plotly.Plots.resize(plot);
        }
    }

    function setFullscreen(card, on) {
        activeCard = on ? card : null;

        card.classList.toggle('is-fullscreen', on);
        document.body.classList.toggle('graph-fullscreen', on);

        const btn = card.querySelector('[data-fullscreen-btn]');
        if (btn) {
            btn.setAttribute('aria-pressed', String(on));
            const icon = btn.querySelector('.fs-icon');
            const label = btn.querySelector('.fs-label');
            if (icon) icon.textContent = on ? '✕' : '⛶';
            if (label) label.textContent = on ? 'Zamknij' : 'Pełny ekran';
        }

        // Dwa przeliczenia: pierwsze po przemalowaniu, drugie po tym, jak
        // pasek adresu przeglądarki mobilnej skończy się chować (~250ms).
        requestAnimationFrame(() => resize(card));
        setTimeout(() => resize(card), 300);
    }

    cards.forEach(function (card) {
        const btn = card.querySelector('[data-fullscreen-btn]');
        if (!btn) return;

        // Brak wykresu (pusty stan) — schowaj przycisk zamiast zostawiać martwy
        if (!card.querySelector('.plot-area')) {
            btn.style.display = 'none';
            return;
        }

        btn.addEventListener('click', function () {
            setFullscreen(card, card !== activeCard);
        });
    });

    // Esc wychodzi z pełnego ekranu. Uwaga: mobile-nav.js też słucha Escape
    // (zamyka panele) — te dwie obsługi się nie gryzą, bo dotyczą innych
    // elementów, a w trybie pełnoekranowym panele i tak są ukryte.
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && activeCard) {
            setFullscreen(activeCard, false);
        }
    });

    // Obrót telefonu / zmiana rozmiaru okna
    window.addEventListener('resize', function () {
        if (activeCard) resize(activeCard);
    });

    window.addEventListener('orientationchange', function () {
        if (activeCard) setTimeout(() => resize(activeCard), 300);
    });
});
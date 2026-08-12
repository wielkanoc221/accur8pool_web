"""Wygląd wykresu 2D — jedno miejsce na wszystko, co jest decyzją wizualną.

Sam dobór punktów jest w series.py; tutaj zostaje figura Plotly i jej
kolory. Podział jest celowy: paleta zmienia się z zupełnie innych powodów
niż decymacja.
"""

from __future__ import annotations

import plotly.graph_objects as go

from .series import Series

# Ten sam krój, którym pisany jest interfejs — inaczej podpisy osi
# wyglądają jak wklejone z innego programu. Scena 3D używa go tak samo
# (motion3d.constants.PLOT_FONT).
PLOT_FONT = ("Inter, system-ui, -apple-system, 'Segoe UI', Roboto, "
             "'Helvetica Neue', Arial, sans-serif")

# Kolejność serii na wykresie. Domyślna paleta Plotly ma dwie pary
# odcieni, których nie rozróżnia osoba z deuteranopią (a przy kilkunastu
# przebiegach IMU na jednym wykresie to nie jest szczegół). Ta kolejność
# jest sprawdzona pod kątem rozróżnialności sąsiadujących kolorów
# w każdym z trzech typów daltonizmu — kolejności NIE zmieniać bez
# ponownego sprawdzenia, bo to ona odpowiada za rozróżnialność, a nie
# same kolory.
#
# Kolor sam w sobie nie identyfikuje serii: nazwę niesie legenda, przez
# którą serie się też włącza i wyłącza.
SERIES_COLORWAY = [
    "#2a78d6",  # niebieski
    "#eb6834",  # pomarańczowy
    "#1baf7a",  # morski
    "#eda100",  # żółty
    "#e87ba4",  # magenta
    "#008300",  # zielony
    "#4a3aa7",  # fioletowy
    "#e34948",  # czerwony
]

_TEXT = "#475569"
_MUTED = "#64748b"
_GRID = "#eef2f7"
_LINE = "#e3e8ef"


def build_figure(series: Series) -> go.Figure:
    """Figura startowa: cały przebieg w rozdzielczości ekranu."""
    figure = go.Figure()

    for name, points in series.payload().items():
        # Scattergl zamiast Scatter — rysowanie idzie przez WebGL na karcie
        # graficznej. Zwykły Scatter przy kilkunastu seriach po kilka
        # tysięcy punktów zamula przewijanie i zoom, szczególnie na
        # telefonie.
        figure.add_trace(go.Scattergl(
            x=points["x"], y=points["y"], name=name, mode="lines",
            line=dict(width=1.6),
            hovertemplate="%{y:.4f}<extra>" + name + "</extra>",
        ))

    figure.update_layout(
        template="plotly_white",
        colorway=SERIES_COLORWAY,
        hovermode="closest",
        # Wykres siedzi w karcie, która ma własne obramowanie i nagłówek —
        # figura nie dokłada do tego drugiej ramki ani tytułu.
        margin=dict(l=56, r=8, t=8, b=44),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=PLOT_FONT, size=12, color=_TEXT),
        xaxis=_axis("Indeks"),
        yaxis=_axis("Wartość znormalizowana"),
        legend=_legend(),
        hoverlabel=dict(
            bgcolor="#0f172a",
            bordercolor="#0f172a",
            font=dict(family=PLOT_FONT, size=12, color="#f8fafc"),
        ),
        uirevision="keep",  # zoom przeżywa aktualizacje danych
    )
    return figure


def _axis(title: str) -> dict:
    return dict(
        title=dict(text=title, font=dict(size=11.5, color=_MUTED)),
        gridcolor=_GRID,
        zerolinecolor=_LINE,
        linecolor=_LINE,
        ticks="outside",
        tickcolor=_LINE,
        ticklen=4,
        tickfont=dict(size=11, color=_MUTED),
    )


def _legend() -> dict:
    """Legenda jest jedynym miejscem, w którym seria dostaje NAZWĘ.

    Sam kolor nie wystarcza do rozpoznania przebiegu, a część kolorów jest
    jasna. Klikanie w legendę włącza i wyłącza serie, więc pozycje muszą
    być czytelne, nie drobne.
    """
    return dict(
        orientation="v",
        x=1.01, y=1, xanchor="left", yanchor="top",
        font=dict(size=11.5, color=_TEXT),
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor=_LINE,
        borderwidth=1,
        itemsizing="constant",
        itemwidth=30,
        tracegroupgap=4,
    )

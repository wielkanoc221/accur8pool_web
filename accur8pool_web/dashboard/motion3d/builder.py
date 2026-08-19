"""Złożenie całości: z okna nagrania robi scenę, klatki i opis.

Każdy krok jest osobno testowalny i osobno opisany w swoim module; tutaj
zostaje sama KOLEJNOŚĆ, w której się je wykonuje, oraz kształt odpowiedzi
wysyłanej do przeglądarki.
"""

from __future__ import annotations

import numpy as np

from . import scene as scene_module
from .constants import WATCH_LENGTH_CM
from .frames import FrameSampler
from .orientation import Orientation
from .recording import RecordingWindow, SensorRecording
from .trajectory import Trajectory


class MotionBuilder:
    """Rekonstrukcja jednego uderzenia — od okna próbek do gotowego JSON-a."""

    def __init__(self, recording: SensorRecording, lo: int, hi: int,
                 phases=(), fps=0.0, smooth: bool = True,
                 watch_scale: float = 1.0):
        self._recording = recording
        self._phases = phases
        self._fps = fps
        self._watch_scale = watch_scale

        self._window: RecordingWindow = recording.window(lo, hi)
        self._orientation = Orientation.solve(self._window, smooth=smooth)
        self._trajectory = Trajectory.solve(self._window, self._orientation)

    # ------------------------------------------------------------
    #  WYNIK
    # ------------------------------------------------------------

    def build(self) -> dict:
        window = self._window

        positions = self._trajectory.position                         # [m]
        velocities = self._trajectory.velocity                        # [m/s]

        frames = FrameSampler.of(window.sample_count, window.sample_rate, self._fps)
        frame_seconds = frames.take(window.seconds)
        frame_positions = frames.take(positions)
        frame_quaternions = frames.take(self._orientation.q)

        # Statystyki liczą się z metrów, scena rysuje w centymetrach.
        path_m = float(np.linalg.norm(np.diff(frame_positions, axis=0), axis=1).sum())
        max_speed = float(np.linalg.norm(velocities, axis=1).max())
        positions_cm = frame_positions * 100.0

        bounds = scene_module.SceneBounds.around(positions_cm, self._watch_scale)
        watch = scene_module.WatchGeometry.of(bounds.watch_scale)

        spans = scene_module.phase_spans(
            self._phases, window.seconds, window.lo, window.hi, frame_seconds)
        phase_per_frame = scene_module.phase_per_frame(spans, len(frames))

        figure, dynamic = scene_module.FigureBuilder(
            positions_cm, bounds, watch.colors).build()

        return {
            "figure": figure,
            "payload": self._payload(frame_seconds, positions_cm, frame_quaternions,
                                     phase_per_frame, watch, bounds, dynamic, spans),
            "meta": self._meta(frames, bounds, spans, path_m, max_speed),
        }

    # ------------------------------------------------------------
    #  DANE DLA ODTWARZACZA
    # ------------------------------------------------------------

    @staticmethod
    def _payload(frame_seconds, positions_cm, quaternions, phase_per_frame,
                 watch, bounds, dynamic, spans) -> dict:
        """Wszystko, czym przeglądarka rusza scenę — i nic ponadto."""
        return {
            "t": [round(float(value), 4) for value in frame_seconds],
            "pos": [[round(float(v), 4) for v in row] for row in positions_cm],
            "quat": [[round(float(v), 6) for v in row] for row in quaternions],
            "phase": phase_per_frame.tolist(),
            "verts": [[round(v, 4) for v in vertex] for vertex in watch.vertices],
            "faces": {"i": [face[0] for face in watch.faces],
                      "j": [face[1] for face in watch.faces],
                      "k": [face[2] for face in watch.faces]},
            "axis_len": round(bounds.axis_length, 4),
            "dynamic": dynamic,
            "spans": spans,
        }

    def _meta(self, frames, bounds, spans, path_m, max_speed) -> dict:
        """Opis rekonstrukcji: co policzono, z czego i jak dobrze."""
        window = self._window
        orientation = self._orientation
        trajectory = self._trajectory

        return {
            "label": f"{orientation.description}; {trajectory.describe()}",
            "source": orientation.source,
            "acc_source": trajectory.acc_source,
            "frames": len(frames),
            # Ostatnia klatka JEST ostatnią próbką zaznaczenia, więc czas
            # animacji i długość wycinka to jedno i to samo.
            "duration": round(window.duration, 4),
            "sample_rate": round(window.sample_rate, 2),
            "stride": frames.stride,
            "smoothed": orientation.smoothed,
            # Rozjazd rotation vectora z żyroskopem, w wielokrotnościach
            # samego sygnału. None = nie było czym porównać. Powyżej
            # ROT_GYRO_MAX orientacja poszła z żyroskopu — bez tej liczby
            # nie da się zauważyć, że plik ma zepsuty wektor obrotu.
            "rot_vs_gyro": (round(orientation.rot_vs_gyro, 3)
                            if orientation.rot_vs_gyro is not None else None),
            "rows": window.hi - window.lo,
            "lo": window.lo,
            "hi": window.hi,
            "path_cm": round(path_m * 100.0, 2),
            "v_max": round(max_speed, 4),
            "has_position": trajectory.known,
            # Ile toru usunął filtr dryfu — w centymetrach i w stosunku do
            # tego, co zostało na ekranie. To jest CENA podwójnego
            # całkowania i jedyna liczba, po której da się poznać, że
            # oglądany kształt jest w większości dziełem filtru, a nie
            # pomiaru. Bez niej długi segment rysuje wiarygodnie wyglądającą
            # pętlę, która jest czystym odpłynięciem całkowania.
            "drift_cm": round(trajectory.drift_m * 100.0, 1),
            "drift_ratio": round(trajectory.drift_ratio, 3),
            # Bok sześcianu sceny. Bez tej liczby „mały ruch” i „duży ruch”
            # wyglądają na ekranie tak samo — scena skaluje się do
            # zawartości, więc dopiero ona mówi, co się właściwie ogląda.
            "span_cm": round(bounds.side, 3),
            # Dłuższy bok narysowanej koperty. Stoi obok span_cm celowo:
            # ta para to CAŁA skala obrazu — „zegarek 4.4 cm na scenie
            # 62 cm” mówi wprost, ile razy zamach był większy od
            # urządzenia. Przy ręcznym ?watch= liczba rośnie i od razu
            # widać, że proporcja przestała być prawdziwa.
            "watch_cm": round(WATCH_LENGTH_CM * bounds.watch_scale, 2),
            "time_source": self._recording.time.source,
            "gaps": self._recording.time.gaps,
            "phases": spans,
        }

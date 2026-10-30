"""Monochromatic 3D Interactive Globe Widget for TicNet.

Renders an authentic, high-contrast 3D Earth matching the updated reference design:
- Axial tilt to the right side (Earth's natural tilt around ~18.5°).
- Gentle, graceful, time-based smooth rotation showing all fine details clearly.
- Crisp white/light spherical surface with subtle volumetric 3D lighting.
- High-fidelity periwinkle blue continental landmasses with rich geographic outlines:
  North America, Great Lakes highlight, South America, Eurasia, Africa, Madagascar,
  Japan, Indonesia, Philippines, Australia, New Zealand, UK, Ireland, and Greenland.
- Geographical graticule grid lines with highlighted Equator, Prime Meridian, and Tropics.
- Concentric circle target nodes (crisp black outer ring with solid black center dot).
- Reddish-coral dotted 3D network connection arcs with animated traveling packet telemetry.
- Floating subtle orbital particles creating a sophisticated network/technology atmosphere.
- Smooth mouse drag interaction (yaw and pitch) with realistic inertia decay.
"""

from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# High-Fidelity Continents & Islands Vector Polygons (Latitude, Longitude)
# Scientifically recognizable outlines matching global geography
# ---------------------------------------------------------------------------
_CONTINENT_POLYGONS: Dict[str, List[Tuple[float, float]]] = {
    "north_america": [
        # Alaska
        (71.3, -156.5), (70.5, -162.0), (66.0, -168.0), (64.5, -165.0),
        (60.0, -166.0), (58.5, -158.0), (55.0, -162.0), (54.0, -165.0),
        (58.0, -152.0), (60.0, -148.0), (59.5, -140.0), (56.0, -134.0),
        # Canada West & US Pacific NW
        (54.0, -131.0), (50.5, -127.5), (48.5, -124.5), (46.0, -124.0),
        # California & Baja
        (40.5, -124.5), (38.0, -123.0), (34.5, -120.5), (32.5, -117.2),
        (30.0, -115.5), (28.0, -114.0), (23.0, -110.0), (24.0, -110.0),
        (28.0, -112.0), (31.5, -114.5),
        # Mexico & Central America Pacific
        (28.0, -111.0), (24.0, -107.0), (20.0, -105.0), (16.0, -98.0),
        (14.5, -92.5), (12.5, -87.0), (9.0, -84.0), (8.0, -78.0),
        # Panama to Caribbean / Gulf Coast
        (9.0, -79.5), (10.5, -83.5), (15.5, -83.5), (16.0, -88.0),
        (18.5, -88.0), (21.5, -87.0), (21.5, -90.5), (19.0, -91.0),
        (18.5, -94.0), (22.0, -97.5), (26.0, -97.0), (29.0, -95.0),
        (29.5, -92.0), (30.0, -88.0), (30.0, -84.0),
        # Florida
        (28.0, -82.5), (25.0, -81.0), (25.0, -80.0), (28.0, -80.5),
        (31.0, -81.5),
        # US East Coast
        (34.0, -77.5), (36.0, -75.5), (39.0, -74.5), (40.5, -73.8),
        (41.5, -71.0), (42.5, -70.5), (44.5, -67.0),
        # Canadian Maritimes & St. Lawrence
        (45.0, -64.0), (47.0, -64.5), (46.0, -60.0), (48.0, -64.0),
        (50.0, -66.0), (52.0, -56.0), (55.0, -60.0), (60.0, -64.0),
        # Hudson Bay
        (62.5, -78.0), (55.0, -80.0), (52.0, -82.0), (56.0, -89.0),
        (58.5, -94.0), (63.0, -91.0), (64.5, -87.0),
        # Arctic Canada
        (68.0, -98.0), (69.0, -115.0), (70.0, -128.0), (71.3, -156.5),
    ],
    "south_america": [
        # North coast: Colombia, Venezuela, Guianas
        (12.5, -71.8), (11.0, -66.0), (10.5, -62.0), (8.5, -59.5),
        (6.0, -55.0), (4.5, -51.5),
        # Brazil Atlantic coast (prominent eastern bulge)
        (1.0, -49.5), (-2.5, -44.0), (-4.0, -38.0), (-5.2, -35.2),
        (-8.0, -34.8), (-13.0, -38.5), (-18.0, -39.5), (-22.5, -41.8),
        (-24.0, -46.3), (-27.0, -48.5), (-32.0, -52.0),
        # Rio de la Plata & Argentina
        (-35.0, -56.0), (-38.5, -57.5), (-42.0, -65.0), (-47.0, -66.0),
        (-52.0, -68.0), (-55.0, -67.0),
        # Chile Pacific coast
        (-54.0, -73.0), (-46.0, -75.0), (-40.0, -74.0), (-33.0, -71.8),
        (-24.0, -70.5), (-18.0, -70.5),
        # Peru & Ecuador
        (-12.0, -77.2), (-5.0, -81.2), (-2.0, -80.5), (1.0, -79.5),
        (4.0, -77.5), (8.0, -77.0), (12.5, -71.8),
    ],
    "africa": [
        # Mediterranean coast
        (35.8, -5.8), (36.0, 0.0), (37.2, 10.0), (33.0, 11.5),
        (32.0, 20.0), (32.0, 25.0), (31.5, 32.5),
        # Red Sea & Horn of Africa
        (28.0, 33.5), (22.0, 36.5), (15.0, 40.0), (12.0, 43.5),
        (11.5, 51.2), (8.0, 50.0), (2.0, 45.0), (-2.0, 40.5),
        # East Africa & Mozambique Channel
        (-11.0, 40.5), (-16.0, 38.0), (-24.0, 35.0), (-30.0, 31.0),
        # South Africa (Cape of Good Hope / Cape Agulhas)
        (-34.0, 26.0), (-34.8, 20.0), (-34.0, 18.4),
        # West Africa / Atlantic
        (-30.0, 17.0), (-23.0, 14.5), (-16.0, 12.0), (-8.0, 13.0),
        (-5.0, 12.0), (0.0, 9.5), (4.5, 8.5),
        # Gulf of Guinea & West Africa Bulge
        (4.5, 6.0), (6.0, 2.5), (5.5, -1.0), (4.5, -7.5),
        (7.0, -11.8), (11.0, -15.5), (14.8, -17.5),
        # Northwest Africa
        (21.0, -17.0), (26.0, -14.5), (31.0, -10.0), (35.8, -5.8),
    ],
    "eurasia": [
        # Gibraltar & Iberia
        (36.0, -6.0), (37.0, -9.0), (43.0, -9.2), (43.5, -2.0),
        # France & Low Countries
        (46.0, -1.5), (48.5, -4.5), (49.5, -1.0), (51.0, 2.0),
        (53.5, 6.0), (54.5, 9.0),
        # Scandinavia & Baltic
        (57.5, 10.0), (56.0, 12.5), (59.0, 11.0), (62.0, 5.0),
        (68.0, 14.0), (71.0, 26.0),
        # Northern Russia & Siberian Arctic Coast
        (68.0, 38.0), (67.0, 45.0), (70.0, 60.0), (73.0, 80.0),
        (76.0, 110.0), (73.0, 140.0), (70.0, 160.0), (68.0, 175.0),
        (66.0, -170.0),
        # Kamchatka & Far East Coast
        (60.0, 165.0), (55.0, 160.0), (52.0, 156.0), (55.0, 138.0),
        (48.0, 140.0), (43.0, 132.0),
        # Korean Peninsula
        (40.0, 128.5), (37.5, 126.5), (34.5, 127.5), (36.0, 129.5),
        (39.5, 124.5),
        # China Coast
        (39.0, 118.0), (36.0, 120.5), (30.0, 122.0), (24.5, 118.5),
        (22.0, 114.0),
        # Southeast Asia & Indochina
        (21.0, 107.5), (16.0, 108.5), (10.0, 107.0), (5.0, 103.0),
        (1.3, 103.8), (3.0, 101.5), (12.0, 98.5), (16.0, 96.0),
        (21.0, 92.0),
        # Bay of Bengal & India
        (22.5, 89.5), (21.5, 87.0), (17.0, 83.0), (13.0, 80.2),
        (8.1, 77.5), (9.5, 76.5), (15.0, 74.0), (19.0, 72.8),
        (22.5, 69.5),
        # Middle East & Arabian Peninsula
        (24.0, 67.5), (25.0, 63.0), (25.5, 58.0), (22.5, 59.5),
        (15.0, 53.0), (13.0, 44.5), (20.0, 40.0), (27.0, 35.0),
        (29.5, 32.5),
        # Mediterranean Coast & Southern Europe
        (31.5, 34.5), (36.0, 35.0), (37.5, 27.0), (39.0, 26.0),
        (37.5, 22.0), (39.5, 20.0), (44.0, 15.0), (45.5, 13.5),
        (41.0, 16.5), (38.0, 15.5), (41.0, 12.5), (44.0, 9.5),
        (43.5, 4.0), (38.0, 0.0), (36.0, -6.0),
    ],
    "australia": [
        (-12.5, 131.0), (-14.0, 126.0), (-20.0, 118.5), (-22.0, 114.0),
        (-28.0, 114.0), (-32.0, 115.5), (-35.0, 117.5), (-34.5, 124.0),
        (-32.0, 132.0), (-35.5, 137.5), (-38.5, 143.5), (-38.0, 148.0),
        (-34.0, 151.2), (-28.0, 153.5), (-22.0, 150.0), (-16.0, 145.5),
        (-11.0, 142.5), (-14.5, 136.0), (-12.5, 131.0),
    ],
    "uk": [
        (58.5, -4.5), (57.5, -1.8), (53.5, 0.2), (51.5, 1.2),
        (50.5, -1.0), (50.0, -5.2), (51.8, -5.0), (53.5, -3.0),
        (55.5, -4.8), (58.5, -4.5),
    ],
    "ireland": [
        (55.2, -7.5), (53.8, -6.0), (51.8, -8.0), (51.5, -9.8),
        (53.5, -10.0), (55.2, -7.5),
    ],
    "japan": [
        (45.5, 142.0), (43.5, 145.5), (42.0, 141.0), (39.5, 141.5),
        (35.5, 140.0), (34.0, 136.0), (32.0, 130.5), (34.0, 131.0),
        (36.5, 137.0), (41.0, 140.5), (45.5, 142.0),
    ],
    "madagascar": [
        (-12.0, 49.3), (-16.0, 49.8), (-25.0, 47.0), (-25.5, 45.0),
        (-18.0, 44.0), (-13.0, 48.0), (-12.0, 49.3),
    ],
    "greenland": [
        (83.0, -30.0), (80.0, -18.0), (76.0, -20.0), (70.0, -22.0),
        (65.0, -38.0), (60.0, -43.0), (64.0, -50.0), (68.0, -54.0),
        (76.0, -68.0), (81.0, -65.0), (83.0, -30.0),
    ],
    "indonesia_sumatra": [
        (5.5, 95.3), (2.0, 99.0), (-3.0, 103.0), (-5.8, 105.8),
        (-3.5, 102.0), (1.0, 98.0), (5.5, 95.3),
    ],
    "indonesia_java": [
        (-6.0, 106.0), (-6.8, 109.0), (-7.5, 112.5), (-8.3, 114.5),
        (-8.5, 112.0), (-7.8, 108.5), (-6.0, 106.0),
    ],
    "borneo": [
        (7.0, 117.0), (5.0, 118.5), (1.0, 117.5), (-3.5, 116.0),
        (-4.0, 112.5), (0.5, 109.0), (4.5, 114.0), (7.0, 117.0),
    ],
    "philippines": [
        (18.5, 121.0), (15.0, 121.5), (13.0, 124.0), (7.0, 126.0),
        (6.0, 122.0), (10.0, 122.5), (14.5, 120.0), (18.5, 121.0),
    ],
    "cuba": [
        (23.2, -82.4), (22.0, -78.0), (20.0, -74.5), (20.0, -76.5),
        (22.0, -84.0), (23.2, -82.4),
    ],
    "new_zealand_north": [
        (-35.0, 174.0), (-38.0, 178.0), (-41.5, 175.0), (-39.0, 174.0),
        (-35.0, 174.0),
    ],
    "new_zealand_south": [
        (-41.0, 174.0), (-44.0, 171.0), (-46.5, 166.5), (-46.5, 170.0),
        (-42.0, 172.5), (-41.0, 174.0),
    ],
}

# Interior lakes / features (Great Lakes in North America)
_GREAT_LAKES_POLYGON: List[Tuple[float, float]] = [
    (48.5, -89.0),
    (47.5, -84.5),
    (45.5, -82.0),
    (43.5, -79.0),
    (42.0, -83.0),
    (44.0, -85.5),
    (46.5, -88.0),
    (48.5, -89.0),
]


def _subdivide_polygon(
    poly: List[Tuple[float, float]], max_deg: float = 2.0
) -> List[Tuple[float, float]]:
    """Subdivide polygon edges to curve naturally around the 3D spherical horizon."""
    subdivided: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        dlat = p2[0] - p1[0]
        dlon = p2[1] - p1[1]
        # Antimeridian wrap: ensure shortest path across +-180°
        if dlon > 180.0:
            dlon -= 360.0
        elif dlon < -180.0:
            dlon += 360.0
        dist = math.hypot(dlat, dlon)
        steps = max(1, int(math.ceil(dist / max_deg)))
        for s in range(steps):
            t = s / steps
            subdivided.append((p1[0] + dlat * t, p1[1] + dlon * t))
    return subdivided


# Pre-subdivided continents for fine curve interpolation
_SUBDIVIDED_CONTINENTS = {
    name: _subdivide_polygon(poly, max_deg=2.0)
    for name, poly in _CONTINENT_POLYGONS.items()
}

_SUBDIVIDED_GREAT_LAKES = _subdivide_polygon(_GREAT_LAKES_POLYGON, max_deg=1.5)

# Precomputed spherical constants (cos_lat, sin_lat, lon_deg) to accelerate render loop
_PRECOMPUTED_CONTINENTS: Dict[str, List[Tuple[float, float, float]]] = {}
for _name, _pts in _SUBDIVIDED_CONTINENTS.items():
    _pre: List[Tuple[float, float, float]] = []
    for _lat, _lon in _pts:
        _rad = math.radians(_lat)
        _pre.append((math.cos(_rad), math.sin(_rad), _lon))
    _PRECOMPUTED_CONTINENTS[_name] = _pre

_PRECOMPUTED_GREAT_LAKES: List[Tuple[float, float, float]] = [
    (math.cos(math.radians(_lat)), math.sin(math.radians(_lat)), _lon)
    for _lat, _lon in _SUBDIVIDED_GREAT_LAKES
]


# ---------------------------------------------------------------------------
# Network Endpoint Hubs & Concentric Target Markers (Matching Reference Image)
# ---------------------------------------------------------------------------
_PING_MARKERS = [
    {
        "id": "west_coast",
        "name": "San Francisco / Silicon Valley",
        "lat": 37.7749,
        "lon": -122.4194,
        "latency_ms": 38,
        "status": "good",
    },
    {
        "id": "new_york",
        "name": "New York",
        "lat": 40.7128,
        "lon": -74.0060,
        "latency_ms": 42,
        "status": "good",
    },
    {
        "id": "sao_paulo",
        "name": "São Paulo",
        "lat": -23.5505,
        "lon": -46.6333,
        "latency_ms": 78,
        "status": "good",
    },
    {
        "id": "london",
        "name": "London",
        "lat": 51.5074,
        "lon": -0.1278,
        "latency_ms": 56,
        "status": "good",
    },
    {
        "id": "frankfurt",
        "name": "Frankfurt",
        "lat": 50.1109,
        "lon": 8.6821,
        "latency_ms": 52,
        "status": "good",
    },
    {
        "id": "cape_town",
        "name": "Cape Town",
        "lat": -33.9249,
        "lon": 18.4241,
        "latency_ms": 94,
        "status": "good",
    },
    {
        "id": "dubai",
        "name": "Dubai",
        "lat": 25.2048,
        "lon": 55.2708,
        "latency_ms": 68,
        "status": "good",
    },
    {
        "id": "singapore",
        "name": "Singapore",
        "lat": 1.3521,
        "lon": 103.8198,
        "latency_ms": 64,
        "status": "good",
    },
    {
        "id": "tokyo",
        "name": "Tokyo",
        "lat": 35.6762,
        "lon": 139.6503,
        "latency_ms": 82,
        "status": "good",
    },
    {
        "id": "sydney",
        "name": "Sydney",
        "lat": -33.8688,
        "lon": 151.2093,
        "latency_ms": 118,
        "status": "good",
    },
]

# Geographically authentic backbone arcs
_NETWORK_ARCS = [
    ("west_coast", "new_york"),
    ("new_york", "london"),
    ("london", "frankfurt"),
    ("frankfurt", "dubai"),
    ("london", "sao_paulo"),
    ("sao_paulo", "cape_town"),
    ("cape_town", "dubai"),
    ("dubai", "singapore"),
    ("singapore", "tokyo"),
    ("tokyo", "west_coast"),
    ("singapore", "sydney"),
]


# ---------------------------------------------------------------------------
# Floating Orbital Particle System
# ---------------------------------------------------------------------------
class GlobeParticle:
    """Subtle floating orbital particle around the globe."""

    def __init__(self, seed: int) -> None:
        rng = random.Random(seed)
        self.base_lat = rng.uniform(-65.0, 65.0)
        self.base_lon = rng.uniform(-180.0, 180.0)
        self.orbit_ratio = rng.uniform(1.10, 1.42)
        # Drift rates (degrees per second)
        self.drift_lon_speed = rng.uniform(-0.18, 0.22)
        self.osc_rate = rng.uniform(0.5, 1.5)
        self.osc_amp = rng.uniform(2.5, 7.0)
        self.size = rng.uniform(1.6, 2.8)
        self.base_alpha = rng.uniform(0.35, 0.75)
        self.pulse_phase = rng.uniform(0.0, math.pi * 2.0)
        # Gentle tint: cool tech cyan / ice blue / periwinkle
        t = rng.uniform(0.0, 1.0)
        if t < 0.45:
            self.color = (92, 126, 214)     # Periwinkle
        elif t < 0.80:
            self.color = (56, 189, 248)     # Cyan
        else:
            self.color = (147, 197, 253)    # Ice Blue


class InteractiveGlobeWidget(QWidget):
    """Monochromatic 3D Interactive Network Globe Widget matching updated reference design."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 310)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy(),
        )
        self.setMouseTracking(True)

        # 3D Orientation state
        # Initial orientation showcases the Atlantic/Americas/Europe corridor
        self.yaw_deg: float = 38.0
        self.pitch_deg: float = -12.0
        # Axial tilt to the right side (Earth's natural tilt, slightly to the right ~18.5°)
        self.tilt_deg: float = 18.5

        # Automatic rotation speeds
        # Gentle, graceful, slightly slower rotation showing all fine details clearly
        self.base_speed: float = 0.38        # Smooth baseline rotation (°/frame at 45 FPS)
        self.hover_speed: float = 0.58       # Subtle acceleration on hover (°/frame)
        self.current_speed: float = self.base_speed
        self.target_speed: float = self.base_speed

        # Mouse interaction & dragging state
        self.is_dragging: bool = False
        self.last_mouse_pos: Optional[QPointF] = None
        self.velocity_yaw: float = 0.0
        self.velocity_pitch: float = 0.0

        # Hover state
        self.hovered_marker_id: Optional[str] = None
        self.is_cursor_inside: bool = False

        # Animation timing & time-based motion
        self.pulse_phase: float = 0.0
        self.arc_pulse_t: float = 0.0
        self.anim_time: float = 0.0
        self._last_tick_time: float = time.perf_counter()

        # Cached frame rotation constants
        self._cos_pitch: float = math.cos(math.radians(self.pitch_deg))
        self._sin_pitch: float = math.sin(math.radians(self.pitch_deg))
        self._cos_tilt: float = math.cos(math.radians(self.tilt_deg))
        self._sin_tilt: float = math.sin(math.radians(self.tilt_deg))

        # Floating subtle orbital particles (42 deterministic particles)
        self.particles: List[GlobeParticle] = [
            GlobeParticle(i * 101 + 42) for i in range(42)
        ]

        # Frame loop timer (~45 FPS for fluid, graceful motion)
        self._timer = QTimer(self)
        self._timer.setInterval(22)
        self._timer.timeout.connect(self._on_animation_tick)
        self._timer.start()

    def _check_reduced_motion(self) -> bool:
        """Check operating system reduced-motion preference."""
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "reducedMotion"):
            return bool(hints.reducedMotion())
        return False

    def _on_animation_tick(self) -> None:
        """Animation frame loop: updates angles, pulses, and repaints with delta-time."""
        if not self.isVisible():
            return

        now = time.perf_counter()
        dt = max(0.001, min(0.060, now - self._last_tick_time))
        self._last_tick_time = now
        self.anim_time += dt

        is_reduced = self._check_reduced_motion()

        # Update pulse cycles scaled by elapsed time
        self.pulse_phase = (self.pulse_phase + 3.6 * dt) % (2.0 * math.pi)
        self.arc_pulse_t = (self.arc_pulse_t + 0.55 * dt) % 1.0

        # Smooth speed ramp on hover
        self.current_speed += (self.target_speed - self.current_speed) * min(1.0, 6.0 * dt)

        if self.is_dragging:
            pass  # Directly driven by mouseMoveEvent
        else:
            # Apply residual dragging inertia with decay
            if abs(self.velocity_yaw) > 0.01:
                self.yaw_deg = (self.yaw_deg + self.velocity_yaw * (dt / 0.022)) % 360.0
                self.velocity_yaw *= math.pow(0.92, dt / 0.022)
            elif not is_reduced:
                # Continuous time-based automatic rotation independent of framerate
                deg_per_sec = self.current_speed / 0.022
                self.yaw_deg = (self.yaw_deg + deg_per_sec * dt) % 360.0

            if abs(self.velocity_pitch) > 0.01:
                self.pitch_deg = max(
                    -55.0, min(55.0, self.pitch_deg + self.velocity_pitch * (dt / 0.022))
                )
                self.velocity_pitch *= math.pow(0.92, dt / 0.022)

        self.update()

    # -----------------------------------------------------------------------
    # Mouse & Cursor Interaction
    # -----------------------------------------------------------------------
    def enterEvent(self, event) -> None:
        self.is_cursor_inside = True
        self.target_speed = self.hover_speed
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.is_cursor_inside = False
        self.target_speed = self.base_speed
        self.hovered_marker_id = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = True
            self.last_mouse_pos = event.position()
            self.velocity_yaw = 0.0
            self.velocity_pitch = 0.0
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()

        if self.is_dragging and self.last_mouse_pos:
            dx = pos.x() - self.last_mouse_pos.x()
            dy = pos.y() - self.last_mouse_pos.y()
            self.yaw_deg = (self.yaw_deg + dx * 0.40) % 360.0
            self.pitch_deg = max(-55.0, min(55.0, self.pitch_deg + dy * 0.32))
            self.velocity_yaw = dx * 0.40
            self.velocity_pitch = dy * 0.32
            self.last_mouse_pos = pos
            self.update()
            event.accept()
            return

        # Check proximity to ping markers for hover highlight
        cx, cy, r = self._get_globe_bounds()
        hovered: Optional[str] = None
        self._update_frame_trig()

        for m in _PING_MARKERS:
            x, y, z = self._project_lat_lon(m["lat"], m["lon"], r)
            if z > 0:  # Facing viewer
                sx = cx + x
                sy = cy + y
                dist = math.hypot(pos.x() - sx, pos.y() - sy)
                if dist <= 16:
                    hovered = m["id"]
                    break

        if hovered != self.hovered_marker_id:
            self.hovered_marker_id = hovered
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if hovered else Qt.CursorShape.ArrowCursor
            )
            self.update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if self.hovered_marker_id else Qt.CursorShape.ArrowCursor
            )
            event.accept()
        super().mouseReleaseEvent(event)

    # -----------------------------------------------------------------------
    # 3D Spherical Math & Coordinate Projection (With Right Axial Tilt)
    # -----------------------------------------------------------------------
    def _update_frame_trig(self) -> None:
        """Precompute frame rotation constants once per render/hit-test."""
        pitch = math.radians(self.pitch_deg)
        tilt = math.radians(self.tilt_deg)
        self._cos_pitch = math.cos(pitch)
        self._sin_pitch = math.sin(pitch)
        self._cos_tilt = math.cos(tilt)
        self._sin_tilt = math.sin(tilt)

    def _get_globe_bounds(self) -> Tuple[float, float, float]:
        """Compute the center (cx, cy) and radius r of the 3D globe."""
        w = float(self.width())
        h = float(self.height())
        r = min(w * 0.44, h * 0.44)
        r = max(80.0, r)
        cx = w * 0.50
        cy = h * 0.50
        return cx, cy, r

    def _project_lat_lon(
        self, lat_deg: float, lon_deg: float, r: float
    ) -> Tuple[float, float, float]:
        """Convert geographic latitude and longitude into projected 3D Cartesian coordinates.

        Applies:
        1. Longitude rotation around polar axis (yaw).
        2. Pitch tilt (elevation angle).
        3. Axial tilt to the right (tilt_deg leaning North pole rightwards).
        """
        lat = math.radians(lat_deg)
        lon = math.radians(lon_deg + self.yaw_deg)

        # 3D unit coordinates
        x0 = math.cos(lat) * math.sin(lon)
        y0 = -math.sin(lat)
        z0 = math.cos(lat) * math.cos(lon)

        # 1. Pitch tilt around X-axis
        x1 = x0
        y1 = y0 * self._cos_pitch - z0 * self._sin_pitch
        z1 = y0 * self._sin_pitch + z0 * self._cos_pitch

        # 2. Axial roll / tilt to the right around Z-axis
        x = x1 * self._cos_tilt - y1 * self._sin_tilt
        y = x1 * self._sin_tilt + y1 * self._cos_tilt
        z = z1

        return x * r, y * r, z * r

    def _project_point_3d(
        self, vx: float, vy: float, vz: float, rad: float
    ) -> Tuple[float, float, float]:
        """Project an arbitrary 3D vector at radius rad using pitch and right tilt."""
        x1 = vx
        y1 = vy * self._cos_pitch - vz * self._sin_pitch
        z1 = vy * self._sin_pitch + vz * self._cos_pitch

        x = (x1 * self._cos_tilt - y1 * self._sin_tilt) * rad
        y = (x1 * self._sin_tilt + y1 * self._cos_tilt) * rad
        z = z1 * rad
        return x, y, z

    # -----------------------------------------------------------------------
    # Rendering Pipeline
    # -----------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        self._update_frame_trig()
        cx, cy, r = self._get_globe_bounds()

        # 1. Background floating particles (particles behind globe horizon)
        self._draw_particles(painter, cx, cy, r, foreground=False)

        # 2. Soft Ambient Ground Shadow
        self._draw_ambient_shadow(painter, cx, cy, r)

        # 3. Globe Sphere Base Body (Crisp Pure White with Subtle 3D Depth)
        self._draw_sphere_body(painter, cx, cy, r)

        # 4. Clipping to Globe Sphere for Continents, Graticules & Details
        sphere_path = QPainterPath()
        sphere_path.addEllipse(QPointF(cx, cy), r, r)

        painter.save()
        painter.setClipPath(sphere_path)

        # 4A. Geographical Grid Lines (Graticules with highlighted Equator & Tropics)
        self._draw_graticules(painter, cx, cy, r)

        # 4B. Continents (Periwinkle Blue Vector Landmasses with rich outlines)
        self._draw_continents(painter, cx, cy, r)

        # 4C. Great Lakes Highlight in North America
        self._draw_great_lakes(painter, cx, cy, r)

        # 4D. Soft Spherical Horizon Edge Shadow
        self._draw_sphere_edge_shadow(painter, cx, cy, r)

        painter.restore()

        # 5. Clean Spherical Boundary Ring
        self._draw_spherical_rim(painter, cx, cy, r)

        # 6. 3D Great-Circle Network Connection Arcs (Anchored with strict 3D occlusion)
        self._draw_network_arcs(painter, cx, cy, r)

        # 7. Concentric Circle Target Nodes (With smooth horizon fade)
        self._draw_ping_markers(painter, cx, cy, r)

        # 8. Foreground floating particles (particles floating in front of globe)
        self._draw_particles(painter, cx, cy, r, foreground=True)

        # 9. Hover Callout Tooltip
        self._draw_hover_tooltip(painter, cx, cy, r)

        painter.end()

    # -----------------------------------------------------------------------
    # Rendering Sub-routines
    # -----------------------------------------------------------------------
    def _draw_ambient_shadow(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Render subtle soft ambient drop shadow beneath the floating globe."""
        shadow_rect = QRectF(cx - r * 0.75, cy + r * 0.92, r * 1.50, r * 0.18)
        shadow_grad = QRadialGradient(QPointF(cx, cy + r * 1.00), r * 0.75)
        shadow_grad.setColorAt(0.0, QColor(15, 23, 42, 28))
        shadow_grad.setColorAt(0.6, QColor(15, 23, 42, 10))
        shadow_grad.setColorAt(1.0, QColor(15, 23, 42, 0))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shadow_grad))
        painter.drawEllipse(shadow_rect)

    def _draw_sphere_body(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Draw clean white/light 3D shaded sphere ocean surface."""
        light_x = cx - r * 0.35
        light_y = cy - r * 0.35
        body_grad = QRadialGradient(QPointF(light_x, light_y), r * 1.30)
        body_grad.setColorAt(0.0, QColor(255, 255, 255))      # Crisp specular white
        body_grad.setColorAt(0.40, QColor(253, 254, 255))     # Light pristine white
        body_grad.setColorAt(0.80, QColor(245, 247, 252))     # Soft slate-white midtone
        body_grad.setColorAt(1.0, QColor(234, 239, 248))      # Delicate horizon tone

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(body_grad))
        painter.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_graticules(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Draw fine latitude and longitude grid lines, highlighting Equator and Tropics."""
        grid_pen = QPen(QColor(210, 218, 234, 190), 0.85)
        grid_pen.setStyle(Qt.PenStyle.SolidLine)

        equator_pen = QPen(QColor(170, 185, 215, 240), 1.1)
        tropic_pen = QPen(QColor(195, 208, 230, 160), 0.85)
        tropic_pen.setStyle(Qt.PenStyle.DashLine)

        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Parallels (Latitude lines every 15°, plus Equator and Tropics ±23.5°)
        lat_list = [
            (-75, grid_pen),
            (-60, grid_pen),
            (-45, grid_pen),
            (-30, grid_pen),
            (-23.5, tropic_pen),  # Tropic of Capricorn
            (-15, grid_pen),
            (0, equator_pen),      # Equator
            (15, grid_pen),
            (23.5, tropic_pen),   # Tropic of Cancer
            (30, grid_pen),
            (45, grid_pen),
            (60, grid_pen),
            (75, grid_pen),
        ]

        for lat_deg, pen in lat_list:
            painter.setPen(pen)
            path = QPainterPath()
            first = True
            for lon_deg in range(-180, 181, 6):
                x, y, z = self._project_lat_lon(float(lat_deg), float(lon_deg), r)
                if z > 0:
                    pt = QPointF(cx + x, cy + y)
                    if first:
                        path.moveTo(pt)
                        first = False
                    else:
                        path.lineTo(pt)
                else:
                    first = True
            if not path.isEmpty():
                painter.drawPath(path)

        # Meridians (Longitude lines every 30°, highlighted Prime Meridian)
        for lon_deg in range(-180, 180, 30):
            painter.setPen(equator_pen if lon_deg == 0 else grid_pen)
            path = QPainterPath()
            first = True
            for lat_deg in range(-90, 91, 6):
                x, y, z = self._project_lat_lon(float(lat_deg), float(lon_deg), r)
                if z > 0:
                    pt = QPointF(cx + x, cy + y)
                    if first:
                        path.moveTo(pt)
                        first = False
                    else:
                        path.lineTo(pt)
                else:
                    first = True
            if not path.isEmpty():
                painter.drawPath(path)

    def _draw_continents(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Render high-fidelity periwinkle blue continents with spherical horizon clamping."""
        land_pen = QPen(QColor(92, 110, 192), 1.0)
        land_brush = QBrush(QColor(107, 126, 206))  # #6B7ECE

        painter.setPen(land_pen)
        painter.setBrush(land_brush)

        for poly in _PRECOMPUTED_CONTINENTS.values():
            if len(poly) < 3:
                continue

            path = QPainterPath()
            first = True
            any_front = False

            for cos_lat, sin_lat, lon_deg in poly:
                lon = math.radians(lon_deg + self.yaw_deg)
                x0 = cos_lat * math.sin(lon)
                y0 = -sin_lat
                z0 = cos_lat * math.cos(lon)

                x1 = x0
                y1 = y0 * self._cos_pitch - z0 * self._sin_pitch
                z1 = y0 * self._sin_pitch + z0 * self._cos_pitch

                x = (x1 * self._cos_tilt - y1 * self._sin_tilt) * r
                y = (x1 * self._sin_tilt + y1 * self._cos_tilt) * r
                z = z1 * r

                if z > 0:
                    any_front = True
                    pt = QPointF(cx + x, cy + y)
                else:
                    # Project points on back hemisphere outward to circular rim
                    d = math.hypot(x, y)
                    if d > 1e-4:
                        scale = (r * 1.02) / d
                        pt = QPointF(cx + x * scale, cy + y * scale)
                    else:
                        pt = QPointF(cx + r, cy)

                if first:
                    path.moveTo(pt)
                    first = False
                else:
                    path.lineTo(pt)

            path.closeSubpath()
            if any_front:
                painter.drawPath(path)

    def _draw_great_lakes(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Render prominent sky-blue Great Lakes highlight in North America."""
        lake_pen = QPen(QColor(96, 165, 250), 1.0)
        lake_brush = QBrush(QColor(126, 182, 255))  # #7EB6FF

        painter.setPen(lake_pen)
        painter.setBrush(lake_brush)

        front_path = QPainterPath()
        has_points = False

        for cos_lat, sin_lat, lon_deg in _PRECOMPUTED_GREAT_LAKES:
            lon = math.radians(lon_deg + self.yaw_deg)
            x0 = cos_lat * math.sin(lon)
            y0 = -sin_lat
            z0 = cos_lat * math.cos(lon)

            x1 = x0
            y1 = y0 * self._cos_pitch - z0 * self._sin_pitch
            z1 = y0 * self._sin_pitch + z0 * self._cos_pitch

            x = (x1 * self._cos_tilt - y1 * self._sin_tilt) * r
            y = (x1 * self._sin_tilt + y1 * self._cos_tilt) * r
            z = z1 * r

            if z > 0:
                pt = QPointF(cx + x, cy + y)
                if not has_points:
                    front_path.moveTo(pt)
                    has_points = True
                else:
                    front_path.lineTo(pt)

        if has_points:
            front_path.closeSubpath()
            painter.drawPath(front_path)

    def _draw_sphere_edge_shadow(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Volumetric subtle spherical horizon edge shadow."""
        edge_grad = QRadialGradient(QPointF(cx, cy), r)
        edge_grad.setColorAt(0.80, QColor(15, 23, 42, 0))
        edge_grad.setColorAt(0.95, QColor(15, 23, 42, 14))
        edge_grad.setColorAt(1.0, QColor(15, 23, 42, 38))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(edge_grad))
        painter.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_spherical_rim(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Draw crisp subtle boundary ring around the sphere."""
        rim_pen = QPen(QColor(190, 202, 222), 1.1)
        painter.setPen(rim_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), r - 0.5, r - 0.5)

    def _draw_network_arcs(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Render 3D curved network connection arcs with true 3D occlusion."""
        marker_map = {m["id"]: m for m in _PING_MARKERS}

        for id1, id2 in _NETWORK_ARCS:
            m1 = marker_map.get(id1)
            m2 = marker_map.get(id2)
            if not m1 or not m2:
                continue

            # Convert to 3D unit vectors
            lat1, lon1 = math.radians(m1["lat"]), math.radians(m1["lon"] + self.yaw_deg)
            lat2, lon2 = math.radians(m2["lat"]), math.radians(m2["lon"] + self.yaw_deg)

            v1 = (math.cos(lat1) * math.sin(lon1), -math.sin(lat1), math.cos(lat1) * math.cos(lon1))
            v2 = (math.cos(lat2) * math.sin(lon2), -math.sin(lat2), math.cos(lat2) * math.cos(lon2))

            dot = max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]))
            omega = math.acos(dot)
            if omega < 1e-4:
                continue

            # Both endpoints must be in the front hemisphere so arcs never float or disconnect
            _, _, z1_proj = self._project_point_3d(v1[0], v1[1], v1[2], r)
            _, _, z2_proj = self._project_point_3d(v2[0], v2[1], v2[2], r)
            if z1_proj <= 0 or z2_proj <= 0:
                continue

            # Arc opacity fades gracefully if either node approaches the horizon
            fade_1 = min(1.0, z1_proj / (0.12 * r))
            fade_2 = min(1.0, z2_proj / (0.12 * r))
            arc_alpha_factor = min(fade_1, fade_2)
            if arc_alpha_factor < 0.05:
                continue

            sin_omega = math.sin(omega)
            steps = 36
            arc_pts: List[Tuple[float, float, float, bool, float]] = []

            # Elevation scaled gracefully by angular distance
            max_elevation = min(0.18, 0.08 + 0.10 * math.sin(omega / 2.0))

            for s in range(steps + 1):
                t = s / steps
                w1 = math.sin((1.0 - t) * omega) / sin_omega
                w2 = math.sin(t * omega) / sin_omega
                vx = w1 * v1[0] + w2 * v2[0]
                vy = w1 * v1[1] + w2 * v2[1]
                vz = w1 * v1[2] + w2 * v2[2]
                vlen = math.hypot(vx, vy, vz)
                if vlen > 1e-6:
                    vx /= vlen
                    vy /= vlen
                    vz /= vlen

                arc_alt = 1.0 + max_elevation * math.sin(math.pi * t)
                rad = r * arc_alt

                x, y, z = self._project_point_3d(vx, vy, vz, rad)

                # Strict Front-Hemisphere 3D Depth Occlusion
                is_visible = False
                horizon_fade = 0.0

                if z > 0:
                    d_sq = x * x + y * y
                    if d_sq <= r * r:
                        z_sphere_front = math.sqrt(max(0.0, r * r - d_sq))
                        if z >= z_sphere_front - 1.0:
                            is_visible = True
                            horizon_fade = min(1.0, z / (0.10 * r))
                    else:
                        is_visible = True
                        horizon_fade = min(1.0, z / (0.10 * r))

                arc_pts.append((x, y, z, is_visible, horizon_fade))

            # Dotted terracotta / coral-red arc stroke matching Image 2
            pen_alpha = int(230 * arc_alpha_factor)
            arc_pen = QPen(QColor(218, 110, 88, pen_alpha), 1.5)
            arc_pen.setStyle(Qt.PenStyle.CustomDashLine)
            arc_pen.setDashPattern([1.5, 3.5])
            painter.setPen(arc_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            first = True
            for x, y, z, vis, fade in arc_pts:
                if vis and fade > 0.05:
                    pt = QPointF(cx + x, cy + y)
                    if first:
                        path.moveTo(pt)
                        first = False
                    else:
                        path.lineTo(pt)
                else:
                    first = True

            if not path.isEmpty():
                painter.drawPath(path)

            # Animated telemetry packet traveling along the arc
            packet_idx = int(self.arc_pulse_t * steps)
            if 0 <= packet_idx < len(arc_pts):
                px, py, pz, pvis, pfade = arc_pts[packet_idx]
                if pvis and pfade > 0.1:
                    alpha = int(240 * pfade * arc_alpha_factor)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(218, 110, 88, alpha)))
                    painter.drawEllipse(QPointF(cx + px, cy + py), 2.5, 2.5)

    def _draw_ping_markers(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Render concentric target markers with smooth horizon fade."""
        for m in _PING_MARKERS:
            x, y, z = self._project_lat_lon(m["lat"], m["lon"], r)
            if z <= 0:
                continue  # Culled by back hemisphere

            # Smooth fade near glancing horizon
            fade = min(1.0, z / (0.12 * r))
            alpha_outer = int(255 * fade)
            if alpha_outer < 15:
                continue

            sx = cx + x
            sy = cy + y
            is_hovered = (m["id"] == self.hovered_marker_id)

            if is_hovered:
                # Pulsing hover ring
                pulse_rad = 9.0 + 3.0 * math.sin(self.pulse_phase)
                painter.setPen(QPen(QColor(37, 99, 235, int(120 * fade)), 1.5))
                painter.setBrush(QBrush(QColor(37, 99, 235, int(25 * fade))))
                painter.drawEllipse(QPointF(sx, sy), pulse_rad, pulse_rad)

            # Concentric Target Design from Reference Image 2:
            # 1. Outer Ring: Thin black circle with white center fill
            outer_pen = QPen(QColor(24, 24, 27, alpha_outer), 1.8)
            painter.setPen(outer_pen)
            painter.setBrush(QBrush(QColor(255, 255, 255, int(255 * fade))))
            painter.drawEllipse(QPointF(sx, sy), 6.5, 6.5)

            # 2. Inner Dot: Solid black center bullseye
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(24, 24, 27, alpha_outer)))
            painter.drawEllipse(QPointF(sx, sy), 2.5, 2.5)

    def _draw_particles(
        self, painter: QPainter, cx: float, cy: float, r: float, foreground: bool
    ) -> None:
        """Render subtle floating orbital particles in two passes (background vs foreground)."""
        painter.setPen(Qt.PenStyle.NoPen)

        for p in self.particles:
            # Orbital motion: slow drift and vertical oscillation
            cur_lon = p.base_lon + p.drift_lon_speed * self.anim_time
            cur_lat = p.base_lat + p.osc_amp * math.sin(p.osc_rate * self.anim_time)
            rad = r * p.orbit_ratio

            x, y, z = self._project_lat_lon(cur_lat, cur_lon, rad)

            d_sq = x * x + y * y
            inside_disk = (d_sq <= r * r)

            # Depth occlusion test
            if inside_disk:
                z_sphere_front = math.sqrt(max(0.0, r * r - d_sq))
                is_front = (z >= z_sphere_front)
            else:
                is_front = (z >= 0.0)

            # Filter by pass
            if foreground != is_front:
                continue

            # Occlude background particles hidden behind the sphere disk
            if not foreground and inside_disk:
                continue

            sx = cx + x
            sy = cy + y

            # Gentle breathing opacity
            pulse = 0.85 + 0.15 * math.sin(self.pulse_phase + p.pulse_phase)
            alpha_factor = p.base_alpha * pulse

            if not foreground:
                alpha_factor *= 0.45  # Background particles are softer and more distant

            final_alpha = int(255 * alpha_factor)
            if final_alpha < 10:
                continue

            cr, cg, cb = p.color

            if foreground:
                # Soft glowing halo
                glow_grad = QRadialGradient(QPointF(sx, sy), p.size * 2.2)
                glow_grad.setColorAt(0.0, QColor(cr, cg, cb, int(final_alpha * 0.9)))
                glow_grad.setColorAt(0.5, QColor(cr, cg, cb, int(final_alpha * 0.35)))
                glow_grad.setColorAt(1.0, QColor(cr, cg, cb, 0))
                painter.setBrush(QBrush(glow_grad))
                painter.drawEllipse(QPointF(sx, sy), p.size * 2.2, p.size * 2.2)

            # Crisp particle core
            painter.setBrush(QBrush(QColor(cr, cg, cb, final_alpha)))
            painter.drawEllipse(QPointF(sx, sy), p.size * 0.55, p.size * 0.55)

    def _draw_hover_tooltip(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Draw sleek tooltip when hovering over a node."""
        if not self.hovered_marker_id:
            return

        marker = next((m for m in _PING_MARKERS if m["id"] == self.hovered_marker_id), None)
        if not marker:
            return

        x, y, z = self._project_lat_lon(marker["lat"], marker["lon"], r)
        if z <= 0:
            return

        sx = cx + x
        sy = cy + y

        text = f"{marker['name']} • {marker['latency_ms']} ms"
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text) + 16
        text_h = 24

        bx = sx - text_w * 0.5
        by = sy - 28

        # Draw tooltip pill
        painter.setPen(QPen(QColor(226, 232, 240), 1.0))
        painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
        painter.drawRoundedRect(QRectF(bx, by, text_w, text_h), 12.0, 12.0)

        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QRectF(bx, by, text_w, text_h),
            int(Qt.AlignmentFlag.AlignCenter),
            text,
        )

    # -----------------------------------------------------------------------
    # Lifecycle & Cleanup
    # -----------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """Stop frame loop and safely free resources."""
        if self._timer.isActive():
            self._timer.stop()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        """Pause animation frame loop when hidden to conserve CPU/power."""
        if self._timer.isActive():
            self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        """Resume animation frame loop when shown."""
        if not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)

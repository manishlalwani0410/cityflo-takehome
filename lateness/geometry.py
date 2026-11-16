"""Route geometry: local equirectangular projection and snapping a fix onto the ordered stop polyline.

The polyline runs through stops, not a map-matched road centreline (per the analyst note), so sub-100 m
precision is not meaningful. Every threshold that uses distance is set well above that.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON_EQ = 111_320.0


@dataclass(frozen=True)
class RouteLine:
    route_id: int
    lat0: float
    pts: tuple          # ((x, y), ...) in metres, stop order
    cum: tuple          # cumulative metres at each stop
    stop_ids: tuple

    @property
    def length_m(self) -> float:
        return self.cum[-1]

    def xy(self, lat: float, lon: float):
        return lon * M_PER_DEG_LON_EQ * math.cos(math.radians(self.lat0)), lat * M_PER_DEG_LAT

    def project(self, lat: float, lon: float):
        """Return (along_m, off_route_m) for the nearest point on the polyline."""
        px, py = self.xy(lat, lon)
        best = (math.inf, 0.0)
        for i in range(1, len(self.pts)):
            (x1, y1), (x2, y2) = self.pts[i - 1], self.pts[i]
            dx, dy = x2 - x1, y2 - y1
            seg2 = dx * dx + dy * dy
            u = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg2))
            off = math.hypot(px - (x1 + u * dx), py - (y1 + u * dy))
            if off < best[0]:
                best = (off, self.cum[i - 1] + u * math.sqrt(seg2))
        return best[1], best[0]


def build(route_id: int, stops) -> RouteLine:
    lat0 = sum(s.lat for s in stops) / len(stops)
    line = RouteLine(route_id, lat0, (), (), ())
    pts = tuple(line.xy(s.lat, s.lon) for s in stops)
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + math.dist(pts[i - 1], pts[i]))
    return RouteLine(route_id, lat0, pts, tuple(cum), tuple(s.stop_id for s in stops))


def distance_m(lat1, lon1, lat2, lon2) -> float:
    lat0 = (lat1 + lat2) / 2
    return math.hypot((lon2 - lon1) * M_PER_DEG_LON_EQ * math.cos(math.radians(lat0)), (lat2 - lat1) * M_PER_DEG_LAT)

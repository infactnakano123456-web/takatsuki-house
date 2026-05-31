"""Geocoding and distance utilities using 国土地理院 API."""

import json
import logging
import math
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)

# JR高槻駅の座標
JR_TAKATSUKI_LAT = 34.84966
JR_TAKATSUKI_LON = 135.61733

# 徒歩速度: 80m/分
WALK_SPEED_M_PER_MIN = 80


def geocode(address: str) -> Optional[tuple[float, float]]:
    """
    国土地理院 住所検索APIで住所を座標に変換。
    Returns (lat, lon) or None on failure.
    """
    # 「大阪府高槻市」以降だけにする（ページ内注釈を除去）
    clean = address.split("[")[0].split("（")[0].strip()
    query = urllib.parse.urlencode({"q": clean})
    url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?{query}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "takatsuki-house-scraper/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data:
            coords = data[0]["geometry"]["coordinates"]  # [lon, lat]
            return float(coords[1]), float(coords[0])
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as e:
        logger.debug(f"Geocoding failed for '{address}': {e}")
    return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """直線距離をメートルで返す（Haversine公式）。"""
    R = 6_371_000  # 地球半径 (m)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_from_jr_takatsuki_m(lat: float, lon: float) -> float:
    return haversine_m(JR_TAKATSUKI_LAT, JR_TAKATSUKI_LON, lat, lon)


def estimated_walk_min_from_jr(lat: float, lon: float) -> int:
    """JR高槻駅からの直線距離を徒歩分数（80m/分）に換算。高槻市街地は道路がほぼ直線的なため係数1.1を使用。"""
    dist_m = distance_from_jr_takatsuki_m(lat, lon)
    # 直線 → 実際の道のり係数 1.1（市街地・格子状道路）
    return math.ceil(dist_m * 1.1 / WALK_SPEED_M_PER_MIN)

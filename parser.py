"""Parse raw text fields from scraped property data."""

import re
from typing import Optional


def parse_price(text: str) -> Optional[int]:
    """
    '5,280万円' -> 5280
    '1億3160万円' -> 13160
    '2億円' -> 20000
    """
    if not text:
        return None
    text = text.replace(",", "").replace("，", "")
    # 億＋万 例: 1億3160万円
    m = re.search(r"(\d+)\s*億\s*(\d+)\s*万円", text)
    if m:
        return int(m.group(1)) * 10000 + int(m.group(2))
    # 億のみ 例: 2億円
    m = re.search(r"(\d+)\s*億円", text)
    if m:
        return int(m.group(1)) * 10000
    # 万のみ 例: 5280万円
    m = re.search(r"(\d+(?:\.\d+)?)\s*万円", text)
    if m:
        return int(float(m.group(1)))
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def parse_walk_minutes(text: str) -> Optional[int]:
    """
    '「高槻」歩8分' -> 8  /  'バス7分山手町歩6分' -> None（バス利用は除外）
    複数路線ある場合は最小値（純徒歩ルートのみ対象）。
    """
    if not text:
        return None

    # 路線ごとに分割（「[ 乗り換え案内 ]」区切り、改行、連続スペース）
    segments = re.split(r"\[[\s　]*乗り換え案内[\s　]*\]|\n|　{2,}|\s{3,}", text)

    min_walk = None
    for seg in segments:
        # バスが絡むセグメントは徒歩扱いしない
        if "バス" in seg:
            continue
        m = re.search(r"(?:徒歩|歩)\s*(\d+)\s*分", seg)
        if m:
            w = int(m.group(1))
            if min_walk is None or w < min_walk:
                min_walk = w

    return min_walk


def parse_area_m2(text: str) -> Optional[float]:
    """'105.23m²' / '105.23㎡' / '105.23m 2'（SUUMOのsup変換）-> 105.23"""
    if not text:
        return None
    # m<sup>2</sup>がテキスト化されると "m 2" になる
    text = re.sub(r"m\s*2\b", "㎡", text)
    text = text.replace("m²", "㎡")
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*㎡", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def m2_to_tsubo(m2: Optional[float]) -> Optional[float]:
    if m2 is None:
        return None
    return round(m2 / 3.30579, 2)


def parse_floor_plan(text: str) -> Optional[str]:
    """Extract floor plan like '4LDK', '3SLDK', '2DK' from arbitrary text."""
    if not text:
        return None
    m = re.search(r"\d+(?:S?LDK|DK|LK|K|SDK|SLDK)", text, re.IGNORECASE)
    return m.group(0).upper() if m else text.strip()


def parse_floors(text: str) -> Optional[str]:
    """'地上2階建' or '2階建' -> '2階建'"""
    if not text:
        return None
    m = re.search(r"(?:地上)?(\d+)階建(?:て)?", text)
    return f"{m.group(1)}階建" if m else text.strip()


def parse_built_year_month(text: str) -> Optional[str]:
    """'2003年3月' or '2003年03月' -> '2003年3月'"""
    if not text:
        return None
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
    if m:
        return f"{m.group(1)}年{m.group(2)}月"
    # 和暦対応
    wareki = {"令和": 2018, "平成": 1988, "昭和": 1925}
    for name, base in wareki.items():
        m = re.search(rf"{name}\s*(\d+)\s*年\s*(\d{{1,2}})\s*月", text)
        if m:
            year = base + int(m.group(1))
            return f"{year}年{m.group(2)}月"
    return text.strip()


def extract_reform_info(texts: list[str]) -> Optional[str]:
    """Search a list of text snippets for reform-related mentions."""
    keywords = re.compile(
        r"リフォーム|リノベ|改装|改築|修繕|補修|耐震|外壁塗装|屋根|給湯器|キッチン交換|浴室交換",
        re.IGNORECASE
    )
    found = []
    for t in texts:
        if t and keywords.search(t):
            # normalize whitespace
            clean = re.sub(r"\s+", " ", t).strip()
            if clean not in found:
                found.append(clean)
    return " / ".join(found) if found else None


def parse_road_info(text: str) -> Optional[str]:
    """Extract road-facing info like '南西 幅員4.0m'."""
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip()


def compute_tsubo_price(price_man: Optional[int], land_tsubo: Optional[float]) -> Optional[float]:
    if price_man and land_tsubo and land_tsubo > 0:
        return round(price_man / land_tsubo, 1)
    return None

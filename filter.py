"""Filter and score properties against user preferences."""

import math
import re
from parser import compute_tsubo_price

# 安満遺跡公園の座標
AMAN_LAT = 34.8562
AMAN_LON = 135.6228


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def score_property(prop: dict, cfg: dict) -> dict:
    """
    新スコア設計（100点満点）:
      - 駅距離    最大40点: 近いほど高い
      - 建物広さ  最大25点: 75㎡以上から加点
      - 予算内    15点
      - 相場比    最大15点: 町ごとの坪単価比較（APIキーあり時）
      - 安満公園  最大5点:  600m以内
    """
    budget = cfg["budget"]
    negative_words = cfg.get("negative_words", [])
    max_dist_m = cfg["search"].get("max_dist_jr_m", 1560)

    score = 0
    reasons = []
    is_excluded = False
    geo_excluded = False
    exclude_reason = ""

    searchable = " ".join(filter(None, [
        prop.get("name", ""),
        prop.get("address", ""),
        prop.get("reform_info", ""),
    ]))

    # ─── ジオ除外 ───
    dist_jr_m = prop.get("dist_jr_m")
    # walk_minutes = SUUMO公式掲載の徒歩分数（最優先）
    # est_walk_jr_min = 座標から計算した推定値（フォールバック）
    est_walk_jr = prop.get("walk_minutes") or prop.get("est_walk_jr_min")
    if dist_jr_m is not None and dist_jr_m > max_dist_m:
        geo_excluded = True
        is_excluded = True
        exclude_reason = f"JR高槻駅から直線{int(dist_jr_m)}m（推定徒歩{est_walk_jr}分）—圏外"

    # ─── キーワード除外 ───
    if not is_excluded:
        for neg in negative_words:
            if neg in searchable:
                is_excluded = True
                exclude_reason = f"除外ワード一致: 「{neg}」"
                break

    if is_excluded:
        tsubo_price = compute_tsubo_price(prop.get("price_man"), prop.get("land_area_tsubo"))
        return {
            "match_score": 0,
            "match_reason": [exclude_reason],
            "is_excluded": True,
            "geo_excluded": geo_excluded,
            "exclude_reason": exclude_reason,
            "recommendation": "✕",
            "tsubo_price": tsubo_price,
        }

    # ─── 1. 駅距離（最大30点）───
    # SUUMO記載の正確な徒歩分数を最優先で評価
    if est_walk_jr is not None:
        if est_walk_jr <= 7:
            pts = 40
        elif est_walk_jr <= 10:
            pts = 35
        elif est_walk_jr <= 12:
            pts = 20
        elif est_walk_jr <= 15:
            pts = 10
        elif est_walk_jr <= 18:
            pts = -5
        else:
            pts = -10
        score += pts
        reasons.append(f"JR高槻駅: SUUMO記載徒歩{est_walk_jr}分 → {'+' if pts >= 0 else ''}{pts}点")
    # SUUMOの徒歩分数が取れなかった場合のみ、直線距離（係数1.1）で代替
    elif dist_jr_m is not None:
        calc_walk = round(dist_jr_m * 1.1 / 80)
        if dist_jr_m <= 600:
            pts = 40
        elif dist_jr_m <= 800:
            pts = 35
        elif dist_jr_m <= 1000:
            pts = 20
        elif dist_jr_m <= 1250:
            pts = 10
        elif dist_jr_m <= 1500:
            pts = -5
        else:
            pts = -10
        score += pts
        reasons.append(f"JR高槻駅: 直線{int(dist_jr_m)}m・推定徒歩{calc_walk}分 → {'+' if pts >= 0 else ''}{pts}点")

    # ─── 2. 土地のみペナルティ ───
    building_m2 = prop.get("building_area_m2")
    prop_type = prop.get("property_type") or ""
    is_land_only = not building_m2  # 建物面積が取れない = 土地のみとみなす
    if is_land_only:
        score += -15
        reasons.append("土地のみ・建物面積不明 → -15点")

    # ─── 3. 建物面積（最大15点）───
    if building_m2:
        if building_m2 >= 90:
            pts = 15
        elif building_m2 >= 80:
            pts = 13
        elif building_m2 >= 70:
            pts = 8
        elif building_m2 >= 60:
            pts = 5
        else:
            pts = 0
        score += pts
        reasons.append(f"建物面積: {building_m2:.1f}㎡ → +{pts}点")
    else:
        fp = prop.get("floor_plan") or ""
        if "4LDK" in fp or "5LDK" in fp:
            score += 10
            reasons.append(f"間取り: {fp}（面積不明） → +10点")
        elif "3LDK" in fp or "3SLDK" in fp:
            score += 5
            reasons.append(f"間取り: {fp}（面積不明） → +5点")

    # ─── 3. 土地面積（最大20点）───
    land_m2 = prop.get("land_area_m2")
    if land_m2:
        if land_m2 >= 90:
            pts = 20
        elif land_m2 >= 80:
            pts = 15
        elif land_m2 >= 70:
            pts = 12
        elif land_m2 >= 60:
            pts = 5
        else:
            pts = 0
        score += pts
        reasons.append(f"土地面積: {land_m2:.1f}㎡ → +{pts}点")

    # ─── 4. 坪単価 vs 相場（±点）───
    price = prop.get("price_man")
    tsubo_price = compute_tsubo_price(price, prop.get("land_area_tsubo"))
    market_tsubo = prop.get("market_tsubo_median")  # reinfolib連動（APIキー後）
    if tsubo_price and market_tsubo:
        ratio = tsubo_price / market_tsubo
        if ratio <= 0.90:
            pts = 15
            label = f"相場より{int((1-ratio)*100)}%安い → +{pts}点"
        elif ratio <= 1.00:
            pts = 0
            label = "相場並み → +0点"
        elif ratio <= 1.15:
            pts = -10
            label = f"相場より{int((ratio-1)*100)}%高め → {pts}点"
        elif ratio <= 1.25:
            pts = -20
            label = f"相場より{int((ratio-1)*100)}%割高 → {pts}点"
        else:
            pts = -30
            label = f"相場より{int((ratio-1)*100)}%割高 → {pts}点"
        score += pts
        reasons.append(f"坪単価: {tsubo_price:.1f}万円/坪（相場{market_tsubo:.1f}万円/坪、{label}）")
    elif tsubo_price:
        reasons.append(f"坪単価: {tsubo_price:.1f}万円/坪（相場データなし）")

    # ─── 5. 築年数（最大10点、土地のみは0点）───
    built = prop.get("built_year_month") or ""
    building_m2_check = prop.get("building_area_m2")
    built_year_match = re.search(r"(\d{4})年", built) if built else None
    if built_year_match and building_m2_check:
        built_year = int(built_year_match.group(1))
        if built_year >= 2025:
            pts = 15
        elif built_year >= 2020:
            pts = 12
        elif built_year >= 2015:
            pts = 10
        elif built_year >= 2010:
            pts = 7
        elif built_year >= 2000:
            pts = 5
        elif built_year >= 1981:
            pts = 0
        else:
            pts = -15  # 旧耐震
        score += pts
        label = "（旧耐震基準）" if built_year < 1981 else ""
        reasons.append(f"築年: {built_year}年{label} → {'+' if pts >= 0 else ''}{pts}点")
    elif not building_m2_check:
        reasons.append("土地のみ（築年スコアなし）")

    # ─── 6. 接道・道路幅（+5 〜 -15点）───
    road_searchable = " ".join(filter(None, [
        prop.get("name", ""),
        prop.get("address", ""),
        prop.get("reform_info", ""),
        prop.get("description", ""),
    ]))
    road_neg = ["セットバック", "4m未満", "私道負担"]
    road_pos = ["前面道路6m以上", "公道6m"]
    if any(w in road_searchable for w in road_neg):
        matched = [w for w in road_neg if w in road_searchable]
        score += -15
        reasons.append(f"接道: {'・'.join(matched)} → -15点")
    elif any(w in road_searchable for w in road_pos):
        matched = [w for w in road_pos if w in road_searchable]
        score += 5
        reasons.append(f"接道: {'・'.join(matched)} → +5点")

    # ─── 7. リフォームの質（スクレイピング対応後に有効化予定）───
    # TODO: reform_info フィールド取得後に有効化
    # reform_full = ["水回り新調", "水回り交換", "外壁塗装", "フルリフォーム"]
    # reform_partial = ["クロス張替", "畳替え"]
    pass

    # ─── 8. 周辺環境（+10 〜 -15点）───
    env_pos = ["スーパー徒歩", "ライフ徒歩", "万代徒歩", "アルプラ徒歩"]
    env_neg = ["坂あり", "傾斜"]
    if any(w in road_searchable for w in env_neg):
        matched = [w for w in env_neg if w in road_searchable]
        score += -15
        reasons.append(f"周辺環境: {'・'.join(matched)} → -15点")
    elif any(w in road_searchable for w in env_pos):
        matched = [w for w in env_pos if w in road_searchable]
        score += 10
        reasons.append(f"周辺環境: {'・'.join(matched)}徒歩5分以内 → +10点")

    # ─── 9. 安満遺跡公園（最大10点）───
    lat = prop.get("lat")
    lon = prop.get("lon")
    if lat and lon:
        dist_aman = _haversine_m(lat, lon, AMAN_LAT, AMAN_LON)
        if dist_aman <= 300:
            score += 15
            reasons.append(f"安満遺跡公園: {int(dist_aman)}m圏内 → +15点")
        elif dist_aman <= 600:
            score += 10
            reasons.append(f"安満遺跡公園: {int(dist_aman)}m → +10点")
        elif dist_aman <= 1000:
            score += 5
            reasons.append(f"安満遺跡公園: {int(dist_aman)}m → +5点")

    score = max(0, min(100, score))

    if score >= 70:
        recommendation = "◎"
    elif score >= 50:
        recommendation = "○"
    elif score >= 30:
        recommendation = "△"
    else:
        recommendation = "✕"

    return {
        "match_score": score,
        "match_reason": reasons,
        "is_excluded": False,
        "geo_excluded": False,
        "exclude_reason": "",
        "recommendation": recommendation,
        "tsubo_price": tsubo_price,
    }

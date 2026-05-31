#!/usr/bin/env python3
"""Generate properties_list.html and market.html from the SQLite database."""

import sqlite3
import json
import re
from collections import defaultdict
from pathlib import Path

from filter import score_property

DB_PATH = Path(__file__).parent / "properties.db"
LIST_PATH = Path(__file__).parent / "properties_list.html"
MARKET_PATH = Path(__file__).parent / "market.html"
CONFIG_PATH = Path(__file__).parent / "config.json"


def reinfolib_map_url(lat, lon):
    if lat and lon:
        return f"https://www.reinfolib.mlit.go.jp/?z=16&lat={lat}&lng={lon}"
    return "https://www.reinfolib.mlit.go.jp/"


def extract_district(address: str) -> str:
    if not address:
        return ""
    m = re.search(r"高槻市(.+?)(?:\d|$)", address)
    if m:
        district = re.sub(r"[\d－\-]+.*", "", m.group(1)).strip()
        return district if district else ""
    return ""


def district_anchor(district: str) -> str:
    return re.sub(r"\s+", "_", district)


def generate(cfg: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM properties WHERE is_active=1 ORDER BY price_man ASC NULLS LAST"
    ).fetchall()
    conn.close()

    props = [dict(r) for r in rows]

    # reinfolib 実勢成約データ（国交省）を読み込む
    REINFOLIB_PATH = Path(__file__).parent / "reinfolib_data.json"
    reinfolib_data = {}
    if REINFOLIB_PATH.exists():
        with open(REINFOLIB_PATH, encoding="utf-8") as f:
            reinfolib_data = json.load(f)
    # district_median: 地区名 → 坪単価中央値（万円/坪）
    district_median = {d: v["median_tsubo"] for d, v in reinfolib_data.items()}

    # market_tsubo_median を各物件に付与
    for p in props:
        d = extract_district(p.get("address", ""))
        p["market_tsubo_median"] = district_median.get(d)

    district_props = defaultdict(list)
    scored = []
    for p in props:
        sc = score_property(p, cfg)
        scored.append((p, sc))
        d = extract_district(p.get("address", ""))
        if d:
            district_props[d].append((p, sc))

    scored.sort(key=lambda x: (x[1]["is_excluded"], -x[1]["match_score"]))

    # ── 物件一覧行 ──
    rows_html = []
    for p, sc in scored:
        rec = sc["recommendation"]
        score = sc["match_score"]
        excl = sc["is_excluded"]

        price = f"{p['price_man']}万円" if p["price_man"] else "—"
        tsubo_val = sc.get("tsubo_price")
        tsubo_price = f"{tsubo_val:.1f}万円/坪" if tsubo_val else "—"
        walk_suumo = f"{p['walk_minutes']}分" if p["walk_minutes"] else "—"
        # 表示：SUUMO公式があればそれを主表示、計算値はサブ表示
        if p["walk_minutes"]:
            walk_jr = f"SUUMO:{p['walk_minutes']}分"
            walk_sub = f"推定:{p['est_walk_jr_min']}分" if p["est_walk_jr_min"] else ""
        elif p["est_walk_jr_min"]:
            walk_jr = f"推定:{p['est_walk_jr_min']}分"
            walk_sub = ""
        else:
            walk_jr = "—"
            walk_sub = ""
        dist_jr = f"{p['dist_jr_m']:.0f}m" if p["dist_jr_m"] else "—"
        land = f"{p['land_area_m2']:.1f}㎡" if p["land_area_m2"] else "—"
        land_tsubo = f"({p['land_area_tsubo']:.1f}坪)" if p["land_area_tsubo"] else ""
        building = f"{p['building_area_m2']:.1f}㎡" if p["building_area_m2"] else "—"
        floor_plan = p["floor_plan"] or "—"
        built = p["built_year_month"] or "—"
        address = p["address"] or "—"
        prop_type = p.get("property_type") or ""

        district = extract_district(address)
        # 住所セル：リンクなし（町名のみ表示）
        district_cell = district if district else address

        # 坪単価 ± 相場比（%表示・色付き）
        market_tsubo = p.get("market_tsubo_median")
        anchor = district_anchor(district) if district else ""
        if market_tsubo and tsubo_val:
            ratio = tsubo_val / market_tsubo
            pct = int((ratio - 1) * 100)
            pct_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
            pct_color = ("#1a8a1a" if ratio <= 0.90 else
                         "#555" if ratio <= 1.00 else
                         "#b86000" if ratio <= 1.15 else
                         "#c00")
            tsubo_cell = f'{tsubo_val:.1f}万円/坪 <span style="color:{pct_color};font-size:11px">({pct_str})</span>'
        else:
            tsubo_cell = tsubo_price

        # 相場坪単価セル（色なし・成約実績リンクのみ）
        if market_tsubo:
            market_cell = f'<a href="market.html#dist-{anchor}" target="_blank" style="color:#1a6bd1" title="町の成約実績">{market_tsubo:.1f}万円/坪</a>'
        else:
            market_cell = (
                f'<a href="market.html#dist-{anchor}" target="_blank" style="color:#bbb;font-size:11px">成約実績↓</a>'
                if anchor else '<span style="color:#bbb">—</span>'
            )

        row_class = ("excluded" if excl else
                     "great" if score >= 70 else
                     "good" if score >= 50 else "")

        import json as _json
        tooltip_lines = sc["match_reason"]
        tooltip_json = _json.dumps(tooltip_lines, ensure_ascii=False).replace("'", "&#39;")

        # data属性でフィルタ用
        rows_html.append(f"""
    <tr class="{row_class}" data-score="{score}" data-excl="{'1' if excl else '0'}"
        data-address="{address}" data-district="{district}" data-type="{prop_type}"
        data-price="{p['price_man'] or ''}" data-floor="{floor_plan}">
      <td class="rec">{rec}</td>
      <td class="score" data-reasons='{tooltip_json}' onclick="showScoreCard(this)">{score}</td>
      <td class="price">{price}</td>
      <td class="tsubo">{tsubo_cell}</td>
      <td class="tsubo">{market_cell}</td>
      <td>{walk_jr}<br><small style="color:#999">{walk_sub}</small></td>
      <td>{dist_jr}</td>
      <td>{floor_plan}</td>
      <td>{land}{land_tsubo}<br><small>{building}</small></td>
      <td>{built}</td>
      <td class="addr">{district_cell}<br><small>{address}</small></td>
      <td><a href="{p['url']}" target="_blank">物件ページ</a></td>
    </tr>""")

    list_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>JR高槻駅周辺 物件リスト</title>
<style>
body{{font-family:sans-serif;font-size:13px;margin:20px}}
h1{{font-size:18px;margin-bottom:8px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:4px 8px;vertical-align:top}}
th{{background:#f0f0f0;position:sticky;top:0;z-index:1}}
tr.great{{background:#e8f8e8}}
tr.good{{background:#f8f8e8}}
tr.excluded{{background:#f4f4f4;color:#999}}
td.rec{{font-size:16px;text-align:center}}
td.score{{text-align:right;cursor:pointer;text-decoration:underline dotted #999}}
#score-card{{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
  background:#fff;border:1px solid #ccc;border-radius:10px;padding:16px 20px;
  box-shadow:0 4px 20px rgba(0,0,0,0.25);z-index:1000;min-width:260px;max-width:90vw;font-size:13px;line-height:1.8}}
#score-card h3{{margin:0 0 8px;font-size:14px;border-bottom:1px solid #eee;padding-bottom:6px}}
#score-card ul{{margin:0;padding-left:18px}}
#score-card li{{margin-bottom:2px}}
#score-card-close{{float:right;cursor:pointer;font-size:18px;color:#999;margin-top:-4px}}
#score-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:999}}
td.price,td.tsubo{{text-align:right;white-space:nowrap}}
td.addr{{max-width:200px;font-size:12px}}
small{{color:#666}}
a{{color:#1a6bd1}}
#search-bar{{background:#f8f8f8;border:1px solid #ddd;padding:12px 16px;border-radius:6px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}}
#search-bar label{{font-weight:bold;font-size:12px}}
#search-bar input,#search-bar select{{font-size:12px;padding:4px 6px;border:1px solid #ccc;border-radius:3px}}
#result-count{{font-size:12px;color:#666;margin-left:auto}}
tr.hidden{{display:none}}
</style>
</head>
<body>
<div id="score-overlay" onclick="closeScoreCard()"></div>
<div id="score-card">
  <span id="score-card-close" onclick="closeScoreCard()">✕</span>
  <h3 id="score-card-title">スコア内訳</h3>
  <ul id="score-card-list"></ul>
</div>
<h1>JR高槻駅周辺 物件リスト（<span id="shown-count">{len(scored)}</span>件 / 全{len(scored)}件）</h1>

<div id="search-bar">
  <label>キーワード</label>
  <input type="text" id="kw" placeholder="町名・間取・住所など" style="width:180px">

  <label>評価</label>
  <select id="sel-rec">
    <option value="">すべて</option>
    <option value="◎">◎のみ</option>
    <option value="◎○">◎○</option>
    <option value="◎○△">◎○△（除外除く）</option>
  </select>

  <label>物件種別</label>
  <select id="sel-type">
    <option value="">すべて</option>
    <option value="中古">中古一戸建て</option>
    <option value="新築">新築一戸建て</option>
  </select>

  <label>価格上限</label>
  <select id="sel-price">
    <option value="">上限なし</option>
    <option value="3000">3000万円以下</option>
    <option value="4000">4000万円以下</option>
    <option value="5000">5000万円以下</option>
    <option value="6000">6000万円以下</option>
    <option value="7000">7000万円以下</option>
  </select>

  <button onclick="resetFilters()" style="font-size:12px;padding:4px 10px">リセット</button>
  <span id="result-count"></span>
</div>

<p style="font-size:11px;color:#888;margin-top:-8px">
  ◎≥70点 / ○≥50点 / △≥30点 / ✕=除外 — 点数にカーソルで計算内訳 —
  <a href="market.html" target="_blank">町別成約実績 →</a>
</p>

<table id="main-table">
<thead>
<tr>
  <th>評価</th><th style="cursor:pointer;text-decoration:underline dotted #999" onclick="showGroundRules()" title="採点ロジックを表示">点 ℹ️</th><th>価格</th><th>坪単価</th><th>相場<br>坪単価</th>
  <th>徒歩(JR推定<br>/SUUMO)</th><th>直線距離</th>
  <th>間取</th><th>土地(坪)<br>建物</th>
  <th>築年月</th><th>町名 / 住所</th><th>物件ページ</th>
</tr>
</thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>

<script>
function showGroundRules() {{
  document.getElementById('score-card-title').textContent = '採点ロジック（Ground Rules）';
  document.getElementById('score-card-list').innerHTML = `
    <li><b>🚶 JR高槻駅距離（最大40点）</b><ul>
      <li>≤600m（≈7分）→ +40</li>
      <li>≤800m（≈10分）→ +35</li>
      <li>≤1000m（≈12分）→ +25</li>
      <li>≤1250m（≈15分）→ +10</li>
      <li>1250m超 → 0</li>
    </ul></li>
    <li><b>🏠 建物面積（最大15点）</b><ul>
      <li>≥90㎡ → +15 / ≥80㎡ → +13 / ≥70㎡ → +8 / ≥60㎡ → +5 / 60㎡未満 → 0</li>
    </ul></li>
    <li><b>🌍 土地面積（最大20点）</b><ul>
      <li>≥90㎡ → +20 / ≥80㎡ → +15 / ≥70㎡ → +12 / ≥60㎡ → +5 / 60㎡未満 → 0</li>
    </ul></li>
    <li><b>💴 坪単価 vs 相場（±点）</b><ul>
      <li>相場比 ≤90% → +15 / ≤100% → 0 / ≤115% → −10 / 115%超 → −20</li>
    </ul></li>
    <li><b>🏗️ 築年数（最大+15 / 最小−10点）</b><ul>
      <li>2020年〜 → +15 / 2015〜 → +10 / 2010〜 → +8 / 2000〜 → +5</li>
      <li>1981〜1999年 → 0 / 〜1980年（旧耐震）→ −10 / 土地のみはスコアなし</li>
    </ul></li>
    <li><b>🛣️ 接道・道路幅（+5 〜 −15点）</b><ul>
      <li>セットバック・4m未満・私道負担 → −15 / 前面道路6m以上・公道6m → +5</li>
    </ul></li>
    <li><b>🔨 リフォームの質（準備中）</b><ul>
      <li style="color:#999">スクレイピングでリフォーム情報取得後に有効化予定</li>
    </ul></li>
    <li><b>🛒 周辺環境（+10 〜 −15点）</b><ul>
      <li>スーパー徒歩5分以内（ライフ・万代・アルプラ等） → +10</li>
      <li>坂あり・傾斜 → −15</li>
    </ul></li>
    <li><b>🌳 安満遺跡公園（最大10点）</b><ul>
      <li>≤300m → +10 / ≤600m → +7 / ≤1000m → +5</li>
    </ul></li>
    <li><b>評価基準：</b> ◎≥70点 / ○≥50点 / △≥30点 / ✕=除外</li>
    <li><b>除外条件：</b> JR高槻駅から1560m超 または 除外キーワード一致</li>
  `;
  document.getElementById('score-overlay').style.display = 'block';
  document.getElementById('score-card').style.display = 'block';
}}
function showScoreCard(td) {{
  const reasons = JSON.parse(td.dataset.reasons || '[]');
  const score = td.textContent.trim();
  const row = td.closest('tr');
  const district = row.dataset.district || '';
  document.getElementById('score-card-title').textContent =
    (district ? district + ' — ' : '') + 'スコア: ' + score + '点';
  const ul = document.getElementById('score-card-list');
  ul.innerHTML = reasons.map(r => `<li>${{r}}</li>`).join('');
  document.getElementById('score-overlay').style.display = 'block';
  document.getElementById('score-card').style.display = 'block';
}}
function closeScoreCard() {{
  document.getElementById('score-overlay').style.display = 'none';
  document.getElementById('score-card').style.display = 'none';
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeScoreCard(); }});

function applyFilters() {{
  const kw = document.getElementById('kw').value.toLowerCase();
  const rec = document.getElementById('sel-rec').value;
  const typ = document.getElementById('sel-type').value;
  const maxPrice = parseInt(document.getElementById('sel-price').value) || Infinity;

  let shown = 0;
  document.querySelectorAll('#main-table tbody tr').forEach(tr => {{
    const address = (tr.dataset.address + ' ' + tr.dataset.district + ' ' + tr.dataset.floor).toLowerCase();
    const score = parseInt(tr.dataset.score);
    const excl = tr.dataset.excl === '1';
    const recCell = tr.querySelector('td.rec').textContent.trim();
    const price = parseInt(tr.dataset.price) || 0;
    const type = tr.dataset.type;

    let hide = false;
    if (kw && !address.includes(kw)) hide = true;
    if (rec === '◎' && recCell !== '◎') hide = true;
    if (rec === '◎○' && !['◎','○'].includes(recCell)) hide = true;
    if (rec === '◎○△' && (excl || !['◎','○','△'].includes(recCell))) hide = true;
    if (typ && !type.includes(typ)) hide = true;
    if (maxPrice < Infinity && price > maxPrice) hide = true;

    tr.classList.toggle('hidden', hide);
    if (!hide) shown++;
  }});
  document.getElementById('shown-count').textContent = shown;
}}

function resetFilters() {{
  ['kw','sel-rec','sel-type','sel-price'].forEach(id => {{
    const el = document.getElementById(id);
    el.tagName === 'INPUT' ? el.value = '' : el.selectedIndex = 0;
  }});
  applyFilters();
}}

['kw','sel-rec','sel-type','sel-price'].forEach(id =>
  document.getElementById(id).addEventListener('input', applyFilters)
);
applyFilters();
</script>
</body>
</html>"""

    LIST_PATH.write_text(list_html, encoding="utf-8")
    print(f"Generated {LIST_PATH} ({len(scored)} properties)")

    # ── market.html（町別成約実績） ──
    district_sections = []
    for district in sorted(district_props.keys()):
        anchor = district_anchor(district)
        d_props = district_props[district]
        prop_rows = ""
        for dp, dsc in sorted(d_props, key=lambda x: x[0].get("price_man") or 0):
            dp_price = f"{dp['price_man']}万円" if dp["price_man"] else "—"
            dp_tsubo = f"{dsc['tsubo_price']:.1f}万円/坪" if dsc.get("tsubo_price") else "—"
            dp_land = f"{dp['land_area_m2']:.0f}㎡" if dp["land_area_m2"] else "—"
            dp_bld = f"{dp['building_area_m2']:.0f}㎡" if dp["building_area_m2"] else "—"
            dp_built = dp.get("built_year_month") or "—"
            dp_name = (dp.get("name") or "")[:30]
            prop_rows += f"""
      <tr>
        <td>{dsc['recommendation']}</td>
        <td>{dp_price}</td><td>{dp_tsubo}</td>
        <td>{dp_land}</td><td>{dp_bld}</td>
        <td>{dp_built}</td>
        <td><a href="{dp['url']}" target="_blank">{dp_name}</a></td>
      </tr>"""

        med = district_median.get(district)
        rf_info = reinfolib_data.get(district, {})
        rf_count = rf_info.get("count", 0)
        if med:
            source = f"国交省成約実績{rf_count}件の中央値" if rf_count else "掲載物件からの推定"
            med_str = f"坪単価中央値: <strong>{med}万円/坪</strong>（{source}）"
        else:
            med_str = "成約実績データなし"

        # reinfolib 成約実績テーブル
        rf_transactions = rf_info.get("transactions", [])
        if rf_transactions:
            txn_rows = ""
            for t in rf_transactions[:100]:
                floor_str = f"{t.get('floor_m2')}㎡" if t.get('floor_m2') else "—"
                station_str = f"{t.get('station')} {t.get('station_min')}分" if t.get('station_min') else t.get('station') or "—"
                road_str = f"{t.get('road_dir','')}{t.get('road_width','')}m" if t.get('road_width') else "—"
                remarks = t.get('remarks') or ''
                txn_rows += f"""<tr>
                  <td>{t['period']}</td>
                  <td style="text-align:right">{t['price_man']}万円</td>
                  <td style="text-align:right">{t['tsubo_price']}万円/坪</td>
                  <td style="text-align:right">{t['land_m2']}㎡</td>
                  <td style="text-align:right">{floor_str}</td>
                  <td>{t.get('built') or '—'}</td>
                  <td>{t.get('structure') or '—'}</td>
                  <td>{station_str}</td>
                  <td>{road_str}</td>
                  <td>{t.get('city_plan') or '—'}</td>
                  <td style="font-size:11px;color:#888">{remarks}</td>
                </tr>"""
            rf_section = f"""<h3>過去の成約実績（国交省 reinfolib / {rf_count}件・直近100件表示）</h3>
  <div style="overflow-x:auto">
  <table>
  <thead><tr>
    <th>取引時期</th><th>成約価格</th><th>坪単価</th>
    <th>土地面積</th><th>延床面積</th><th>建築年</th><th>構造</th>
    <th>最寄駅（徒歩）</th><th>前面道路</th><th>都市計画</th><th>備考</th>
  </tr></thead>
  <tbody>{txn_rows}</tbody>
  </table></div>"""
        else:
            rf_section = '<p style="color:#999;font-size:12px">この地区の成約実績データなし</p>'

        district_sections.append(f"""
<div class="dist" id="dist-{anchor}">
  <h2>{district}（掲載{len(d_props)}件）</h2>
  <p style="color:#1a6bd1;font-size:13px">{med_str}</p>
  <h3>現在の掲載物件</h3>
  <table>
  <thead><tr><th>評価</th><th>価格</th><th>坪単価</th><th>土地</th><th>建物</th><th>築年月</th><th>物件名</th></tr></thead>
  <tbody>{prop_rows}</tbody>
  </table>
  {rf_section}
  <p><a href="https://www.reinfolib.mlit.go.jp/" target="_blank">reinfolib で検索 →</a>
  &nbsp; <a href="properties_list.html">← 物件一覧に戻る</a></p>
</div><hr>""")

    # reinfolib のみのデータがある地区も追加
    rf_only_districts = sorted(set(reinfolib_data.keys()) - set(district_props.keys()))
    for d in rf_only_districts:
        rf_info = reinfolib_data.get(d, {})
        rf_count = rf_info.get("count", 0)
        med = rf_info.get("median_tsubo")
        rf_transactions = rf_info.get("transactions", [])
        txn_rows = ""
        for t in rf_transactions[:100]:
            floor_str = f"{t.get('floor_m2')}㎡" if t.get('floor_m2') else "—"
            station_str = f"{t.get('station')} {t.get('station_min')}分" if t.get('station_min') else t.get('station') or "—"
            road_str = f"{t.get('road_dir','')}{t.get('road_width','')}m" if t.get('road_width') else "—"
            remarks = t.get('remarks') or ''
            txn_rows += f"""<tr>
              <td>{t['period']}</td>
              <td style="text-align:right">{t['price_man']}万円</td>
              <td style="text-align:right">{t['tsubo_price']}万円/坪</td>
              <td style="text-align:right">{t['land_m2']}㎡</td>
              <td style="text-align:right">{floor_str}</td>
              <td>{t.get('built') or '—'}</td>
              <td>{t.get('structure') or '—'}</td>
              <td>{station_str}</td>
              <td>{road_str}</td>
              <td>{t.get('city_plan') or '—'}</td>
              <td style="font-size:11px;color:#888">{remarks}</td>
            </tr>"""
        rf_section = f"""<h3>過去の成約実績（国交省 reinfolib / {rf_count}件・直近100件表示）</h3>
  <div style="overflow-x:auto">
  <table>
  <thead><tr>
    <th>取引時期</th><th>成約価格</th><th>坪単価</th>
    <th>土地面積</th><th>延床面積</th><th>建築年</th><th>構造</th>
    <th>最寄駅（徒歩）</th><th>前面道路</th><th>都市計画</th><th>備考</th>
  </tr></thead>
  <tbody>{txn_rows}</tbody>
  </table></div>""" if txn_rows else ""
        anchor = district_anchor(d)
        med_str = f"坪単価中央値: <strong>{med}万円/坪</strong>（国交省成約実績{rf_count}件）" if med else ""
        district_sections.append(f"""
<div class="dist" id="dist-{anchor}">
  <h2>{d}（掲載物件なし）</h2>
  <p style="color:#1a6bd1;font-size:13px">{med_str}</p>
  {rf_section}
  <p><a href="properties_list.html">← 物件一覧に戻る</a></p>
</div><hr>""")

    all_districts = sorted(set(district_props.keys()) | set(reinfolib_data.keys()))
    nav_links = " | ".join(
        f'<a href="#dist-{district_anchor(d)}">{d}</a>'
        for d in all_districts
    )

    market_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>町別 成約実績・掲載物件</title>
<style>
body{{font-family:sans-serif;font-size:13px;margin:20px}}
h1{{font-size:18px}}
h2{{font-size:15px;margin-top:40px;border-left:4px solid #4a90d9;padding-left:8px}}
h3{{font-size:13px;color:#555}}
table{{border-collapse:collapse;width:100%;margin-bottom:12px}}
th,td{{border:1px solid #ccc;padding:4px 8px}}
th{{background:#f0f0f0}}
a{{color:#1a6bd1}}
.nav{{font-size:11px;line-height:2;background:#f8f8f8;padding:10px;border:1px solid #ddd;border-radius:4px;margin-bottom:20px}}
.dist{{margin-bottom:20px}}
hr{{border:none;border-top:1px solid #e0e0e0;margin:24px 0}}
</style>
</head>
<body>
<h1>町別 成約実績・掲載物件</h1>
<p><a href="properties_list.html">← 物件一覧に戻る</a></p>
<div class="nav">{nav_links}</div>
{''.join(district_sections)}
</body>
</html>"""

    MARKET_PATH.write_text(market_html, encoding="utf-8")
    print(f"Generated {MARKET_PATH} ({len(district_props)} districts)")


if __name__ == "__main__":
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    generate(cfg)

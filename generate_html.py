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
    from statistics import median as _median
    REINFOLIB_PATH = Path(__file__).parent / "reinfolib_data.json"
    reinfolib_data = {}
    if REINFOLIB_PATH.exists():
        with open(REINFOLIB_PATH, encoding="utf-8") as f:
            reinfolib_data = json.load(f)

    # 期間別の地区坪単価中央値を事前計算
    PERIOD_RANGES = {
        "2024-2025": ("2024", "2025"),
        "2023-2025": ("2023", "2024", "2025"),
        "2022-2025": ("2022", "2023", "2024", "2025"),
        "all":       None,  # 全期間
    }
    DEFAULT_PERIOD = "2024-2025"

    def compute_district_medians(period_years):
        result = {}
        for d, v in reinfolib_data.items():
            txns = v.get("transactions", [])
            if period_years:
                txns = [t for t in txns if t.get("period", "")[:4] in period_years]
            vals = [t["tsubo_price"] for t in txns if t.get("tsubo_price")]
            if vals:
                result[d] = round(_median(vals), 1)
        return result

    # 全期間分を計算してJSに埋め込む用（地区名をanchorキーに変換）
    all_period_medians = {
        period: {district_anchor(d): v for d, v in compute_district_medians(years).items()}
        for period, years in PERIOD_RANGES.items()
    }

    # デフォルト期間で物件に付与
    district_median = all_period_medians[DEFAULT_PERIOD]

    # market_tsubo_median を各物件に付与（デフォルト期間）
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

    # ── 重複排除（同一住所+価格+土地面積 → 最上位1件のみ残す）──
    seen_keys = set()
    deduped = []
    for p, sc in scored:
        key = (p.get("address", ""), p.get("price_man"), p.get("land_area_m2"))
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append((p, sc))
    dup_count = len(scored) - len(deduped)
    scored = deduped

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

        # 築年・徒歩 data属性用
        import re as _re
        _is_land_only = not p.get("building_area_m2")
        built_year_m = _re.search(r"(\d{4})年", p.get("built_year_month") or "")
        data_built = built_year_m.group(1) if built_year_m else ("land" if _is_land_only else "")
        data_walk = p.get("walk_minutes") or p.get("est_walk_jr_min") or ""

        # LDK数値抽出 (3LDK→3, 4DK→4)
        fp_m = _re.search(r"(\d+)[SLDK]", floor_plan)
        data_ldk = fp_m.group(1) if fp_m else "0"

        # 間取り表示：土地のみなら「土地」
        floor_display = floor_plan if floor_plan != "—" else ("土地" if _is_land_only else "—")

        # 坪単価（数値のみ、JS切替用）
        tsubo_raw = f"{tsubo_val:.1f}" if tsubo_val else ""

        # data属性でフィルタ用
        rows_html.append(f"""
    <tr class="{row_class}" data-score="{score}" data-excl="{'1' if excl else '0'}"
        data-address="{address}" data-district="{district}" data-type="{prop_type}"
        data-price="{p['price_man'] or ''}" data-floor="{floor_plan}"
        data-walk="{data_walk}" data-built="{data_built}" data-ldk="{data_ldk}"
        data-tsubo="{tsubo_raw}" data-anchor="{anchor}">
      <td class="rec">{rec}</td>
      <td class="score" data-reasons='{tooltip_json}' onclick="showScoreCard(this)">{score}</td>
      <td>{land}{land_tsubo}<br><small style="color:#666">{building}</small></td>
      <td>{floor_display}</td>
      <td class="price">{price}</td>
      <td class="tsubo tsubo-val">{tsubo_cell}</td>
      <td class="tsubo market-val"></td>
      <td>{built}</td>
      <td>{walk_jr}<br><small style="color:#999">{walk_sub}</small></td>
      <td>{dist_jr}</td>
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
  <input type="text" id="kw" placeholder="町名・住所など" style="width:140px">

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

  <label>駅徒歩</label>
  <select id="sel-walk">
    <option value="">指定なし</option>
    <option value="7">〜7分</option>
    <option value="10">〜10分</option>
    <option value="12">〜12分</option>
    <option value="15">〜15分</option>
    <option value="20">〜20分</option>
  </select>

  <label>間取り</label>
  <select id="sel-floor">
    <option value="">すべて</option>
    <option value="1LDK">1LDK以上</option>
    <option value="2LDK">2LDK以上</option>
    <option value="3LDK">3LDK以上</option>
    <option value="4LDK">4LDK以上</option>
  </select>

  <label>築年数</label>
  <select id="sel-built">
    <option value="">指定なし</option>
    <option value="2020">2020年以降</option>
    <option value="2015">2015年以降</option>
    <option value="2010">2010年以降</option>
    <option value="2000">2000年以降</option>
    <option value="land">土地のみ</option>
  </select>

  <label>相場期間</label>
  <select id="sel-period" onchange="updateMarketCells()">
    <option value="2024-2025">直近2年（2024-25）</option>
    <option value="2023-2025">直近3年（2023-25）</option>
    <option value="2022-2025">直近4年（2022-25）</option>
    <option value="all">全期間（2020-25）</option>
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
  <th>評価</th>
  <th style="cursor:pointer;text-decoration:underline dotted #999" onclick="showGroundRules()" title="採点ロジックを表示">点 ℹ️</th>
  <th>土地(坪)<br><small>建物</small></th>
  <th>間取り</th>
  <th>価格</th>
  <th>坪単価</th>
  <th>相場<br>坪単価</th>
  <th>築年月</th>
  <th>徒歩<br><small>推定</small></th>
  <th>直線距離</th>
  <th>町名 / 住所</th>
  <th>物件ページ</th>
</tr>
</thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>

<script>
// 期間別・地区別 成約坪単価中央値
const MARKET_DATA = {json.dumps(all_period_medians, ensure_ascii=False)};

function updateMarketCells() {{
  const period = document.getElementById('sel-period').value;
  const medians = MARKET_DATA[period] || {{}};
  document.querySelectorAll('#main-table tbody tr').forEach(tr => {{
    const anchor = tr.dataset.anchor || '';
    const tsuboVal = parseFloat(tr.dataset.tsubo) || 0;
    const med = medians[anchor] || medians[decodeURIComponent(anchor)];
    const cell = tr.querySelector('td.market-val');
    if (!cell) return;
    if (med) {{
      const ratio = tsuboVal > 0 ? tsuboVal / med : null;
      let pctHtml = '';
      if (ratio) {{
        const pct = Math.round((ratio - 1) * 100);
        const sign = pct >= 0 ? '+' : '';
        const color = ratio <= 0.90 ? '#1a8a1a' : ratio <= 1.00 ? '#555' : ratio <= 1.15 ? '#b86000' : '#c00';
        pctHtml = ` <span style="color:${{color}};font-size:11px">(${{sign}}${{pct}}%)</span>`;
        // 坪単価セルの%も更新
        const tsuboCell = tr.querySelector('td.tsubo-val');
        if (tsuboCell && tsuboVal > 0) {{
          tsuboCell.innerHTML = `${{tsuboVal.toFixed(1)}}万円/坪${{pctHtml}}`;
        }}
      }}
      cell.innerHTML = `<a href="market.html#dist-${{anchor}}" target="_blank" style="color:#1a6bd1">${{med.toFixed(1)}}万円/坪</a>`;
    }} else {{
      cell.innerHTML = anchor
        ? `<a href="market.html#dist-${{anchor}}" target="_blank" style="color:#bbb;font-size:11px">成約実績↓</a>`
        : '<span style="color:#bbb">—</span>';
    }}
  }});
}}

function showGroundRules() {{
  document.getElementById('score-card-title').textContent = '採点ロジック（Ground Rules）';
  document.getElementById('score-card-list').innerHTML = `
    <li><b>🚶 JR高槻駅距離（最大+40 / 最小−10点）</b><ul>
      <li>≤600m（≈7分）→ +40</li>
      <li>≤800m（≈10分）→ +35</li>
      <li>≤1000m（≈12分）→ +20</li>
      <li>≤1250m（≈15分）→ +10</li>
      <li>≤1500m（≈18分）→ −5</li>
      <li>1500m超 → −10</li>
    </ul></li>
    <li><b>🏠 建物面積（最大15点）</b><ul>
      <li>≥90㎡ → +15 / ≥80㎡ → +13 / ≥70㎡ → +8 / ≥60㎡ → +5 / 60㎡未満 → 0</li>
    </ul></li>
    <li><b>🌍 土地面積（最大20点）</b><ul>
      <li>≥90㎡ → +20 / ≥80㎡ → +15 / ≥70㎡ → +12 / ≥60㎡ → +5 / 60㎡未満 → 0</li>
    </ul></li>
    <li><b>💴 坪単価 vs 相場（±点）</b><ul>
      <li>相場比 ≤90% → +15 / ≤100% → 0 / ≤115% → −10 / ≤125% → −20 / 125%超 → −30</li>
    </ul></li>
    <li><b>🏗️ 築年数（最大+15 / 最小−15点）</b><ul>
      <li>2025年〜 → +15 / 2020〜 → +12 / 2015〜 → +10 / 2010〜 → +7 / 2000〜 → +5</li>
      <li>1981〜1999年 → 0 / 〜1980年（旧耐震）→ −15</li>
      <li>建物なし（土地のみ）→ 築年スコアなし＋別途 −15点ペナルティ</li>
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
    <li><b>🌳 安満遺跡公園（最大15点）</b><ul>
      <li>≤300m → +15 / ≤600m → +10 / ≤1000m → +5</li>
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
  const maxWalk = parseInt(document.getElementById('sel-walk').value) || Infinity;
  const minLdk = parseInt(document.getElementById('sel-floor').value) || 0;
  const builtFilter = document.getElementById('sel-built').value;

  let shown = 0;
  document.querySelectorAll('#main-table tbody tr').forEach(tr => {{
    const address = (tr.dataset.address + ' ' + tr.dataset.district + ' ' + tr.dataset.floor).toLowerCase();
    const excl = tr.dataset.excl === '1';
    const recCell = tr.querySelector('td.rec').textContent.trim();
    const price = parseInt(tr.dataset.price) || 0;
    const type = tr.dataset.type || '';
    const walk = parseInt(tr.dataset.walk) || 999;
    const built = tr.dataset.built || '';
    const ldk = parseInt(tr.dataset.ldk) || 0;

    let hide = false;
    if (kw && !address.includes(kw)) hide = true;
    if (rec === '◎' && recCell !== '◎') hide = true;
    if (rec === '◎○' && !['◎','○'].includes(recCell)) hide = true;
    if (rec === '◎○△' && (excl || !['◎','○','△'].includes(recCell))) hide = true;
    if (typ && !type.includes(typ)) hide = true;
    if (maxPrice < Infinity && price > maxPrice) hide = true;
    if (maxWalk < Infinity && walk > maxWalk) hide = true;
    if (minLdk > 0 && ldk < minLdk) hide = true;
    if (builtFilter === 'land' && built !== 'land') hide = true;
    else if (builtFilter && builtFilter !== 'land') {{
      if (!built || built === 'land' || parseInt(built) < parseInt(builtFilter)) hide = true;
    }}

    tr.classList.toggle('hidden', hide);
    if (!hide) shown++;
  }});
  document.getElementById('shown-count').textContent = shown;
}}

function resetFilters() {{
  ['kw','sel-rec','sel-type','sel-price','sel-walk','sel-floor','sel-built'].forEach(id => {{
    const el = document.getElementById(id);
    el.tagName === 'INPUT' ? el.value = '' : el.selectedIndex = 0;
  }});
  applyFilters();
}}

['kw','sel-rec','sel-type','sel-price','sel-walk','sel-floor','sel-built'].forEach(id =>
  document.getElementById(id).addEventListener('input', applyFilters)
);
applyFilters();
updateMarketCells();
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
  <h2 data-anchor="{anchor}" onclick="toggleDist(this)">
    {district}（掲載{len(d_props)}件）
    <span class="toggle-icon">▼</span>
  </h2>
  <div class="dist-body" id="body-{anchor}">
    <p style="color:#1a6bd1;font-size:13px">{med_str}</p>
    <h3>現在の掲載物件</h3>
    <table>
    <thead><tr><th>評価</th><th>価格</th><th>坪単価</th><th>土地</th><th>建物</th><th>築年月</th><th>物件名</th></tr></thead>
    <tbody>{prop_rows}</tbody>
    </table>
    {rf_section}
    <p><a href="https://www.reinfolib.mlit.go.jp/" target="_blank">reinfolib で検索 →</a>
    &nbsp; <a href="properties_list.html">← 物件一覧に戻る</a></p>
  </div>
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
  <h2 data-anchor="{anchor}" onclick="toggleDist(this)">
    {d}（掲載物件なし）
    <span class="toggle-icon">▼</span>
  </h2>
  <div class="dist-body" id="body-{anchor}">
    <p style="color:#1a6bd1;font-size:13px">{med_str}</p>
    {rf_section}
    <p><a href="properties_list.html">← 物件一覧に戻る</a></p>
  </div>
</div><hr>""")

    all_districts = sorted(set(district_props.keys()) | set(reinfolib_data.keys()))
    nav_links = " | ".join(
        f'<a href="#dist-{district_anchor(d)}">{d}</a>'
        for d in all_districts
    )

    # 四半期別坪単価データ（グラフ用）
    from collections import defaultdict as _dd
    all_quarters_data = _dd(list)
    district_quarters_data = _dd(lambda: _dd(list))
    for d, v in reinfolib_data.items():
        for t in v.get("transactions", []):
            q = t.get("period", "")
            tp = t.get("tsubo_price")
            if q and tp:
                all_quarters_data[q].append(tp)
                district_quarters_data[d][q].append(tp)

    quarters_sorted = sorted(all_quarters_data.keys())
    # 高槻市全体の四半期中央値
    from statistics import median as _med
    city_series = {q: round(_med(vals), 1) for q, vals in all_quarters_data.items() if vals}
    # 地区別四半期中央値
    district_series = {}
    for d in all_districts:
        qdata = district_quarters_data.get(d, {})
        if qdata:
            district_series[d] = {q: round(_med(vals), 1) for q, vals in qdata.items() if vals}

    chart_data_js = json.dumps({
        "quarters": quarters_sorted,
        "city": city_series,
        "districts": district_series,
    }, ensure_ascii=False)

    # 短い四半期ラベル: '2020年第1四半期' → '20Q1'
    import re as _re
    def short_q(q):
        m = _re.search(r"(\d{4})年第(\d)四半期", q)
        return f"'{m.group(1)[2:]}Q{m.group(2)}" if m else q
    quarters_labels = [short_q(q) for q in quarters_sorted]

    market_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>町別 成約実績・掲載物件</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
body{{font-family:sans-serif;font-size:13px;margin:20px;max-width:1400px}}
h1{{font-size:18px}}
h2{{font-size:15px;margin-top:40px;border-left:4px solid #4a90d9;padding-left:8px;cursor:pointer;user-select:none}}
h2 .toggle-icon{{float:right;font-size:12px;color:#999}}
h3{{font-size:13px;color:#555}}
table{{border-collapse:collapse;width:100%;margin-bottom:12px}}
th,td{{border:1px solid #ccc;padding:4px 8px}}
th{{background:#f0f0f0}}
a{{color:#1a6bd1}}
.nav-wrap{{background:#f8f8f8;border:1px solid #ddd;border-radius:4px;margin-bottom:20px}}
.nav-header{{padding:8px 12px;cursor:pointer;font-size:12px;font-weight:bold;display:flex;justify-content:space-between;align-items:center}}
.nav-body{{display:none;padding:8px 12px;font-size:11px;line-height:2;border-top:1px solid #ddd}}
.nav-body.open{{display:block}}
.dist-body{{display:none}}
.dist-body.open{{display:block}}
hr{{border:none;border-top:1px solid #e0e0e0;margin:16px 0}}
/* グラフエリア */
#chart-section{{background:#f8f8f8;border:1px solid #ddd;border-radius:6px;padding:16px;margin-bottom:24px}}
#chart-section h2{{border:none;margin-top:0;cursor:default}}
.chart-controls{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}}
.tag{{display:inline-flex;align-items:center;gap:4px;background:#e8f0fe;border:1px solid #4a90d9;border-radius:12px;padding:3px 10px;font-size:12px}}
.tag .rm{{cursor:pointer;color:#c00;font-weight:bold;margin-left:2px}}
.tag.city-tag{{background:#fff3e0;border-color:#e67e00}}
#chart-container{{position:relative;height:320px}}
</style>
</head>
<body>
<h1>町別 成約実績・掲載物件</h1>
<p><a href="properties_list.html">← 物件一覧に戻る</a></p>

<!-- ナビ（折りたたみ） -->
<div class="nav-wrap">
  <div class="nav-header" onclick="toggleNav()">
    <span>▶ 町一覧ジャンプ（{len(all_districts)}町）</span>
    <span id="nav-icon">▼ 展開</span>
  </div>
  <div class="nav-body" id="nav-body">{nav_links}</div>
</div>

<!-- 坪単価推移グラフ -->
<div id="chart-section">
  <h2 style="cursor:default;border-left:4px solid #e67e00">📈 坪単価推移グラフ（万円/坪）</h2>
  <div class="chart-controls">
    <span style="font-size:12px;font-weight:bold">表示中：</span>
    <span class="tag city-tag" id="tag-高槻市">高槻市全体 <span class="rm" onclick="removeDistrict('高槻市')">❌</span></span>
    <span id="tags-container"></span>
    <select id="district-select" style="font-size:12px;padding:4px 8px">
      <option value="">＋ 町を追加...</option>
      {''.join(f'<option value="{d}">{d}</option>' for d in all_districts if district_series.get(d))}
    </select>
  </div>
  <div id="chart-container"><canvas id="trendChart"></canvas></div>
</div>

{''.join(district_sections)}

<script>
const CHART_DATA = {chart_data_js};
const LABELS = {json.dumps(quarters_labels, ensure_ascii=False)};
const QUARTERS = CHART_DATA.quarters;
const COLORS = ['#e67e00','#1a6bd1','#1a8a1a','#c00','#7b2d8b','#0097a7','#795548','#607d8b','#e91e63','#4caf50'];
let colorIdx = 1;
let activeDistricts = ['高槻市'];

function toggleNav() {{
  const body = document.getElementById('nav-body');
  const icon = document.getElementById('nav-icon');
  body.classList.toggle('open');
  icon.textContent = body.classList.contains('open') ? '▲ 閉じる' : '▼ 展開';
}}

function toggleDist(el) {{
  const anchor = el.dataset.anchor;
  const body = document.getElementById('body-' + anchor);
  const icon = el.querySelector('.toggle-icon');
  if (body) {{
    body.classList.toggle('open');
    icon.textContent = body.classList.contains('open') ? '▲' : '▼';
  }}
}}

// Chart.js セットアップ
const ctx = document.getElementById('trendChart').getContext('2d');
const chart = new Chart(ctx, {{
  type: 'line',
  data: {{ labels: LABELS, datasets: [] }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ position: 'top', labels: {{ font: {{ size: 12 }} }} }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y}}万円/坪` }} }}
    }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 11 }} }} }},
      y: {{ title: {{ display: true, text: '万円/坪' }}, ticks: {{ font: {{ size: 11 }} }} }}
    }}
  }}
}});

function getSeriesData(name) {{
  const src = name === '高槻市' ? CHART_DATA.city : (CHART_DATA.districts[name] || {{}});
  return QUARTERS.map(q => src[q] ?? null);
}}

function renderChart() {{
  chart.data.datasets = activeDistricts.map((name, i) => {{
    const color = name === '高槻市' ? COLORS[0] : COLORS[1 + (activeDistricts.filter(d=>d!=='高槻市').indexOf(name) % (COLORS.length-1))];
    return {{
      label: name === '高槻市' ? '高槻市全体' : name,
      data: getSeriesData(name),
      borderColor: color, backgroundColor: color + '22',
      tension: 0.3, spanGaps: true,
      borderWidth: name === '高槻市' ? 3 : 2,
      pointRadius: 3,
    }};
  }});
  chart.update();
}}

function removeDistrict(name) {{
  activeDistricts = activeDistricts.filter(d => d !== name);
  if (name === '高槻市') {{
    document.getElementById('tag-高槻市').style.display = 'none';
  }} else {{
    const tag = document.getElementById('tag-' + name);
    if (tag) tag.remove();
  }}
  renderChart();
}}

document.getElementById('district-select').addEventListener('change', function() {{
  const name = this.value;
  if (!name || activeDistricts.includes(name)) {{ this.value = ''; return; }}
  activeDistricts.push(name);
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.id = 'tag-' + name;
  tag.innerHTML = `${{name}} <span class="rm" onclick="removeDistrict('${{name}}')">❌</span>`;
  document.getElementById('tags-container').appendChild(tag);
  this.value = '';
  renderChart();
}});

renderChart();

// URLハッシュで直接ジャンプ時は自動展開
(function() {{
  const hash = location.hash;
  if (hash && hash.startsWith('#dist-')) {{
    const anchor = hash.slice(6);
    const body = document.getElementById('body-' + anchor);
    if (body) {{
      body.classList.add('open');
      const h2 = document.querySelector('[data-anchor="' + anchor + '"]');
      if (h2) h2.querySelector('.toggle-icon').textContent = '▲';
      setTimeout(() => body.parentElement.scrollIntoView({{behavior:'smooth'}}), 100);
    }}
  }}
}})();
</script>
</body>
</html>"""

    MARKET_PATH.write_text(market_html, encoding="utf-8")
    print(f"Generated {MARKET_PATH} ({len(district_props)} districts)")


if __name__ == "__main__":
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    generate(cfg)

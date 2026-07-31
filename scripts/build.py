#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全国の祭りデータを収集して data/festivals.json を生成する。

データソース（すべて無料・API キー不要）
  1. 日本語版 Wikipedia  MediaWiki API
       - 「日本の祭一覧」記事のウィキテキスト（名称・市町村・開催日が揃っている）
       - Category:日本の祭り (都道府県別) 以下の全記事（小さな地元の祭りを拾う）
       - Category:重要無形民俗文化財 / 日本の年中行事 / 日本の民俗芸能
  2. Wikidata          座標 (P625) の補完
  3. 国土地理院ジオコーディング API  市区町村名からの座標補完
  4. data/manual.csv   手入力ぶんのマージ（Wikipedia に無い集落の祭り用）

使い方:
    python3 scripts/build.py                # フル実行
    python3 scripts/build.py --limit 300    # 動作確認用に件数を絞る
    python3 scripts/build.py --no-geocode   # ジオコーディングを省略（高速）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dates as D            # noqa: E402
import taxonomy as TX        # noqa: E402
import wareki as W           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CACHE_DIR = os.path.join(ROOT, ".cache")

WP_API = "https://ja.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"
GSI_GEOCODE = "https://msearch.gsi.go.jp/address-search/AddressSearch"

UA = ("MatsuriMap/1.0 (https://github.com/; festival open-data builder; "
      "contact: via GitHub issues) python-urllib")

THIS_YEAR = dt.date.today().year
YEARS = [THIS_YEAR, THIS_YEAR + 1, THIS_YEAR + 2]

PREFS = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県",
    "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県",
    "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府",
    "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県",
    "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県",
    "鹿児島県", "沖縄県",
]

# (カテゴリ名, 信頼できるか, 再帰の深さ)
#   信頼できる = そのカテゴリの記事はほぼ全部が祭り・行事。名前フィルタをかけない。
#     「ナマハゲ」「チャッキラコ」「エイサー」など、名前に「祭」が入らない行事を
#     取りこぼさないために重要。
SEED_CATEGORIES = [
    ("Category:日本の祭り (都道府県別)",      True,  3),
    ("Category:重要無形民俗文化財",           True,  2),
    ("Category:選択無形民俗文化財",           True,  2),
    ("Category:都道府県指定無形民俗文化財",   True,  2),
    ("Category:山・鉾・屋台行事",             True,  2),
    ("Category:日本の盆踊り",                 True,  2),
    ("Category:日本の花火大会",               True,  3),
    ("Category:日本の年中行事",               False, 2),
    ("Category:日本の民俗芸能",               False, 3),
    ("Category:神道の祭祀",                   False, 2),
    ("Category:日本の神事",                   False, 2),
    ("Category:日本の伝統芸能",               False, 2),
]

# 都道府県別カテゴリは親カテゴリ経由だと取りこぼすので直接列挙する
PREF_CATEGORIES = [f"Category:{p}の祭り" for p in PREFS]

# 祭りらしいタイトルの判定（カテゴリ経由で入ってきたノイズを落とす）
NAME_OK = re.compile(
    r"(祭|祀|まつり|マツリ|まち$|行事|神事|踊|おどり|舞|ばやし|囃子|神楽|獅子|曳山|山車|"
    r"だんじり|山笠|山鉾|太鼓|流し|送り|迎え|市$|講|大会|フェス|カーニバル|参り|まいり|"
    r"詣|会陽|蘇民|やぶさめ|流鏑馬|competition)"
)
NAME_NG = re.compile(
    r"(一覧|カテゴリ|Category|Template|Wikipedia:|Portal:|の登場人物|漫画|アニメ|"
    r"小説|映画|テレビ|楽曲|アルバム|選手権大会$|甲子園|オリンピック|の歴史$)"
)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_json(url: str, params: dict, retries: int = 4) -> dict:
    q = urllib.parse.urlencode(params, doseq=True)
    full = f"{url}?{q}"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA,
                                                        "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                              # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    print(f"  ! request failed: {last}", file=sys.stderr)
    return {}


def wp(params: dict) -> dict:
    p = {"format": "json", "formatversion": 2, "maxlag": 5, **params}
    return http_json(WP_API, p)


# ---------------------------------------------------------------------------
# 1) タイトル収集
# ---------------------------------------------------------------------------
def fetch_list_article() -> "OrderedDict[str, dict]":
    """「日本の祭一覧」から (記事名 -> {city, dateText, pref}) を抽出。"""
    out: "OrderedDict[str, dict]" = OrderedDict()
    r = wp({"action": "query", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "titles": "日本の祭一覧"})
    try:
        text = r["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    except Exception:                                        # noqa: BLE001
        print("  ! 日本の祭一覧 の取得に失敗", file=sys.stderr)
        return out

    cur_pref = None
    for line in text.split("\n"):
        h = re.match(r"^==+\s*(.+?)\s*==+\s*$", line)
        if h:
            name = re.sub(r"\[\[|\]\]", "", h.group(1)).strip()
            if name in PREFS:
                cur_pref = name
            continue
        if not line.lstrip().startswith("*"):
            continue

        # 記事リンク（最初のもの）を祭りの名前とみなす
        m = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", line)
        if not m:
            continue
        title = m.group(1).split("#")[0].strip()
        if not title or NAME_NG.search(title):
            continue

        # （市町村、開催日）の丸括弧
        paren = re.findall(r"[（(]([^（）()]{2,80})[）)]", line)
        city, date_text = "", ""
        for p in paren:
            p_clean = re.sub(r"\[\[([^\]|]*\|)?([^\]]+)\]\]", r"\2", p)
            parts = re.split(r"[、,]", p_clean)
            for part in parts:
                part = part.strip()
                if re.search(r"[月日曜旬暦]", part) and not date_text:
                    date_text = part
                elif re.search(r"(市|町|村|区|郡)$", part) and not city:
                    city = part
            if date_text or city:
                break

        rec = out.setdefault(title, {"pref": cur_pref, "city": city,
                                     "dateText": date_text, "fromList": True})
        if not rec.get("dateText") and date_text:
            rec["dateText"] = date_text
        if not rec.get("city") and city:
            rec["city"] = city
    return out


def category_members(cat: str, depth: int = 2, seen: set | None = None) -> set:
    """カテゴリ配下の記事タイトルを再帰的に集める。"""
    seen = seen if seen is not None else set()
    titles: set = set()
    cont = {}
    while True:
        r = wp({"action": "query", "list": "categorymembers", "cmtitle": cat,
                "cmlimit": "500", "cmtype": "page|subcat", **cont})
        for m in r.get("query", {}).get("categorymembers", []):
            t = m["title"]
            if t.startswith("Category:"):
                if depth > 0 and t not in seen:
                    seen.add(t)
                    titles |= category_members(t, depth - 1, seen)
            elif ":" not in t:
                titles.add(t)
        if "continue" in r:
            cont = r["continue"]
            time.sleep(0.15)
        else:
            break
    return titles


# ---------------------------------------------------------------------------
# 2) 記事本文・座標の取得
# ---------------------------------------------------------------------------
def strip_wikitext(s: str) -> str:
    """ウィキテキストからタグ付け・年代判定に使えるプレーンテキストを作る。

    完全なパースは狙わない。キーワード一致と年号抽出ができれば十分なので、
    リンク・テンプレート・参照タグを潰して読める形にするだけ。
    """
    if not s:
        return ""
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)

    # テンプレートは中身を残したいものだけ展開してから、残りを削る。
    # {{和暦|1750}} や {{要出典}} など。ネストは3回まで潰す。
    s = re.sub(r"\{\{(?:和暦|西暦|JIS2004|読み|ruby|ルビ)\|([^{}|]*)[^{}]*\}\}", r"\1", s)
    for _ in range(3):
        s2 = re.sub(r"\{\{[^{}]*\}\}", " ", s)
        if s2 == s:
            break
        s = s2

    # [[記事名|表示名]] -> 表示名 / [[記事名]] -> 記事名
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\]", "", s)

    s = re.sub(r"^[=*#:;|!]+", " ", s, flags=re.M)   # 見出し・箇条書き・表の記号
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def fetch_pages(titles: list[str]) -> dict:
    """記事タイトル -> {text, lat, lng, qid, url}

    本文は prop=revisions で取る。prop=extracts は全文モードだと
    1件/リクエストに制限されるため、大量取得にはまったく向かない
    （ここを間違えると 20 件のうち 1 件しか本文が返らない）。
    """
    result: dict[str, dict] = {}
    B = 50
    for i in range(0, len(titles), B):
        batch = titles[i:i + B]
        r = wp({"action": "query",
                "prop": "revisions|coordinates|pageprops|info",
                "rvprop": "content", "rvslots": "main",
                "colimit": "max", "inprop": "url",
                "titles": "|".join(batch)})
        pages = r.get("query", {}).get("pages", [])
        for p in pages:
            if p.get("missing"):
                continue
            try:
                wt = p["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                wt = ""
            co = (p.get("coordinates") or [{}])[0]
            result[p["title"]] = {
                "wikitext": wt,
                "text": strip_wikitext(wt),
                "lat": co.get("lat"),
                "lng": co.get("lon"),
                "qid": (p.get("pageprops") or {}).get("wikibase_item"),
                "url": p.get("fullurl") or
                       "https://ja.wikipedia.org/wiki/" + urllib.parse.quote(p["title"]),
            }
        if (i // B) % 10 == 0:
            print(f"    pages {i}/{len(titles)}  (取得済 {len(result)})", flush=True)
        time.sleep(0.12)
    return result


def wikidata_coords(qids: list[str]) -> dict:
    """Wikidata の QID -> (lat, lng)。P625 が無ければ P276/P131 を辿る。"""
    out: dict[str, tuple] = {}
    pending = [q for q in qids if q]
    B = 50
    hop2: dict[str, str] = {}                 # qid -> 参照先 qid
    for i in range(0, len(pending), B):
        batch = pending[i:i + B]
        r = http_json(WD_API, {"action": "wbgetentities", "format": "json",
                               "props": "claims", "ids": "|".join(batch)})
        for qid, ent in (r.get("entities") or {}).items():
            claims = ent.get("claims", {})
            c = claims.get("P625")
            if c:
                try:
                    v = c[0]["mainsnak"]["datavalue"]["value"]
                    out[qid] = (v["latitude"], v["longitude"])
                    continue
                except Exception:                            # noqa: BLE001
                    pass
            for prop in ("P276", "P131", "P159"):
                cc = claims.get(prop)
                if cc:
                    try:
                        hop2[qid] = cc[0]["mainsnak"]["datavalue"]["value"]["id"]
                        break
                    except Exception:                        # noqa: BLE001
                        pass
        time.sleep(0.12)

    # 1 ホップ先の座標
    targets = sorted(set(hop2.values()))
    coords2: dict[str, tuple] = {}
    for i in range(0, len(targets), B):
        batch = targets[i:i + B]
        r = http_json(WD_API, {"action": "wbgetentities", "format": "json",
                               "props": "claims", "ids": "|".join(batch)})
        for qid, ent in (r.get("entities") or {}).items():
            c = ent.get("claims", {}).get("P625")
            if c:
                try:
                    v = c[0]["mainsnak"]["datavalue"]["value"]
                    coords2[qid] = (v["latitude"], v["longitude"])
                except Exception:                            # noqa: BLE001
                    pass
        time.sleep(0.12)
    for qid, tgt in hop2.items():
        if qid not in out and tgt in coords2:
            out[qid] = coords2[tgt]
    return out


_geo_cache: dict[str, tuple] = {}


def geocode(query: str) -> tuple | None:
    """国土地理院ジオコーディング。市区町村名 → 座標。"""
    if not query:
        return None
    if query in _geo_cache:
        return _geo_cache[query]
    r = http_json(GSI_GEOCODE, {"q": query}, retries=2)
    coord = None
    if isinstance(r, list) and r:
        try:
            lon, lat = r[0]["geometry"]["coordinates"]
            if 122 < lon < 154 and 20 < lat < 46:            # 日本国内チェック
                coord = (lat, lon)
        except Exception:                                    # noqa: BLE001
            pass
    _geo_cache[query] = coord
    time.sleep(0.25)
    return coord


# ---------------------------------------------------------------------------
# 3) レコード組み立て
# ---------------------------------------------------------------------------
PREF_RE = re.compile("(" + "|".join(PREFS) + ")")
CITY_RE = re.compile(r"([一-龥ぁ-んァ-ヶA-Za-zー]{1,8}?[市町村区])")


VENUE_RE = re.compile(r"([一-龥ぁ-んァ-ヶa-zA-Z0-9ヶケ]{2,12}?"
                      r"(?:神社|神宮|大社|八幡宮|天満宮|稲荷|寺|院|廟|宮))")
VENUE_NG = re.compile(r"(神社本庁|神社庁|寺社|神社建築|総本社|系神社)")


def extract_venue(wikitext: str, text: str) -> str:
    """祭りが行われる神社・寺の記事名を推定する。座標補完に使う。"""
    m = re.search(r"\|\s*(?:会場|開催場所|場所|神社|寺院|venue)\s*=\s*([^\n|}]{2,40})",
                  wikitext or "")
    if m:
        v = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", m.group(1))
        v = re.sub(r"\{\{[^{}]*\}\}", "", v).strip()
        mm = VENUE_RE.search(v)
        if mm and not VENUE_NG.search(mm.group(1)):
            return mm.group(1)

    for m in re.finditer(r"\[\[([^\]|#]{2,20})(?:\|[^\]]*)?\]\]", (wikitext or "")[:4000]):
        t = m.group(1).strip()
        if VENUE_RE.fullmatch(t) and not VENUE_NG.search(t):
            return t

    for m in VENUE_RE.finditer((text or "")[:1200]):
        if not VENUE_NG.search(m.group(1)):
            return m.group(1)
    return ""


def extract_place(text: str, hint_pref: str | None, hint_city: str) -> tuple:
    pref = hint_pref
    if not pref:
        m = PREF_RE.search(text[:900])
        pref = m.group(1) if m else None

    city = hint_city
    if not city:
        m = CITY_RE.search(text[:900])
        city = m.group(1) if m else ""

    # 「兵庫県兵庫県三木市」のような二重表記を防ぐ
    if city:
        m = PREF_RE.match(city)
        if m:
            if not pref:
                pref = m.group(1)
            city = city[m.end():]
        if pref and city.startswith(pref):
            city = city[len(pref):]
    return pref, city.strip()


SCHED_CUES = ["開催日", "開催期間", "開催時期", "日程", "斎行", "例祭日", "祭礼日",
              "行事日", "毎年", "行われる", "行われている", "催される", "実施される",
              "開催される", "奉納される", "執り行", "始まる"]

# インフォボックスの日付フィールド
INFOBOX_DATE = re.compile(
    r"\|\s*(?:開催時期|開催日|日付|開催期間|時期|date|日程)\s*=\s*([^\n|}]{2,60})")


def extract_schedule_text(text: str, wikitext: str = "") -> str:
    """本文から開催日らしい文を拾う。

    3段構え:
      1. インフォボックスの「開催時期」等のフィールド
      2. 「毎年◯月◯日に行われる」のような文脈語つきの文
      3. 文脈語がなくても日付表現を含む文（本文全体を対象）
    """
    # 1) インフォボックス
    if wikitext:
        m = INFOBOX_DATE.search(wikitext)
        if m:
            v = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", m.group(1))
            v = re.sub(r"\{\{[^{}]*\}\}", "", v).strip()
            if re.search(r"\d{1,2}月|旧暦", v):
                return v[:120]

    DATEPAT = re.compile(
        r"(\d{1,2}月\d{1,2}日|\d{1,2}月[のと]?第[1-5一二三四五][月火水木金土日]曜|"
        r"\d{1,2}月[のと]?(?:最終|最後の|末の)[月火水木金土日]曜|"
        r"\d{1,2}月(?:上旬|中旬|下旬|初旬)|旧暦\d{1,2}月|"
        r"[のと]?(?:成人|海|敬老|スポーツ|体育)の日)")

    HISTORY = re.compile(
        r"(\d{3,4}年|[明治大正昭和平成令和]\d{1,2}年|元年|"
        r"変更|改称|中止|廃止|再開|創始|始まっ|起源|由来|以前は|かつては|"
        r"だったが|されたが|に移(?:さ|っ)|遷|戦後|当時)")

    sents = [x for x in re.split(r"[。\n]", text) if x.strip()]

    def scan(pool, need_cue):
        for sent in pool:
            if not DATEPAT.search(sent):
                continue
            if need_cue and not any(c in sent for c in SCHED_CUES):
                continue
            return sent.strip()[:120]
        return ""

    clean = [x for x in sents[:150] if not HISTORY.search(x)]
    r = scan(clean, True)
    if r:
        return r
    r = scan(clean, False)
    if r:
        return r
    return scan(sents[:150], True)


def slugify(title: str) -> str:
    return unicodedata.normalize("NFKC", title).replace(" ", "_")


DROP = Counter()      # どの段階で何件落としたかの集計（診断用）


def build_record(title: str, page: dict, hint: dict) -> dict | None:
    text = page.get("text") or ""
    wt = page.get("wikitext") or ""
    if len(text) < 60:
        DROP["本文が短すぎる"] += 1
        return None

    # 一覧に書かれていた日付 → 本文/インフォボックス の順に試す
    candidates = [hint.get("dateText") or "", extract_schedule_text(text, wt)]
    date_text, occ, rules, months = "", [], [], []
    for cand in candidates:
        if not cand:
            continue
        o, r = D.occurrences(cand, YEARS)
        m = D.month_hint(r)
        if m:
            date_text, occ, rules, months = cand, o, r, m
            break
    if not months:
        DROP["開催時期が読み取れない"] += 1
        return None                                # 開催時期不明は地図に置けない

    tags = TX.tag_text(text)
    tags += D.season_tags(months)
    tags = sorted(set(tags))

    has_desig = bool(set(tags) & TX.AGE_PROXY_TAGS)
    founded, conf = W.estimate_founded(text, has_desig)
    age = (THIS_YEAR - founded) if founded else None
    if age is not None and age > 2500:
        founded, age, conf = None, None, "proxy" if has_desig else "unknown"

    if age is not None:
        over50 = age >= 50
    elif conf == "proxy":
        over50 = True
    else:
        # 年不明。伝統色の強い語があれば 50 年以上とみなす（推定込みで広く拾う方針）
        over50 = bool(re.search(r"(伝統|古く|昔から|古来|由緒|奉納|例大祭|例祭|神事|"
                                r"江戸|明治|大正|継承|受け継)", text[:2500]))
        conf = "assumed" if over50 else "unknown"

    pref, city = extract_place(text, hint.get("pref"), hint.get("city", ""))
    irregular = any(r["kind"] == "irregular" for r in rules)

    return {
        "id": slugify(title),
        "name": title,
        "pref": pref,
        "city": city,
        "lat": page.get("lat"),
        "lng": page.get("lng"),
        "qid": page.get("qid"),
        "venue": extract_venue(wt, text),
        "url": page.get("url"),
        "summary": re.sub(r"\s+", " ", text[:180]).strip(),
        "when": date_text,
        "months": months,
        "occ": occ[:8],
        "tags": tags,
        "founded": founded,
        "age": age,
        "ageConf": conf,          # exact / estimated / proxy / assumed / unknown
        "over50": over50,
        "irregular": irregular,
        "src": "wikipedia",
    }


def load_manual() -> list[dict]:
    """data/manual.csv を読む（Wikipedia に無い祭りの手入力用）。"""
    path = os.path.join(DATA_DIR, "manual.csv")
    if not os.path.exists(path):
        return []
    import csv
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if not name or name.startswith("#"):
                continue
            when = (row.get("when") or "").strip()
            occ, rules = D.occurrences(when, YEARS)
            months = D.month_hint(rules)
            tags = [t.strip() for t in (row.get("tags") or "").split("|") if t.strip()]
            tags += D.season_tags(months)
            founded = int(row["founded"]) if (row.get("founded") or "").strip().isdigit() else None
            lat = float(row["lat"]) if (row.get("lat") or "").strip() else None
            lng = float(row["lng"]) if (row.get("lng") or "").strip() else None
            out.append({
                "id": "manual_" + slugify(name),
                "name": name,
                "pref": (row.get("pref") or "").strip() or None,
                "city": (row.get("city") or "").strip(),
                "lat": lat, "lng": lng, "qid": None,
                "url": (row.get("url") or "").strip() or None,
                "summary": (row.get("note") or "").strip(),
                "when": when, "months": months, "occ": occ[:8],
                "tags": sorted(set(tags)),
                "founded": founded,
                "age": (THIS_YEAR - founded) if founded else None,
                "ageConf": "exact" if founded else "assumed",
                "over50": (THIS_YEAR - founded >= 50) if founded else True,
                "irregular": any(r["kind"] == "irregular" for r in rules),
                "src": "manual",
            })
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="処理件数の上限（テスト用）")
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--no-categories", action="store_true")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    print("[1/6] 日本の祭一覧 を取得…", flush=True)
    hints = fetch_list_article()
    print(f"      {len(hints)} 件（開催日つき: "
          f"{sum(1 for v in hints.values() if v['dateText'])}）")

    if not args.no_categories:
        print("[2/6] カテゴリを走査…", flush=True)
        def absorb(titles: set, trusted: bool, pref: str | None = None) -> int:
            new = 0
            for t in titles:
                if t in hints or NAME_NG.search(t):
                    continue
                if not trusted and not NAME_OK.search(t):
                    continue
                hints[t] = {"pref": pref, "city": "", "dateText": "", "fromList": False}
                new += 1
            return new

        for pref in PREFS:
            got = category_members(f"Category:{pref}の祭り", depth=2)
            if got:
                n = absorb(got, trusted=True, pref=pref)
                print(f"      {pref}: +{n}（累計 {len(hints)}）", flush=True)

        for cat, trusted, depth in SEED_CATEGORIES:
            got = category_members(cat, depth=depth)
            n = absorb(got, trusted=trusted)
            print(f"      {cat}: +{n}（累計 {len(hints)}）", flush=True)
    else:
        print("[2/6] カテゴリ走査をスキップ")

    titles = list(hints.keys())
    if args.limit:
        titles = titles[:args.limit]
    print(f"[3/6] 記事本文を取得… ({len(titles)} 件)", flush=True)
    pages = fetch_pages(titles)
    print(f"      取得 {len(pages)} 件")

    print("[4/6] レコード化…", flush=True)
    records = []
    for t in titles:
        p = pages.get(t)
        if not p:
            continue
        rec = build_record(t, p, hints.get(t, {}))
        if rec:
            records.append(rec)
    print(f"      開催時期が判定できた {len(records)} 件")

    print("[5/6] 座標を補完…", flush=True)
    need_wd = [r["qid"] for r in records if r["lat"] is None and r["qid"]]
    if need_wd:
        wdc = wikidata_coords(sorted(set(need_wd)))
        for r in records:
            if r["lat"] is None and r["qid"] in wdc:
                r["lat"], r["lng"] = wdc[r["qid"]]
                r["geoSrc"] = "wikidata"
        print(f"      Wikidata から {len(wdc)} 件")

    todo = [r for r in records if r["lat"] is None and r.get("venue")]
    if todo:
        print(f"      会場記事から座標を引く: {len(todo)} 件", flush=True)
        vpages = fetch_pages(sorted({r["venue"] for r in todo}))
        vcoord = {t: (p["lat"], p["lng"]) for t, p in vpages.items()
                  if p.get("lat") is not None}
        need = [p["qid"] for t, p in vpages.items()
                if p.get("lat") is None and p.get("qid")]
        if need:
            vwd = wikidata_coords(sorted(set(need)))
            for t, p in vpages.items():
                if t not in vcoord and p.get("qid") in vwd:
                    vcoord[t] = vwd[p["qid"]]
        hit = 0
        for r in todo:
            if r["venue"] in vcoord:
                r["lat"], r["lng"] = vcoord[r["venue"]]
                r["geoSrc"] = "venue"
                hit += 1
        print(f"        {hit} 件を補完", flush=True)

    if not args.no_geocode:
        todo = [r for r in records if r["lat"] is None]
        print(f"      ジオコーディング対象 {len(todo)} 件", flush=True)
        for n, r in enumerate(todo):
            pref = r.get("pref") or ""
            city = r.get("city") or ""
            venue = r.get("venue") or ""
            for q, src in ((f"{pref}{city}{venue}", "gsi-venue"),
                           (f"{pref}{city}", "gsi-city"),
                           (f"{pref}{venue}", "gsi-venue"),
                           (city, "gsi-city")):
                if not q.strip():
                    continue
                c = geocode(q)
                if c:
                    r["lat"], r["lng"] = c
                    r["geoSrc"] = src
                    break
            if n % 100 == 0:
                print(f"        {n}/{len(todo)}", flush=True)

    records += load_manual()
    nogeo = sum(1 for r in records if r["lat"] is None)
    if nogeo:
        DROP["座標が取れない"] += nogeo
    records = [r for r in records if r["lat"] is not None]
    records.sort(key=lambda r: (r["months"][0] if r["months"] else 13, r["name"]))
    print(f"      座標つき {len(records)} 件")

    print("[6/6] 書き出し…", flush=True)
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "years": YEARS,
        "count": len(records),
        "taxonomy": TX.tags_json(),
        "festivals": records,
    }
    out = os.path.join(DATA_DIR, "festivals.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(out) / 1024
    print(f"      {out}  ({size:.0f} KB, {len(records)} 件)")

    # ---------------- 診断 ----------------
    print("\n=== 収集の内訳 ===")
    print(f"候補タイトル      : {len(titles)}")
    print(f"本文を取得できた  : {len(pages)}")
    for reason, n in DROP.most_common():
        print(f"  除外: {reason:<22}: {n}")
    print(f"最終レコード      : {len(records)}")

    print("\n=== データの質 ===")
    print("50年以上:", sum(1 for r in records if r["over50"]))
    print("確度:", dict(Counter(r["ageConf"] for r in records)))
    print("座標の由来:", dict(Counter(r.get("geoSrc", "wikipedia") for r in records)))
    prefc = Counter(r["pref"] for r in records if r["pref"])
    print(f"都道府県: {len(prefc)}/47")
    missing = [p for p in PREFS if p not in prefc]
    if missing:
        print("  未収録:", "、".join(missing))
    tagc = Counter(t for r in records for t in r["tags"])
    print("\n=== タグ上位 ===")
    for tid, n in tagc.most_common(20):
        label = TX.TAGS.get(tid, (None, tid, None))[1]
        print(f"  {label}: {n}")


if __name__ == "__main__":
    main()

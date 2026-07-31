# -*- coding: utf-8 -*-
"""
日本語の開催日表記を「実際の日付」に展開する。

Wikipedia の祭り一覧は日付が自然文で書かれている:
    「8月3日から6日」「8月第1金曜から3日間」「7月下旬の土曜日」
    「9月の敬老の日を含む土日月」「旧暦6月15日」「72年間隔」
これらをルールに落とし、任意の年の実日付に展開する。

出力する各オカレンス:
    {"start": "2026-08-03", "end": "2026-08-06", "precision": "exact"|"approx"}
precision が approx のものはフロント側で「◯月上旬ごろ」と表示する。
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
import unicodedata

try:                                  # 旧暦（中国暦ベース。和暦旧暦の近似）
    from lunardate import LunarDate
    HAS_LUNAR = True
except Exception:                     # pragma: no cover
    HAS_LUNAR = False

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
WD = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
WD_NAME = ["月", "火", "水", "木", "金", "土", "日"]

KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
             "元": 1}

PERIOD_RANGE = {"上旬": (1, 10), "初旬": (1, 10), "中旬": (11, 20),
                "下旬": (21, 31), "末": (21, 31), "初め": (1, 10),
                "初": (1, 10), "半ば": (11, 20), "序盤": (1, 10)}

# 日本の国民の祝日のうち「◯月第n月曜」で決まるもの（ハッピーマンデー）
HAPPY_MONDAY = {
    "成人の日": (1, 2), "海の日": (7, 3), "敬老の日": (9, 3), "スポーツの日": (10, 2),
    "体育の日": (10, 2),
}


def _z2h(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _num(s: str) -> int | None:
    """算用数字 or 漢数字を int に。"""
    s = _z2h(s).strip()
    if s.isdigit():
        return int(s)
    if s in KANJI_NUM:
        return KANJI_NUM[s]
    # 十一〜十九
    m = re.fullmatch(r"十([一二三四五六七八九])", s)
    if m:
        return 10 + KANJI_NUM[m.group(1)]
    m = re.fullmatch(r"([二三])十([一二三四五六七八九])?", s)
    if m:
        return KANJI_NUM[m.group(1)] * 10 + (KANJI_NUM[m.group(2)] if m.group(2) else 0)
    return None


def _last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _weekdays_in(y: int, m: int, wd: int, lo: int = 1, hi: int = 31) -> list[dt.date]:
    hi = min(hi, _last_day(y, m))
    out = []
    for d in range(lo, hi + 1):
        date = dt.date(y, m, d)
        if date.weekday() == wd:
            out.append(date)
    return out


# ---------------------------------------------------------------------------
# パース: 自然文 -> ルール群
# ---------------------------------------------------------------------------
def parse_schedule(raw: str) -> list[dict]:
    """開催日を表す自然文からルールのリストを返す。空なら未判定。"""
    if not raw:
        return []
    t = _z2h(raw)
    t = t.replace("〜", "-").replace("～", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+", "", t)

    rules: list[dict] = []

    # --- 不定期・隔年（あとでルール群の末尾に足す。日付判定は妨げない）------
    irregular: list[dict] = []
    m = re.search(r"(\d+)年(?:に一度|間隔|ごと|毎)", t)
    if m:
        irregular.append({"kind": "irregular", "interval": int(m.group(1)), "note": raw})
    if re.search(r"(不定期|隔年|次回は\d{4}年|毎年では)", t):
        irregular.append({"kind": "irregular", "note": raw})

    # --- 旧暦 --------------------------------------------------------------
    for m in re.finditer(r"旧暦(\d{1,2})月(\d{1,2})日", t):
        rules.append({"kind": "lunar", "month": int(m.group(1)), "day": int(m.group(2))})
    if not rules:
        # 「旧暦7月盆明けの亥の日」のように日が特定できない旧暦表記は月まるごと
        m = re.search(r"旧暦(\d{1,2})月", t)
        if m:
            rules.append({"kind": "lunar_period", "month": int(m.group(1)),
                          "period": "上旬"})

    # 旧暦部分は以降の新暦パースから除外（「旧暦6月15日」が6/15にならないように）
    t = re.sub(r"旧暦\d{1,2}月(\d{1,2}日|初午|上旬|中旬|下旬)?", "", t)

    # --- 祝日基準（ハッピーマンデー）--------------------------------------
    for name, (mon, nth) in HAPPY_MONDAY.items():
        if name in t:
            span = 3 if re.search(name + r"[^。]{0,12}(3連休|土日月|を含む土日|3日間)", t) else 1
            offset = -2 if re.search(name + r"[^。]{0,12}(土日月|を含む|前日|3連休)", t) else 0
            rules.append({"kind": "nth_wd", "month": mon, "nth": nth, "wd": 0,
                          "span": span, "offset": offset})

    # --- 第n◯曜日 ---------------------------------------------------------
    # 例: 8月第1金曜から3日間 / 9月第2土曜日・日曜日 / 6月第1金曜日-土曜日
    for m in re.finditer(r"(\d{1,2})月[のと]?第([1-5一二三四五])[?]?([月火水木金土日])曜", t):
        mon, nth, wd = int(m.group(1)), _num(m.group(2)), WD[m.group(3)]
        tail = t[m.end(): m.end() + 24]
        rules.append({"kind": "nth_wd", "month": mon, "nth": nth, "wd": wd,
                      "span": _span_from_tail(tail), "offset": _offset_from_tail(tail)})

    # --- 最終◯曜日 --------------------------------------------------------
    for m in re.finditer(r"(\d{1,2})月[のと]?(?:最終|最後の|末の)([月火水木金土日])曜", t):
        mon, wd = int(m.group(1)), WD[m.group(2)]
        tail = t[m.end(): m.end() + 24]
        rules.append({"kind": "nth_wd", "month": mon, "nth": -1, "wd": wd,
                      "span": _span_from_tail(tail)})

    # --- ◯月上旬/中旬/下旬の◯曜日 ----------------------------------------
    for m in re.finditer(r"(\d{1,2})月(上旬|中旬|下旬|初旬|末)[のに]?([月火水木金土日])曜", t):
        mon, per, wd = int(m.group(1)), m.group(2), WD[m.group(3)]
        tail = t[m.end(): m.end() + 24]
        rules.append({"kind": "period_wd", "month": mon, "period": per, "wd": wd,
                      "span": _span_from_tail(tail)})

    # --- ◯月◯日 - ◯月◯日 / ◯月◯日 - ◯日 -------------------------------
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日[-から]+(?:(\d{1,2})月)?(\d{1,2})日", t):
        m1, d1 = int(m.group(1)), int(m.group(2))
        m2 = int(m.group(3)) if m.group(3) else m1
        d2 = int(m.group(4))
        if _valid(m1, d1) and _valid(m2, d2):
            rules.append({"kind": "range", "m1": m1, "d1": d1, "m2": m2, "d2": d2})

    # --- ◯月◯日から3日間 -------------------------------------------------
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日から(\d{1,2})日間", t):
        m1, d1, n = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid(m1, d1):
            rules.append({"kind": "fixed", "month": m1, "day": d1, "span": n})

    # --- ◯月◯・◯・◯日（日付列挙）------------------------------------------
    # 例:「1月15・16日と12月15・16日」「10月4日・5日・6日」
    for m in re.finditer(r"(\d{1,2})月((?:\d{1,2}日?[・、])+\d{1,2}日)", t):
        mon = int(m.group(1))
        days = [int(x) for x in re.findall(r"\d{1,2}", m.group(2))]
        days = [d for d in days if _valid(mon, d)]
        if len(days) >= 2:
            rules.append({"kind": "range", "m1": mon, "d1": min(days),
                          "m2": mon, "d2": max(days)})

    # --- ◯月◯日 単発 ------------------------------------------------------
    if not any(r["kind"] in ("range", "fixed") for r in rules):
        for m in re.finditer(r"(\d{1,2})月(\d{1,2})日", t):
            m1, d1 = int(m.group(1)), int(m.group(2))
            if _valid(m1, d1):
                rules.append({"kind": "fixed", "month": m1, "day": d1, "span": 1})

    # --- ◯月上旬 - ◯月下旬 -----------------------------------------------
    for m in re.finditer(r"(\d{1,2})月(上旬|中旬|下旬|初旬)-(?:(\d{1,2})月)?(上旬|中旬|下旬|初旬)", t):
        m1, p1 = int(m.group(1)), m.group(2)
        m2 = int(m.group(3)) if m.group(3) else m1
        p2 = m.group(4)
        rules.append({"kind": "period_range", "m1": m1, "p1": p1, "m2": m2, "p2": p2})

    # --- ◯月上旬 単発 ------------------------------------------------------
    if not rules:
        for m in re.finditer(r"(\d{1,2})月(上旬|中旬|下旬|初旬|末|半ば)", t):
            rules.append({"kind": "period", "month": int(m.group(1)), "period": m.group(2)})

    # --- ◯月だけ -----------------------------------------------------------
    if not rules:
        months = sorted({int(x) for x in re.findall(r"(\d{1,2})月", t) if 1 <= int(x) <= 12})
        if len(months) == 1:
            rules.append({"kind": "month", "month": months[0]})
        elif len(months) >= 2:
            rules.append({"kind": "period_range", "m1": months[0], "p1": "上旬",
                          "m2": months[-1], "p2": "下旬"})

    # 重複除去（順序保持）
    seen, uniq = set(), []
    for r in rules + irregular:
        key = tuple(sorted(r.items(), key=lambda kv: kv[0]))
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def _valid(m: int, d: int) -> bool:
    return 1 <= m <= 12 and 1 <= d <= 31


def _span_from_tail(tail: str) -> int:
    """「-日曜日」「・日曜日」「から3日間」「の土日」などから日数を推定。"""
    m = re.match(r"[^\d]{0,4}(\d{1,2})日間", tail)
    if m:
        return int(m.group(1))
    if re.match(r"日?[-・、と]{1,2}(翌日|日曜|土曜|月曜)", tail):
        return 2
    if re.match(r"日?[-・、と]{1,2}[月火水木金土日]曜日?[-・、と]{1,2}[月火水木金土日]曜", tail):
        return 3
    if re.match(r"日?(を中日|を挟む)", tail):
        return 3
    if "とその前日" in tail[:10] or "とその翌日" in tail[:10]:
        return 2
    return 1


def _offset_from_tail(tail: str) -> int:
    """基準日より前から始まる場合の開始オフセット（日数）。"""
    if re.match(r"日?(を中日|を挟む)", tail):
        return -1
    if "とその前日" in tail[:10]:
        return -1
    return 0


# ---------------------------------------------------------------------------
# 展開: ルール -> 実日付
# ---------------------------------------------------------------------------
def expand(rule: dict, year: int) -> dict | None:
    k = rule["kind"]
    try:
        if k == "fixed":
            s = dt.date(year, rule["month"], min(rule["day"], _last_day(year, rule["month"])))
            return _occ(s, s + dt.timedelta(days=rule.get("span", 1) - 1), "exact")

        if k == "range":
            s = dt.date(year, rule["m1"], min(rule["d1"], _last_day(year, rule["m1"])))
            e = dt.date(year, rule["m2"], min(rule["d2"], _last_day(year, rule["m2"])))
            if e < s:
                e = dt.date(year + 1, rule["m2"], min(rule["d2"], _last_day(year + 1, rule["m2"])))
            return _occ(s, e, "exact")

        if k == "nth_wd":
            days = _weekdays_in(year, rule["month"], rule["wd"])
            if not days:
                return None
            nth = rule["nth"]
            s = days[-1] if nth == -1 else (days[nth - 1] if nth <= len(days) else days[-1])
            s = s + dt.timedelta(days=rule.get("offset", 0))
            return _occ(s, s + dt.timedelta(days=rule.get("span", 1) - 1), "exact")

        if k == "period_wd":
            lo, hi = PERIOD_RANGE[rule["period"]]
            days = _weekdays_in(year, rule["month"], rule["wd"], lo, hi)
            if not days:
                return None
            s = days[0]
            return _occ(s, s + dt.timedelta(days=rule.get("span", 1) - 1), "exact")

        if k == "period":
            lo, hi = PERIOD_RANGE[rule["period"]]
            hi = min(hi, _last_day(year, rule["month"]))
            return _occ(dt.date(year, rule["month"], lo),
                        dt.date(year, rule["month"], hi), "approx")

        if k == "period_range":
            lo, _ = PERIOD_RANGE[rule["p1"]]
            _, hi = PERIOD_RANGE[rule["p2"]]
            hi = min(hi, _last_day(year, rule["m2"]))
            s = dt.date(year, rule["m1"], lo)
            e = dt.date(year, rule["m2"], hi)
            if e < s:
                e = dt.date(year + 1, rule["m2"], hi)
            return _occ(s, e, "approx")

        if k == "month":
            return _occ(dt.date(year, rule["month"], 1),
                        dt.date(year, rule["month"], _last_day(year, rule["month"])), "approx")

        if k == "lunar":
            if not HAS_LUNAR:
                return None
            s = LunarDate(year, rule["month"], rule["day"]).to_solar_date()
            return _occ(s, s, "approx")

        if k == "lunar_period":
            if not HAS_LUNAR:
                return None
            s = LunarDate(year, rule["month"], 1).to_solar_date()
            return _occ(s, s + dt.timedelta(days=29), "approx")

    except (ValueError, IndexError, KeyError):
        return None
    return None


def _occ(s: dt.date, e: dt.date, precision: str) -> dict:
    if e < s:
        e = s
    if (e - s).days > 120:            # 通年イベントは除く
        e = s + dt.timedelta(days=120)
    return {"start": s.isoformat(), "end": e.isoformat(), "precision": precision}


def occurrences(raw: str, years: list[int]) -> tuple[list[dict], list[dict]]:
    """自然文から複数年ぶんのオカレンスを返す。 (occurrences, rules)"""
    rules = parse_schedule(raw)
    out = []
    for y in years:
        for r in rules:
            if r["kind"] == "irregular":
                continue
            o = expand(r, y)
            if o:
                out.append(o)
    # 重複除去 & 日付順
    seen, uniq = set(), []
    for o in sorted(out, key=lambda x: x["start"]):
        if o["start"] not in seen:
            seen.add(o["start"])
            uniq.append(o)
    return uniq, rules


def month_hint(rules: list[dict]) -> list[int]:
    """ルール群から開催月のリスト（カレンダー表示・月フィルタ用）。"""
    ms = set()
    for r in rules:
        for key in ("month", "m1", "m2"):
            if key in r and isinstance(r[key], int) and 1 <= r[key] <= 12:
                ms.add(r[key])
    return sorted(ms)


def season_tags(months: list[int]) -> list[str]:
    out = set()
    for m in months:
        if m in (3, 4, 5):
            out.add("spring")
        elif m in (6, 7, 8):
            out.add("summer")
        elif m in (9, 10, 11):
            out.add("autumn")
        else:
            out.add("winter")
    return sorted(out)


if __name__ == "__main__":            # 簡易セルフテスト
    samples = [
        "8月3日から6日", "8月第1金曜から3日間", "7月下旬の土曜日", "9月の敬老の日を含む土日月",
        "旧暦6月15日", "5月中旬", "10月第2土曜日を中日とする金曜、土曜、日曜日",
        "2月15日から17日", "12月2日・3日", "4月中旬 - 5月初旬", "72年間隔・次回は2075年",
        "7月20日-22日", "8月最終土曜日", "1月15・16日と12月15・16日", "毎年5月の第3日曜日とその前日の2日間",
    ]
    for s in samples:
        occ, rl = occurrences(s, [2026])
        print(f"{s:45s} -> {[ (o['start'], o['end'], o['precision']) for o in occ ]}")

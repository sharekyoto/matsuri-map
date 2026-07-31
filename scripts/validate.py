#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data/festivals.json の健全性チェック。
GitHub Actions では収集のあとにこれを走らせ、明らかにおかしい生成物が
公開されるのを防ぐ。異常があれば終了コード 1。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "festivals.json")

MIN_COUNT = 100          # これを下回ったら収集が失敗しているとみなす
JP_BBOX = (20.0, 46.5, 122.0, 154.0)   # lat_min, lat_max, lon_min, lon_max


def main() -> int:
    if not os.path.exists(PATH):
        print("NG: data/festivals.json がありません")
        return 1

    with open(PATH, encoding="utf-8") as f:
        d = json.load(f)

    fs = d.get("festivals", [])
    errs, warns = [], []

    if len(fs) < MIN_COUNT:
        errs.append(f"件数が少なすぎます: {len(fs)} < {MIN_COUNT}")

    if not d.get("taxonomy", {}).get("tags"):
        errs.append("taxonomy が空です")

    known_tags = {t["id"] for t in d.get("taxonomy", {}).get("tags", [])}
    ids = Counter()
    no_occ = bad_geo = bad_tag = 0
    today = dt.date.today()

    for r in fs:
        ids[r.get("id")] += 1
        lat, lng = r.get("lat"), r.get("lng")
        if lat is None or lng is None or not (
                JP_BBOX[0] <= lat <= JP_BBOX[1] and JP_BBOX[2] <= lng <= JP_BBOX[3]):
            bad_geo += 1
        if not r.get("occ"):
            no_occ += 1
        else:
            last = max(o["end"] for o in r["occ"])
            if dt.date.fromisoformat(last) < today:
                no_occ += 1
        if any(t not in known_tags for t in r.get("tags", [])):
            bad_tag += 1

    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        warns.append(f"ID の重複 {len(dup)} 件: {dup[:5]}")
    if bad_geo:
        errs.append(f"座標が日本国外／欠損: {bad_geo} 件")
    if bad_tag:
        errs.append(f"未知のタグを持つレコード: {bad_tag} 件")
    if no_occ > len(fs) * 0.2:
        errs.append(f"今後の開催日が無いレコードが多すぎます: {no_occ}/{len(fs)}")
    elif no_occ:
        warns.append(f"今後の開催日が無いレコード: {no_occ} 件")

    prefs = {r.get("pref") for r in fs if r.get("pref")}
    if len(prefs) < 40:
        warns.append(f"収録都道府県が {len(prefs)} しかありません")

    print(f"件数: {len(fs)}   都道府県: {len(prefs)}   "
          f"50年以上: {sum(1 for r in fs if r.get('over50'))}")
    print("確度:", dict(Counter(r.get("ageConf") for r in fs)))
    print(f"サイズ: {os.path.getsize(PATH)/1024:.0f} KB")

    for w in warns:
        print("WARN:", w)
    for e in errs:
        print("NG:", e)
    print("OK" if not errs else "検証に失敗しました")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())

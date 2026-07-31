#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data/festivals.json の健全性チェック（と軽い自動修復）。

方針: できるだけ止めない。直せるものは直して公開まで通す。
  - 地図に置けないレコードは黙って除外する
  - 件数が少なくても「警告」にとどめ、公開は止めない
  - 止めるのは「実質からっぽ」で公開する意味が無いときだけ

自動で除外するもの:
  - 座標が無い／日本の範囲外のレコード
  - ID が重複しているレコード
  - 今後の開催予定が1件も無いレコード
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "festivals.json")

TARGET_COUNT = 1000      # 目標件数。下回っても止めないが警告を出す
HARD_MIN = 20            # これ未満は「収集が壊れた」とみなして止める
JP_BBOX = (20.0, 46.5, 122.0, 154.0)


def in_japan(lat, lng) -> bool:
    return (lat is not None and lng is not None
            and JP_BBOX[0] <= lat <= JP_BBOX[1]
            and JP_BBOX[2] <= lng <= JP_BBOX[3])


def main() -> int:
    if not os.path.exists(PATH):
        print("NG: data/festivals.json がありません")
        return 1

    with open(PATH, encoding="utf-8") as f:
        d = json.load(f)

    fs = d.get("festivals", [])
    before = len(fs)
    errs, warns, dropped = [], [], Counter()
    today = dt.date.today()

    known_tags = {t["id"] for t in d.get("taxonomy", {}).get("tags", [])}
    if not known_tags:
        errs.append("taxonomy が空です")

    kept, seen_ids = [], set()
    for r in fs:
        if not in_japan(r.get("lat"), r.get("lng")):
            dropped["座標が無い／日本国外"] += 1
            continue
        rid = r.get("id")
        if rid in seen_ids:
            dropped["IDの重複"] += 1
            continue
        seen_ids.add(rid)
        occ = r.get("occ") or []
        if not occ or max(o["end"] for o in occ) < today.isoformat():
            dropped["今後の開催予定が無い"] += 1
            continue
        if known_tags:
            bad = [t for t in r.get("tags", []) if t not in known_tags]
            if bad:
                r["tags"] = [t for t in r["tags"] if t in known_tags]
        kept.append(r)

    total_dropped = sum(dropped.values())

    # 止めるのは実質からっぽのときだけ
    if len(kept) < HARD_MIN:
        errs.append("有効件数が %d 件しかありません（収集が壊れています）" % len(kept))

    if len(kept) < TARGET_COUNT:
        warns.append("目標の %d 件に届いていません（現在 %d 件）"
                     % (TARGET_COUNT, len(kept)))

    prefs = {r.get("pref") for r in kept if r.get("pref")}
    if len(prefs) < 40:
        warns.append("収録都道府県が %d しかありません" % len(prefs))

    print("入力 {:,} 件 → 有効 {:,} 件".format(before, len(kept)))
    for reason, n in dropped.most_common():
        print("  除外: {}: {} 件".format(reason, n))
    print("都道府県: {}/47   50年以上: {:,} 件".format(
        len(prefs), sum(1 for r in kept if r.get("over50"))))
    print("確度:", dict(Counter(r.get("ageConf") for r in kept)))
    prefc = Counter(r.get("pref") for r in kept if r.get("pref"))
    thin = sorted(prefc.items(), key=lambda kv: kv[1])[:10]
    if thin:
        print("収録が少ない県:", "、".join("%s(%d)" % (p, n) for p, n in thin))

    for w in dict.fromkeys(warns):
        print("WARN:", w)

    if errs:
        for e in errs:
            print("NG:", e)
        print("検証に失敗しました（データは書き換えません）")
        return 1

    if total_dropped:
        d["festivals"] = kept
        d["count"] = len(kept)
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        print("{} 件を除外して書き戻しました".format(total_dropped))

    print("サイズ: {:,.0f} KB".format(os.path.getsize(PATH) / 1024))
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新のたびに「何がどう変わったか」を Markdown で出す。

GitHub Actions の実行サマリに表示されるので、毎月これを眺めるだけで
データが健全に育っているか／壊れていないかが分かる。

    python3 scripts/report.py            # 前回との差分
    python3 scripts/report.py --only-now # 今回ぶんの統計だけ
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = os.path.join(ROOT, "data", "festivals.json")
PREV = os.path.join(ROOT, "data", "festivals.prev.json")


def load(p):
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    now = load(NOW)
    if not now:
        print("festivals.json がありません。")
        return 0
    prev = None if "--only-now" in sys.argv else load(PREV)

    fs = now["festivals"]
    print("## 祭りデータ更新レポート\n")

    # --- 件数 -------------------------------------------------------------
    if prev:
        pf = prev["festivals"]
        d = len(fs) - len(pf)
        sign = f"+{d}" if d > 0 else str(d)
        print(f"**収録件数: {len(fs):,} 件**（前回 {len(pf):,} 件 / {sign}）\n")

        pn = {f["name"] for f in pf}
        nn = {f["name"] for f in fs}
        added = sorted(nn - pn)
        removed = sorted(pn - nn)
        if added:
            print(f"<details><summary>新しく入った祭り {len(added)} 件</summary>\n")
            print("\n".join(f"- {n}" for n in added[:200]))
            if len(added) > 200:
                print(f"\n…ほか {len(added)-200} 件")
            print("\n</details>\n")
        if removed:
            print(f"<details><summary>消えた祭り {len(removed)} 件（記事削除・改名など）</summary>\n")
            print("\n".join(f"- {n}" for n in removed[:200]))
            if len(removed) > 200:
                print(f"\n…ほか {len(removed)-200} 件")
            print("\n</details>\n")

        # 件数が3割以上減っていたら警告
        if len(pf) and len(fs) < len(pf) * 0.7:
            print("> ⚠️ **件数が大きく減っています。収集元の仕様変更を疑ってください。**\n")
    else:
        print(f"**収録件数: {len(fs):,} 件**\n")

    # --- 内訳 -------------------------------------------------------------
    prefs = Counter(f.get("pref") for f in fs if f.get("pref"))
    conf = Counter(f.get("ageConf") for f in fs)
    print(f"- 都道府県: {len(prefs)} / 47")
    print(f"- 50年以上: {sum(1 for f in fs if f.get('over50')):,} 件")
    print(f"- 創始年の確度: 確実 {conf.get('exact',0):,} / 推定 {conf.get('estimated',0):,} / "
          f"文化財指定 {conf.get('proxy',0):,} / 推測 {conf.get('assumed',0):,}")
    print(f"- ファイルサイズ: {os.path.getsize(NOW)/1024:,.0f} KB\n")

    # --- タグ分布 ---------------------------------------------------------
    tax = {t["id"]: t["label"] for t in now.get("taxonomy", {}).get("tags", [])}
    tags = Counter(t for f in fs for t in f.get("tags", []))
    print("<details><summary>タグ別の件数</summary>\n")
    print("| タグ | 件数 |")
    print("|---|---:|")
    for tid, n in tags.most_common():
        if tid in ("spring", "summer", "autumn", "winter"):
            continue
        print(f"| {tax.get(tid, tid)} | {n:,} |")
    print("\n</details>\n")

    # --- 都道府県ワースト（収集の穴を見つける）----------------------------
    thin = sorted(prefs.items(), key=lambda kv: kv[1])[:8]
    print("収録が少ない県（手入力で補うと効果的）: "
          + "、".join(f"{p}({n})" for p, n in thin) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
データを埋め込んだ単体HTML（matsuri-map-standalone.html）を作る。

ブラウザで file:// を開くと fetch() が CORS で失敗するため、
「ダブルクリックで開くだけ」で使いたい場合はこちらを使う。
サーバー不要・GitHub 不要。地図タイルと Leaflet だけはネット接続が要る。

    python3 scripts/standalone.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

src = os.path.join(ROOT, "data", "festivals.json")
if not os.path.exists(src):
    src = os.path.join(ROOT, "data", "seed.json")
if not os.path.exists(src):
    sys.exit("data/festivals.json も data/seed.json もありません。"
             "先に scripts/seed.py か scripts/build.py を実行してください。")

with open(src, encoding="utf-8") as f:
    data = json.load(f)
with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
    html = f.read()

# boot() の fetch を、埋め込んだデータを使うものに差し替える
embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
html = html.replace(
    '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>',
    f'<script id="embedded-data" type="application/json">{embedded}</script>\n'
    '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>',
    1)

html, n = re.subn(
    r'  for\(const url of \["data/festivals\.json","data/seed\.json"\]\)\{(?:.|\n)*?\n  \}\n',
    '  try{ data = JSON.parse(document.getElementById("embedded-data").textContent); }'
    'catch(e){}\n',
    html, count=1)
if n != 1:
    sys.exit("index.html の boot() の形が想定と違います。standalone.py を更新してください。")

# 単体ファイルでは Service Worker を登録しない
html = html.replace('if("serviceWorker" in navigator){', 'if(false){')
html = html.replace('<link rel="manifest" href="manifest.webmanifest">', "")

out = os.path.join(ROOT, "matsuri-map-standalone.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"{out}  {os.path.getsize(out)/1024:.0f} KB  "
      f"（{data.get('count', len(data['festivals']))} 件を埋め込み）")

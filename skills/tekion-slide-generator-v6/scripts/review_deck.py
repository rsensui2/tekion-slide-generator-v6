#!/usr/bin/env python3
"""TEKION Slide Generator v6 - スライドダッシュボード（Phase 8 / ハブ画面）

manifest の確定版スライドを校正刷りとして1枚の HTML に並べ、ブラウザで開く。
スライドごとに: 修正指示の記入と即時送信 / バージョンタイムラインでの比較・確定切替 /
表示中バージョンの PNG ダウンロード。ヘッダーからデッキ全体の PPTX/PDF ダウンロード。

2つのモード:
  --serve（推奨）: ローカルサーバで開く。
      - バージョンの「この版を確定にする」→ manifest 即反映（サーバ継続）
      - 「⤓ PPTX / ⤓ PDF」→ その時点の確定版でデッキを書き出してダウンロード（サーバ継続）
      - 修正指示の送信（1枚ずつ or まとめて）→ <session-dir>/slide_feedback.json に
        保存してプロセス終了。バックグラウンド実行しておけば、送信 = Claude への修正依頼。
  （デフォルト）: 静的 review.html を生成するだけ。file:// でも閲覧・比較・PNG保存は動く
      （確定切替・デッキ書き出しは不可、送信はダウンロードにフォールバック）。

使い方:
    python3 review_deck.py --session-dir <SESSION_DIR> --serve          # 推奨
    python3 review_deck.py --session-dir <SESSION_DIR> --open           # 静的モード

exit code: 0 = フィードバック受信（または静的生成完了）, 2 = --serve タイムアウト
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest_utils import (load_manifest, ordered_bases, read_session_status,
                            locked_update, save_manifest, update_entry)

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  /* TEKION Group デザイン言語（tekion-web / DESIGN.md 準拠）
     主色はオレンジ #FF5A00・グラデ #ff5a00→#ff9a00。赤 #d93b31 は「赤入れ」専用の意味色 */
  :root {
    --paper: #ffffff;        /* 紙: カード面 */
    --desk: #f7f4f0;         /* 机: ウォームなページ背景 */
    --ink-dark: #171717;     /* TEKION ダーク帯 */
    --text: #111827;         /* 本文 */
    --sub: #4b5563;          /* 補助 */
    --muted: #9aa1ad;        /* さらに弱い補助 */
    --line: #ece4db;         /* ウォームな罫線 */
    --orange: #ff5a00;       /* TEKION プライマリ */
    --orange-2: #ff9a00;
    --orange-light: #ff8a3c;
    --orange-dark: #e05000;
    --orange-bg: #fff5f0;
    --orange-line: #ffd9c4;
    --grad: linear-gradient(95deg, #ff5a00, #ff9a00);
    --red: #d93b31;          /* 赤入れ = 修正指示の印 */
    --red-tint: #fdeeec;
    --shadow-sm: 0 1px 2px rgba(23,23,23,.04), 0 6px 18px rgba(23,23,23,.05);
    --shadow-lift: 0 18px 44px -26px rgba(18,24,52,.30);
    color-scheme: light;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--desk); color: var(--text);
    font-family: "Inter", "Hiragino Sans", "Noto Sans JP", sans-serif;
    font-feature-settings: "palt";
  }
  .mono { font-family: "Space Grotesk", "Inter", "Hiragino Sans", sans-serif;
          font-variant-numeric: tabular-nums; }
  html { scroll-behavior: smooth; }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }

  /* eyebrow: 短いグラデ線 + 英語 uppercase ラベル（TEKIONらしさの核） */
  .eyebrow {
    display: inline-flex; align-items: center; gap: 8px;
    font-family: "Space Grotesk", "Inter", sans-serif;
    font-size: 10px; font-weight: 700; letter-spacing: .22em;
    text-transform: uppercase; color: var(--orange);
  }
  .eyebrow::before { content: ""; width: 22px; height: 2px; border-radius: 1px;
                     background: var(--grad); flex: 0 0 auto; }

  header.top {
    position: sticky; top: 0; z-index: 20;
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 14px 28px 12px;
    background: rgba(255,255,255,.88); backdrop-filter: blur(14px) saturate(160%);
    border-bottom: 1px solid var(--line);
  }
  header.top::before {  /* 天面のブランドグラデーションライン */
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: var(--grad);
  }
  a.brand { display: flex; align-items: center; gap: 14px;
            text-decoration: none; color: inherit; }
  .brandlogo { height: 22px; width: auto; display: block; }
  .bdiv { width: 1px; height: 28px; background: var(--line); }
  .bcol { display: flex; flex-direction: column; gap: 3px; }
  .bcol h1 { margin: 0; font-size: 14.5px; font-weight: 800; letter-spacing: .02em; line-height: 1.2; }
  .bcol h1 .session { color: var(--muted); font-weight: 500; margin-left: .6em;
                      font-size: 11px; letter-spacing: .04em; }
  a.brand[href]:hover h1 { color: var(--orange-dark); }

  .tally { margin-left: auto; display: flex; align-items: center; gap: 20px; padding-right: 4px; }
  .tally .item { display: flex; align-items: baseline; gap: 7px; }
  .tally .n { font-family: "Space Grotesk", sans-serif; font-size: 24px; font-weight: 700;
              font-variant-numeric: tabular-nums; line-height: 1;
              background: var(--grad); -webkit-background-clip: text;
              background-clip: text; color: transparent; }
  .tally .label { font-size: 10px; color: var(--sub); letter-spacing: .18em; font-weight: 600; }
  .tally .ink .n { background: none; -webkit-background-clip: initial;
                   background-clip: initial; color: var(--red); }

  button.export {
    background: var(--grad); color: #fff; border: 0;
    padding: 11px 24px; border-radius: 999px;
    font-size: 13px; font-weight: 700; letter-spacing: .04em; cursor: pointer;
    font-family: inherit; box-shadow: 0 14px 30px -12px rgba(255,90,0,.55);
    transition: transform .15s, box-shadow .15s;
  }
  button.export:hover { transform: translateY(-2px);
                        box-shadow: 0 18px 34px -12px rgba(255,90,0,.6); }
  button.export:disabled { background: #c8cdd5; box-shadow: none; transform: none; cursor: default; }
  a.hdr-tool, label.hdr-tool {
    display: inline-flex; align-items: center; gap: 6px;
    border: 1px solid var(--line); background: var(--paper); color: var(--text);
    font-size: 12px; font-weight: 600; padding: 8px 16px; border-radius: 999px;
    text-decoration: none; cursor: pointer;
    transition: border-color .15s, color .15s, box-shadow .15s;
  }
  a.hdr-tool:hover, label.hdr-tool:hover {
    border-color: rgba(255,90,0,.45); color: var(--orange-dark);
    box-shadow: 0 6px 16px -8px rgba(255,90,0,.35);
  }
  a.hdr-tool.busy { pointer-events: none; opacity: .5; }
  button:focus-visible, textarea:focus-visible, .rail a:focus-visible, a:focus-visible,
  a.tool:focus-visible, .vnode:focus-visible {
    outline: 2px solid var(--orange); outline-offset: 2px;
  }

  /* ページモード: スタート画面ではデッキ操作を隠す */
  .page-home .tally, .page-home #dl-pptx, .page-home #dl-pdf,
  .page-home #submit-btn, .page-home footer.hint { display: none; }
  .page-home .wrap { display: block; max-width: none; }
  .page-home nav.rail { display: none; }
  .page-home main { display: block; padding: 0 0 32px; }

  #done-banner {
    display: none; align-items: center; gap: 10px;
    margin: 16px 28px 0; padding: 14px 18px;
    background: var(--orange-bg); border: 1px solid var(--orange-line); border-radius: 12px;
    color: var(--orange-dark); font-size: 14px; font-weight: 600;
  }
  #done-banner.show { display: flex; }
  .pending-banner {
    display: flex; align-items: center; gap: 10px;
    margin: 16px 28px 0; padding: 13px 18px;
    background: #fff8e7; border: 1px solid #f1cf78; border-radius: 12px;
    color: #7a4b00; font-size: 13.5px; font-weight: 700;
  }

  .wrap { display: grid; grid-template-columns: 190px minmax(0, 1fr);
          max-width: 1560px; margin: 0 auto; }

  /* 索引レール: 現在地がスクロールに連動してハイライトされる */
  nav.rail {
    position: sticky; top: 62px; align-self: start;
    max-height: calc(100vh - 62px); overflow-y: auto;
    padding: 24px 12px 24px 24px;
    display: flex; flex-direction: column; gap: 12px;
  }
  .rail a { display: block; text-decoration: none; color: inherit; position: relative;
            border-radius: 8px; }
  .rail img {
    display: block; width: 100%; border-radius: 6px;
    border: 1px solid var(--line); box-shadow: 0 1px 3px rgba(23,23,23,.04);
  }
  .rail a:hover img { border-color: var(--orange-light); }
  .rail a.active img { box-shadow: 0 0 0 2.5px var(--orange); }
  .rail .tag {
    position: absolute; top: 6px; left: 6px;
    font-family: "Space Grotesk", sans-serif;
    font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
    background: #fff; color: var(--sub); letter-spacing: .08em;
    border: 1px solid var(--line);
  }
  .rail a.active .tag { background: var(--orange); color: #fff; border-color: var(--orange); }
  .rail a.has-ink img { border-color: var(--red); border-width: 2px; }
  .rail a.has-ink .tag { background: var(--red); color: #fff; border-color: var(--red); }

  main { padding: 24px 28px 80px; display: flex; flex-direction: column; gap: 36px; }

  article.proof {
    background: var(--paper); border: 1px solid var(--line); border-radius: 18px;
    box-shadow: var(--shadow-sm);
    overflow: hidden; transition: border-color .2s, box-shadow .25s;
    scroll-margin-top: 84px; /* 固定ヘッダーに隠れないオフセット */
  }
  article.proof:hover { box-shadow: var(--shadow-lift); }
  article.proof.has-ink { border-color: var(--red); }

  .proof .head {
    display: flex; align-items: center; gap: 14px; padding: 16px 24px 12px;
  }
  .headtools { display: flex; gap: 6px; margin-left: 14px; }
  .hbtn {
    width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center;
    border: 1px solid var(--line); background: var(--paper); color: var(--sub);
    border-radius: 8px; cursor: pointer; font-size: 13px; font-family: inherit;
    padding: 0; line-height: 1; transition: border-color .15s, color .15s;
  }
  .hbtn:hover { border-color: rgba(255,90,0,.45); color: var(--orange-dark); }
  .hbtn.del:hover { border-color: var(--red); color: var(--red); background: var(--red-tint); }
  .rail a[draggable="true"] { cursor: grab; }
  .rail a.dragging { opacity: .35; }
  #undo-toast {
    display: none; position: fixed; left: 50%; transform: translateX(-50%);
    bottom: 24px; z-index: 60; align-items: center; gap: 14px;
    background: var(--ink-dark); color: #fff; border-radius: 999px;
    padding: 10px 10px 10px 22px; font-size: 13px; font-weight: 600;
    box-shadow: 0 20px 40px -12px rgba(0,0,0,.5);
  }
  #undo-toast.show { display: flex; }
  #undo-toast button {
    border: 0; background: var(--grad); color: #fff; font-weight: 700;
    border-radius: 999px; padding: 8px 18px; cursor: pointer;
    font-family: inherit; font-size: 12.5px;
  }
  .proof .ord {
    font-family: "Space Grotesk", sans-serif;
    font-size: 24px; font-weight: 600; color: #ddd2c5;
    font-variant-numeric: tabular-nums; line-height: 1;
  }
  .proof .id { font-family: "Space Grotesk", "Inter", "Hiragino Sans", sans-serif;
               font-size: 12.5px; color: var(--sub); font-weight: 600; letter-spacing: .04em; }
  .proof .state { margin-left: auto; font-size: 10px; letter-spacing: .2em;
                  color: var(--muted); font-weight: 700; }
  .proof.has-ink .state { color: var(--red); }

  /* 本体: メイン画像 + バージョンタイムライン（右カラム） */
  .proof .body { display: grid; grid-template-columns: minmax(0,1fr); gap: 16px; padding: 0 24px; }
  .proof .body.with-versions { grid-template-columns: minmax(0,1fr) 240px; }
  .proof img.slide {
    display: block; width: 100%; height: auto; border-radius: 10px;
    border: 1px solid var(--line);
  }
  .proof .maincol .tools { display: flex; align-items: center; gap: 8px; padding-top: 10px; }
  .tool {
    display: inline-flex; align-items: center; gap: 6px;
    border: 1px solid var(--line); background: var(--paper); color: var(--text);
    font-size: 12px; font-weight: 600; padding: 6px 16px; border-radius: 999px;
    cursor: pointer; font-family: inherit; text-decoration: none;
    transition: border-color .15s, color .15s;
  }
  .tool:hover { border-color: rgba(255,90,0,.45); color: var(--orange-dark); }
  .tool[hidden] { display: none; }
  .viewing-label { font-size: 12px; color: var(--sub); font-weight: 600; margin-right: auto; }
  .viewing-label b { color: var(--orange-dark); }

  /* プロンプトライブラリ: スライドを作った生成プロンプトの表示 */
  .promptbox { margin-top: 12px; border: 1px solid var(--line); border-radius: 12px;
               background: #faf8f5; overflow: hidden; }
  .promptbox .pbar { display: flex; align-items: center; justify-content: space-between;
                     padding: 10px 16px; border-bottom: 1px solid var(--line); }
  .promptbox .ptitle { font-family: "Space Grotesk", sans-serif;
                       font-size: 10px; font-weight: 700; letter-spacing: .2em;
                       text-transform: uppercase; color: var(--orange); }
  .promptbox .pcopy { border: 1px solid var(--line); background: var(--paper);
                      color: var(--sub); font-size: 11.5px; font-weight: 600;
                      padding: 5px 14px; border-radius: 999px; cursor: pointer;
                      font-family: inherit; transition: border-color .15s, color .15s; }
  .promptbox .pcopy:hover { border-color: rgba(255,90,0,.45); color: var(--orange-dark); }
  .promptbox pre { margin: 0; padding: 14px 16px; max-height: 320px; overflow: auto;
                   font-family: "SF Mono", Menlo, "Hiragino Sans", monospace;
                   font-size: 12px; line-height: 1.8; color: var(--sub);
                   white-space: pre-wrap; word-break: break-word; }

  /* バージョンタイムライン: 縦の接続線で「派生」を可視化 */
  aside.vtree { position: relative; padding-left: 18px; }
  aside.vtree::before {
    content: ""; position: absolute; left: 5px; top: 14px; bottom: 14px;
    width: 2px; background: var(--line); border-radius: 1px;
  }
  .vtree .vtitle { font-family: "Space Grotesk", sans-serif;
                   font-size: 10px; letter-spacing: .2em; color: var(--muted);
                   font-weight: 700; margin: 0 0 10px; }
  .vnode { position: relative; margin-bottom: 14px; cursor: pointer; border: 0;
           background: none; padding: 0; width: 100%; text-align: left; font-family: inherit; }
  .vnode::before {  /* タイムラインの節 */
    content: ""; position: absolute; left: -17.5px; top: 12px;
    width: 9px; height: 9px; border-radius: 50%;
    background: var(--paper); border: 2px solid var(--muted);
  }
  .vnode.current::before { background: var(--orange); border-color: var(--orange); }
  .vnode img { display: block; width: 100%; border-radius: 7px;
               border: 1px solid var(--line); transition: box-shadow .15s; }
  .vnode:hover img { border-color: var(--orange-light); }
  .vnode.current img { box-shadow: 0 0 0 3px var(--orange); border-color: var(--orange); }
  .vnode .vmeta { display: flex; align-items: center; gap: 6px; padding: 5px 2px 0; }
  .vnode .vname { font-family: "Space Grotesk", sans-serif;
                  font-size: 11px; font-weight: 700; color: var(--sub); }
  .vnode.current .vname { color: var(--orange-dark); }
  .vnode .badge {
    font-size: 9px; font-weight: 700; letter-spacing: .1em;
    padding: 2px 8px; border-radius: 999px; visibility: hidden;
    background: var(--grad); color: #fff;
  }
  .vnode.current .badge { visibility: visible; }
  .vnode .vtime { margin-left: auto; font-family: "Space Grotesk", sans-serif;
                  font-size: 10px; color: var(--muted); font-variant-numeric: tabular-nums; }
  #ok-toast {
    display: none; position: fixed; left: 50%; transform: translateX(-50%);
    bottom: 24px; z-index: 55;
    background: var(--ink-dark); color: #fff; border-radius: 999px;
    padding: 11px 24px; font-size: 13px; font-weight: 600;
    box-shadow: 0 20px 40px -12px rgba(0,0,0,.5);
  }
  #ok-toast.show { display: block; }

  /* 修正指示スリップ + 送信 */
  /* 赤入れ欄: 常に赤ペンの体裁（書いた文字も赤） */
  .slip { display: grid; grid-template-columns: 96px minmax(0,1fr) auto;
          margin: 16px 24px 24px; border: 1.5px solid #f0c9c5; border-radius: 12px;
          background: var(--paper); overflow: hidden;
          border-left: 4px solid var(--red); transition: border-color .2s, background .2s; }
  .slip:focus-within { border-color: var(--red); box-shadow: 0 0 0 3px #d93b3122; }
  .proof.has-ink .slip { border-color: var(--red); background: var(--red-tint); }
  .slip .label {
    display: flex; flex-direction: column; justify-content: center; gap: 3px;
    padding: 12px 0 12px 18px; border-right: 1px solid #f0c9c5;
  }
  .slip .label .kanji { font-size: 14px; font-weight: 700; letter-spacing: .28em; color: var(--red); }
  .slip .label .sub { font-family: "Space Grotesk", sans-serif;
                      font-size: 9px; color: #e0958f; letter-spacing: .14em; }
  .slip .slipmain { display: flex; flex-direction: column; min-width: 0; }
  .slip textarea {
    width: 100%; min-height: 68px; flex: 1; resize: vertical; border: 0; background: transparent;
    color: var(--red); padding: 15px 18px 8px; font-size: 15px; line-height: 1.75;
    font-family: inherit; font-weight: 600;
  }
  .slip textarea::placeholder { color: #d8a5a1; font-weight: 400; }
  .slipopts { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
              padding: 0 18px 12px; }
  .rebuild-opt {
    display: flex; align-items: center; gap: 7px;
    font-size: 12px; font-weight: 600; color: #c08d88;
    cursor: pointer; user-select: none; margin-left: auto;
  }
  .rebuild-opt input { accent-color: var(--red); width: 14px; height: 14px; margin: 0;
                       cursor: pointer; }
  .rebuild-opt:has(input:checked) { color: var(--red); }
  .attach-btn {
    display: inline-flex; align-items: center; gap: 5px;
    border: 1px dashed #e0b3ae; border-radius: 999px; padding: 5px 14px;
    font-size: 12px; font-weight: 600; color: #c08d88; cursor: pointer;
    user-select: none; transition: border-color .15s, color .15s;
  }
  .attach-btn:hover { border-color: var(--red); color: var(--red); }
  .attach-chips { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .attach-chips .chip { position: relative; display: inline-block; line-height: 0; }
  .attach-chips .chip img { height: 40px; width: auto; max-width: 88px; object-fit: cover;
                            border-radius: 6px; border: 1px solid #e0b3ae; }
  .attach-chips .chip button {
    position: absolute; top: -6px; right: -6px; width: 17px; height: 17px;
    border-radius: 50%; border: 0; background: var(--red); color: #fff;
    font-size: 11px; line-height: 1; cursor: pointer; padding: 0;
  }
  .slip.att-dragover { border-color: var(--red); box-shadow: 0 0 0 3px #d93b3133;
                       background: var(--red-tint); }
  /* 全体指示（デッキ先頭） */
  .gslip { margin: 0; }
  .gslip textarea { min-height: 46px; }
  .slip .send {
    align-self: stretch; display: flex; align-items: center; justify-content: center;
    border: 0; border-left: 1px solid #f0c9c5;
    background: transparent; color: var(--red); cursor: pointer;
    width: 64px; font-size: 22px; font-family: inherit; line-height: 1; opacity: .55;
  }
  .slip .send:hover { background: var(--red); color: #fff; }
  .proof.has-ink .slip .send { opacity: 1; }
  .proof.has-ink .slip .send:hover { background: var(--red); color: #fff; }

  #dead-overlay {
    display: none; position: fixed; inset: 0; z-index: 100;
    background: rgba(23,23,23,.88); backdrop-filter: blur(4px) grayscale(1);
    align-items: center; justify-content: center;
  }
  #dead-overlay.show { display: flex; }
  #dead-overlay .box {
    background: var(--paper); border-radius: 20px; padding: 36px 44px;
    max-width: 460px; text-align: center; box-shadow: 0 20px 60px #0009;
    border-top: 3px solid var(--orange);
  }
  #dead-overlay h2 { margin: 0 0 10px; font-size: 18px; font-weight: 800; }
  #dead-overlay p { margin: 0; color: var(--sub); font-size: 13.5px; line-height: 1.9; }

  footer.hint { text-align: center; color: var(--muted); font-size: 12.5px;
                padding: 0 24px 48px; letter-spacing: .03em; }

  /* 生成実況 */
  .genprog { font-size: 12.5px; font-weight: 700; color: var(--orange-dark);
             background: var(--orange-bg); border: 1px solid var(--orange-line);
             padding: 7px 16px; border-radius: 999px; }
  .genprog .dot { animation: blink 1.2s infinite; }
  @keyframes blink { 50% { opacity: .2; } }
  #reload-banner {
    display: none; position: fixed; right: 24px; bottom: 24px; z-index: 40;
    background: var(--grad); color: #fff; border: 0; border-radius: 999px;
    padding: 14px 24px; font-size: 13.5px; font-weight: 700; cursor: pointer;
    box-shadow: 0 14px 34px -12px rgba(255,90,0,.6); font-family: inherit;
  }
  #reload-banner.show { display: block; }

  /* ===== スタート画面（ハブ） ===== */
  .landing { max-width: 1180px; margin: 0 auto; padding: 0 24px; text-align: center; }

  /* ヒーロー: Ryoko バナー + でっかいプロダクト名 */
  .landing .hero {
    position: relative; overflow: hidden; border-radius: 28px;
    margin: 26px auto 0; text-align: left; min-height: 300px;
    background: linear-gradient(120deg, #fff, var(--orange-bg));
    border: 1px solid var(--line); box-shadow: var(--shadow-sm);
  }
  .landing .hero .herobg {
    position: absolute; inset: 0; width: 100%; height: 100%;
    object-fit: cover; object-position: right center; display: block;
  }
  .landing .hero .heroin {
    position: relative; z-index: 1; padding: 54px 56px 50px; max-width: 62%;
  }
  .landing .hero .eyebrow { font-size: 11px; }
  .landing .hero h2.htitle {
    margin: 14px 0 12px;
    font-family: "Space Grotesk", "Inter", sans-serif;
    font-size: clamp(34px, 4.8vw, 58px); font-weight: 700;
    letter-spacing: -0.01em; line-height: 1.04; color: var(--ink-dark);
  }
  .landing .hero .vbadge {
    display: inline-block; vertical-align: middle; transform: translateY(-.18em);
    margin-left: .35em; font-size: .34em; font-weight: 700; letter-spacing: .1em;
    background: var(--grad); color: #fff; padding: .3em .8em; border-radius: 999px;
  }
  .landing .hero p.lead { margin: 0; color: var(--sub); font-size: 14.5px;
                          line-height: 2; font-weight: 600; }
  .landing .hero .backlink {
    position: absolute; top: 24px; right: 24px; z-index: 2;
    display: inline-flex; align-items: center; gap: 6px;
    border: 1.5px solid var(--orange); border-radius: 999px;
    padding: 9px 18px; color: var(--orange-dark); text-decoration: none;
    font-size: 12.5px; font-weight: 700; background: rgba(255,255,255,.8);
    backdrop-filter: blur(6px);
    transition: background .15s, color .15s;
  }
  .landing .hero .backlink:hover { background: var(--grad); color: #fff;
                                   border-color: transparent; }
  @media (max-width: 900px) {
    .landing .hero .heroin { max-width: 100%; padding: 40px 28px 36px;
                             background: linear-gradient(90deg, #ffffffee, #ffffff88); }
  }

  /* セクション見出し: eyebrow + 和文見出しの2段（tekion-web の型） */
  .sec-head { display: flex; flex-direction: column; gap: 8px;
              margin: 52px 0 16px; text-align: left; }
  .sec-head .eyebrow { font-size: 11px; }
  .sec-head .eyebrow::before { width: 28px; }
  .sec-head h3 { margin: 0; font-size: 19px; font-weight: 800; letter-spacing: .02em; }

  .choices { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 28px; }
  .choice { display: block; border: 1px solid var(--line); border-radius: 20px;
            background: var(--paper); padding: 40px 28px; cursor: pointer;
            font-size: 16px; font-weight: 700; color: var(--text); line-height: 1.6;
            box-shadow: var(--shadow-sm);
            transition: transform .18s, border-color .18s, box-shadow .18s; }
  .choice small { display: block; margin-top: 10px; font-size: 12.5px; font-weight: 400;
                  color: var(--sub); line-height: 1.8; }
  .choice:hover { transform: translateY(-4px); border-color: rgba(255,90,0,.35);
                  box-shadow: 0 24px 48px -30px rgba(255,90,0,.45); }
  .choice.passive { cursor: default; }
  .choice.passive:hover { transform: none; border-color: var(--line);
                          box-shadow: var(--shadow-sm); }
  .choice .big { font-size: 28px; display: block; margin-bottom: 8px; }
  @media (max-width: 700px) { .choices { grid-template-columns: 1fr; } }

  .brandlogo-invert { filter: brightness(0) invert(1); }
  footer.sites {
    display: flex; align-items: center; justify-content: center; gap: 8px;
    padding: 0 24px 44px; font-size: 12px; color: var(--sub); flex-wrap: wrap;
  }
  footer.sites img { height: 14px; width: auto; opacity: .85; margin-right: 6px; }
  footer.sites a { color: var(--sub); text-decoration: none; font-weight: 600; }
  footer.sites a:hover { color: var(--orange-dark); text-decoration: underline; }
  footer.sites .sep { color: #d6cec4; }

  /* ステージ実況ヒーロー */
  .stage-hero {
    display: none; align-items: center; gap: 16px;
    max-width: 720px; margin: 20px auto 0; padding: 20px 26px;
    background: var(--paper); border: 1px solid var(--orange-line); border-radius: 18px;
    box-shadow: 0 14px 34px -20px rgba(255,90,0,.45);
  }
  .stage-hero.show { display: flex; }
  .stage-hero .pulse {
    width: 44px; height: 44px; border-radius: 50%; flex: 0 0 auto;
    background: radial-gradient(circle at 35% 35%, var(--orange-2), var(--orange));
    animation: heartbeat 1.6s ease-in-out infinite;
  }
  .stage-hero .stxt { display: flex; flex-direction: column; gap: 3px; text-align: left; }
  .stage-hero .stitle { font-size: 16px; font-weight: 800; color: var(--orange-dark); }
  .stage-hero .sdetail { font-size: 12.5px; color: var(--sub); }
  .stage-hero .sdots::after { content: ""; animation: dots 1.5s steps(4) infinite; }
  @keyframes heartbeat { 0%,100% { transform: scale(1); opacity: 1; }
                         50% { transform: scale(1.18); opacity: .75; } }
  @keyframes dots { 0% { content: ""; } 25% { content: "."; } 50% { content: ".."; } 75% { content: "..."; } }

  /* 生成待ちプレースホルダーカード */
  .ph-frame { position: relative; aspect-ratio: 16 / 9; border-radius: 10px;
              border: 1.5px dashed #e0d5c8; overflow: hidden;
              background: linear-gradient(110deg, #f3ede6 35%, #faf7f3 50%, #f3ede6 65%);
              background-size: 220% 100%; animation: shimmer 1.8s linear infinite; }
  @keyframes shimmer { to { background-position-x: -220%; } }
  .ph-frame .ph-label { position: absolute; inset: 0; display: flex; flex-direction: column;
                        align-items: center; justify-content: center; gap: 8px;
                        color: #b3a289; font-size: 13px; font-weight: 600; }
  .ph-frame .ph-num { font-family: "Space Grotesk", sans-serif;
                      font-size: 34px; font-weight: 500; color: #d9cdbd; }
  .proof.placeholder { border-style: dashed; box-shadow: none; }
  .proof.placeholder .state { color: #b3a289; }
  .rail .ph-thumb { aspect-ratio: 16 / 9; border-radius: 6px; border: 1.5px dashed #e0d5c8;
                    background: #f3ede6; }
  @media (prefers-reduced-motion: reduce) {
    .ph-frame, .stage-hero .pulse { animation: none; }
  }

  /* 最近のセッション（ハブの中核）: note 風カードグリッド — サムネイルが主役 */
  .recent { text-align: left; }
  .recent .rgrid { display: grid; gap: 18px;
                   grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
  .rcard { display: flex; flex-direction: column; align-items: stretch; text-align: left;
           padding: 0 0 16px; border: 1px solid var(--line); border-radius: 16px;
           background: var(--paper); cursor: pointer; overflow: hidden;
           color: inherit; text-decoration: none;
           box-shadow: var(--shadow-sm); font-family: inherit;
           transition: transform .18s, border-color .18s, box-shadow .18s; }
  .rcard:hover { transform: translateY(-4px); border-color: rgba(255,90,0,.35);
                 box-shadow: 0 24px 48px -30px rgba(255,90,0,.45); }
  .rcard:active { transform: translateY(-1px); }
  .rcard.busy { opacity: .55; pointer-events: none; }
  .rcard .rthumb { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; display: block;
                   border-bottom: 1px solid var(--line); background: #f3ede6; }
  .rcard .rtitle { font-size: 14px; font-weight: 700; line-height: 1.55;
                   padding: 12px 16px 4px;
                   display: -webkit-box; -webkit-line-clamp: 2;
                   -webkit-box-orient: vertical; overflow: hidden; }
  .rcard .rmeta { font-size: 11px; color: var(--sub); padding: 0 16px;
                  font-variant-numeric: tabular-nums;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* オンボーディング（スタート画面下部） */
  .onboard { text-align: left; }
  .onboard .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  .onboard .step { background: var(--paper); border: 1px solid var(--line); border-radius: 16px;
                   padding: 22px 22px 20px; box-shadow: var(--shadow-sm); }
  .onboard .step .n { display: block; font-family: "Space Grotesk", sans-serif;
                      font-size: 26px; font-weight: 700; line-height: 1;
                      background: var(--grad); -webkit-background-clip: text;
                      background-clip: text; color: transparent; margin-bottom: 10px; }
  .onboard .step .n::before { content: "0"; }
  .onboard .step h3 { margin: 0 0 6px; font-size: 14.5px; font-weight: 800; }
  .onboard .step p { margin: 0; font-size: 12.5px; color: var(--sub); line-height: 1.8; }
  .onboard .step p b { color: var(--text); }
  .brandhint { margin: 18px auto 0; padding: 16px 20px; text-align: left;
               background: var(--orange-bg); border: 1px solid var(--orange-line);
               border-radius: 16px;
               font-size: 13px; color: var(--text); line-height: 1.9; }
  .brandhint b { color: var(--orange-dark); }
  @media (max-width: 760px) { .onboard .steps { grid-template-columns: 1fr; } }

  /* Ryoko バナーボタン（スタート画面・横並び） */
  .choices.bannerbtns { grid-template-columns: 1fr 1fr; gap: 20px; }
  .choices.single { grid-template-columns: 1fr; max-width: 560px; margin-left: auto;
                    margin-right: auto; }
  @media (max-width: 760px) { .choices.bannerbtns { grid-template-columns: 1fr; } }
  .banner-btn { display: block; border: 0; padding: 0; background: none; cursor: pointer;
                border-radius: 20px; overflow: hidden; width: 100%; line-height: 0;
                box-shadow: 0 18px 44px -26px rgba(23,23,23,.35);
                transition: transform .18s, box-shadow .18s; }
  .banner-btn img { display: block; width: 100%; height: auto; }
  .banner-btn:hover { transform: translateY(-4px);
                      box-shadow: 0 24px 52px -26px rgba(255,90,0,.5); }
  .banner-btn:active { transform: translateY(0); }
  .create-hint { margin: 18px auto 0; max-width: 640px; padding: 16px 20px;
                 background: var(--orange-bg); border: 1px solid var(--orange-line);
                 border-radius: 14px; color: #8a3d00; font-size: 14px; font-weight: 600;
                 line-height: 1.8; }
  .create-hint[hidden] { display: none; }

  /* ファイル読み込み（ドラッグ&ドロップ） */
  label.hdr-tool { cursor: pointer; }
  #drop-overlay {
    display: none; position: fixed; inset: 0; z-index: 50;
    background: rgba(224,80,0,.55); backdrop-filter: blur(2px);
    align-items: flex-start; justify-content: center; padding-top: 10vh;
    pointer-events: none;  /* 視覚のみ。ドロップは下の要素（赤入れ欄 or 画面全体）が受ける */
  }
  #drop-overlay.show { display: flex; }
  #drop-overlay .box {
    border: 3px dashed #ffffffaa; border-radius: 20px; padding: 48px 64px;
    color: #fff; font-size: 18px; font-weight: 700; letter-spacing: .06em;
    text-align: center; line-height: 2;
  }
  #drop-overlay .box small { font-size: 12.5px; font-weight: 400; opacity: .9; }

  @media (prefers-reduced-motion: no-preference) {
    article.proof, .landing > * { animation: rise .5s cubic-bezier(0.16,1,0.3,1) both; }
    article.proof:nth-child(2), .landing > *:nth-child(2) { animation-delay: .05s; }
    article.proof:nth-child(3), .landing > *:nth-child(3) { animation-delay: .1s; }
    article.proof:nth-child(n+4), .landing > *:nth-child(n+4) { animation-delay: .15s; }
    @keyframes rise { from { opacity: 0; transform: translateY(10px); } }
  }
  @media (max-width: 1280px) {
    .wrap { grid-template-columns: 150px minmax(0, 1fr); }
    main { padding: 20px 18px 60px; }
    .proof .head { padding: 12px 16px 10px; }
    .proof figure, .proof .body { padding-left: 16px; padding-right: 16px; }
    .slip { margin: 12px 16px 16px; }
    .proof .ord { font-size: 20px; }
    .proof .body.with-versions { grid-template-columns: minmax(0,1fr) 190px; }
  }
  @media (max-width: 1100px) {
    .proof .body.with-versions { grid-template-columns: 1fr; }
    aside.vtree { display: flex; gap: 12px; padding-left: 0; overflow-x: auto; }
    aside.vtree::before { display: none; }
    .vtree .vtitle { display: none; }
    .vnode { width: 200px; flex: 0 0 auto; margin-bottom: 4px; }
    .vnode::before { display: none; }
  }
  @media (max-width: 860px) {
    .wrap { grid-template-columns: 1fr; }
    nav.rail { display: none; }
    .tally .label { display: none; }
    .slip { grid-template-columns: minmax(0,1fr) auto; }
    .slip .label { display: none; }
    .landing .hero { padding: 40px 28px 36px; border-radius: 20px; }
    .landing .hero .backlink { position: static; margin-bottom: 18px; }
  }
</style>
</head>
<body class="__PAGE_CLASS__">
<header class="top">
  <a class="brand" id="brand-link" href="__HOME_PATH__" title="トップ画面へ">
__BRAND_TOP__    <span class="bcol">
      <span class="eyebrow">Slide Dashboard</span>
      <h1>スライドダッシュボード<span class="session mono">__SESSION_NAME__</span></h1>
    </span>
  </a>
  <span class="genprog mono" id="gen-progress" hidden></span>
  <div class="tally">
    <span class="item ok"><span class="n" id="n-ok">__COUNT__</span><span class="label">校了</span></span>
    <span class="item ink"><span class="n" id="n-ink">0</span><span class="label">要修正</span></span>
  </div>
  <label class="hdr-tool" id="import-btn">＋ 読み込み<input type="file" id="file-input" multiple accept=".pptx,.pdf,.png,.jpg,.jpeg,.webp" hidden></label>
  <a class="hdr-tool" id="dl-pptx" href="__BASE_PATH__/export/pptx" onclick="busyExport(this)">⤓ PPTX</a>
  <a class="hdr-tool" id="dl-pdf" href="__BASE_PATH__/export/pdf" onclick="busyExport(this)">⤓ PDF</a>
  <button class="export" id="submit-btn" onclick="submitAll()">まとめて修正依頼する</button>
</header>

<div id="done-banner">✓ 送信しました — 担当のAIエージェントが修正を開始します。修正が完了すると、この画面は自動で更新されます。</div>
__PENDING_BANNER__

<div class="stage-hero" id="stage-hero">
  <div class="pulse"></div>
  <div class="stxt">
    <span class="stitle"><span id="stage-title">準備中</span><span class="sdots"></span></span>
    <span class="sdetail" id="stage-detail"></span>
  </div>
</div>

<div class="wrap">
  <nav class="rail" aria-label="スライド索引">
__RAIL__
  </nav>
  <main>
__GLOBAL_PANEL__
__CARDS__
  </main>
</div>

<footer class="sites">
__SITES_LOGO__<a href="https://tekion.jp" target="_blank" rel="noopener">tekion.jp</a>
  <span class="sep">·</span>
  <a href="https://vibe-coder-bootcamp.com" target="_blank" rel="noopener">VibeCoder Bootcamp</a>
  <span class="sep">·</span>
  <a href="https://ai-agent.co.jp" target="_blank" rel="noopener">ai-agent.co.jp</a>
</footer>

<div id="dead-overlay"><div class="box">
  <h2>サーバとの接続が切れています</h2>
  <p>エージェントが修正作業中か、ダッシュボードが終了しています。<br>
  サーバが戻り次第、<b>この画面は自動で復帰します</b>。<br>
  長く戻らない場合は、エージェントに「ダッシュボードを開いて」と頼んでください。</p>
</div></div>

<button id="reload-banner" onclick="location.reload()">デッキが更新されました — 再読み込み</button>
<div id="undo-toast"><span id="undo-msg">スライドを削除しました</span><button onclick="undoDelete()">元に戻す</button></div>
<div id="ok-toast"></div>
<div id="drop-overlay"><div class="box">ドロップで読み込み<br>
<small>スライドの赤い記入欄に落とす → そのスライドの<b>参照画像</b>として添付<br>
それ以外に落とす → デッキに取り込み（.pptx / .pdf は1枚ずつに分解、画像は1枚のスライドに）</small></div></div>

<footer class="hint">
  バージョンを選んで比較し、良い版を「確定にする」。修正指示は各スライドから即送信、または右上からまとめて依頼。
</footer>

<script>
let TOTAL = __COUNT__;
const SESSION_DIR = __SESSION_DIR_JSON__;
const BASE = __BASE_PATH_JSON__;
const SERVED = location.protocol.startsWith('http');
if (!SERVED) {
  ['dl-pptx', 'dl-pdf'].forEach(id => document.getElementById(id).style.display = 'none');
  document.querySelectorAll('.slip .send').forEach(b => b.style.display = 'none');
  // file:// では /home も並べ替え・削除・プロンプト取得もできない
  document.querySelectorAll('.headtools, .prompt-btn').forEach(t => t.style.display = 'none');
  document.querySelectorAll('.rail a[draggable]').forEach(a => a.removeAttribute('draggable'));
  const brand = document.getElementById('brand-link');
  if (brand) brand.removeAttribute('href');
}
/* --- 生成実況: /status をポーリングして進捗表示・自動更新 --- */
let lastSig = null;
let submitted = false;
let pollFails = 0;
function hasUserInput() {
  if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') return true;
  if ([...document.querySelectorAll('textarea[data-slide]')].some(t => t.value.trim())) return true;
  const g = document.getElementById('global-instruction');
  if (g && g.value.trim()) return true;
  // 添付画像も書きかけの入力として扱う（自動リロードで失わない）
  return typeof ATTACH !== 'undefined' && Object.values(ATTACH).some(l => l && l.length);
}
const STAGE_LABELS = {
  planning:  ['スライド構成を執筆中', 'AIが内容を設計しています'],
  prompting: ['画像プロンプトを生成中', 'デザイン指示を組み立てています'],
  prompted:  ['画像生成の開始を待機中', 'まもなく並列生成が始まります'],
  generating: ['スライドを生成中', ''],
  editing:   ['修正指示を反映中', '完了した版から順にこの画面に現れます'],
  attention: ['一部スライドが未完成', '再実行すると欠損分だけ回収されます'],
};
function updateGenProgress(st) {
  const el = document.getElementById('gen-progress');
  const pending = (st.counts.pending || 0) + (st.counts.planned || 0);
  const failed = st.counts.failed || 0;
  const done = st.counts.validated || 0;
  if (pending > 0) {
    el.hidden = false;
    el.innerHTML = '<span class="dot">●</span> 生成中 ' + done + ' / ' + st.total;
  } else if (failed > 0) {
    el.hidden = false;
    el.textContent = '⚠ 未完成 ' + failed + '枚（再実行で回収されます）';
  } else {
    el.hidden = true;
  }

  // ステージヒーロー: 生成前の段階と「1枚もできていない生成中」で大きく実況する
  const hero = document.getElementById('stage-hero');
  const stage = (st.session && st.session.stage) || '';
  const preStages = ['planning', 'prompting', 'prompted', 'editing'];
  const showHero = preStages.includes(stage) || (stage === 'generating' && done === 0 && st.total > 0)
                   || (pending === 0 && failed > 0 && stage === 'attention');
  if (showHero && STAGE_LABELS[stage]) {
    document.getElementById('stage-title').textContent =
      stage === 'generating' ? 'スライドを生成中（' + done + ' / ' + st.total + '）' : STAGE_LABELS[stage][0];
    document.getElementById('stage-detail').textContent =
      (st.session && st.session.detail) || STAGE_LABELS[stage][1];
    hero.classList.add('show');
  } else {
    hero.classList.remove('show');
  }
}
async function pollStatus() {
  if (!SERVED) return;
  try {
    const res = await fetch(BASE + '/status');
    const st = await res.json();
    if (!submitted) updateGenProgress(st);
    const sig = st.slides.map(s => s.base + ':' + s.state + ':' + s.versions + ':' + (s.current || ''))
      .join('|') + '#' + (st.order || []).join(',');
    if (lastSig === null) { lastSig = sig; return; }
    if (sig !== lastSig) {
      lastSig = sig;
      // 送信済みならエージェントの修正が届いた合図 → 即リロードして最新版を見せる。
      // 入力中・アンドゥ表示中は自動リロードしない（書きかけ・取り消し導線を守る）
      if (submitted || (!hasUserInput() && !undoPending)) location.reload();
      else document.getElementById('reload-banner').classList.add('show');
    }
    if (pollFails >= 3) { location.reload(); return; }  // サーバ復活 → 同じタブで復帰
    pollFails = 0;
  } catch (e) {
    // サーバ消失（編集後の再起動待ち等）。ポーリングは続け、復活したら自動復帰する
    if (++pollFails >= 3 && !submitted) {
      document.getElementById('dead-overlay').classList.add('show');
    }
  }
}
if (SERVED) setInterval(pollStatus, 2500);

/* --- ファイル読み込み（＋ボタン / ドラッグ&ドロップ） --- */
const IMPORT_EXTS = ['.pptx', '.pdf', '.png', '.jpg', '.jpeg', '.webp'];
if (!SERVED) document.getElementById('import-btn').style.display = 'none';
document.getElementById('file-input').addEventListener('change', e => {
  if (e.target.files.length) importFiles([...e.target.files]);
  e.target.value = '';
});
let dragDepth = 0;
window.addEventListener('dragenter', e => {
  if (!SERVED || ![...e.dataTransfer.types].includes('Files')) return;
  e.preventDefault(); dragDepth++;
  document.getElementById('drop-overlay').classList.add('show');
});
window.addEventListener('dragleave', e => {
  if (--dragDepth <= 0) { dragDepth = 0;
    document.getElementById('drop-overlay').classList.remove('show'); }
});
window.addEventListener('dragover', e => { if (SERVED) e.preventDefault(); });
window.addEventListener('drop', e => {
  if (!SERVED) return;
  e.preventDefault(); dragDepth = 0;
  document.getElementById('drop-overlay').classList.remove('show');
  if (e.dataTransfer.files.length) importFiles([...e.dataTransfer.files]);
});
function readAsB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result.split(',', 2)[1]);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}
async function importFiles(files) {
  if (submitted) return;
  const valid = files.filter(f => IMPORT_EXTS.some(x => f.name.toLowerCase().endsWith(x)));
  if (!valid.length) { alert('対応形式: ' + IMPORT_EXTS.join(' / ')); return; }
  const btn = document.getElementById('import-btn');
  // トップ（ハブ）では新しいセッションとして開く。デッキ画面では現在のデッキ末尾に追加。
  const newSession = document.body.classList.contains('page-home');
  btn.textContent = '読み込み中…';
  try {
    const payload = { mode: newSession ? 'new_session' : 'append', files: [] };
    for (const f of valid) payload.files.push({ name: f.name, data_b64: await readAsB64(f) });
    const res = await fetch(BASE + '/import', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    const result = await res.json();
    if (!res.ok || !result.ok) throw new Error(result.error || res.status);
    if (result.url) {  // 新しいセッションが立ち上がった → そのままそのデッキへ移動
      btn.textContent = '開いています…';
      location.href = result.url;
      return;
    }
    alert(result.added + '枚を取り込みました' +
          (result.skipped ? '（' + result.skipped + '枚は画像が抽出できずスキップ）' : ''));
    location.reload();
  } catch (e) {
    btn.textContent = '＋ 読み込み';
    alert('読み込みに失敗しました: ' + e.message);
  }
}
async function openSession(btn) {
  btn.disabled = true; btn.classList.add('busy');
  try {
    const res = await fetch(BASE + '/open-session', { method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ path: btn.dataset.path }) });
    const r = await res.json();
    if (!res.ok || !r.ok) throw new Error(r.error || res.status);
    window.open(r.url, '_blank');
  } catch (e) {
    alert('セッションの起動に失敗しました: ' + e.message);
  } finally {
    btn.disabled = false; btn.classList.remove('busy');
  }
}
function busyExport(a) {
  a.classList.add('busy');
  setTimeout(() => a.classList.remove('busy'), 6000);
}
function card(el) { return el.closest('.proof'); }

/* --- バージョン選択 = 確定（クリックした版が即その場で確定版になる） --- */
function applySelection(c, node) {
  c.querySelectorAll('.vnode').forEach(x => x.classList.remove('current'));
  node.classList.add('current');
  c.querySelector('img.slide').src = node.dataset.src;
  c.querySelector('.viewing-label b').textContent = node.dataset.label;
  const dl = c.querySelector('a.tool.dl');
  dl.href = node.dataset.orig;
  dl.download = node.dataset.file;
  const railImg = document.querySelector('.rail a[href="#' + c.id + '"] img');
  if (railImg) railImg.src = node.dataset.src;
}
async function selectVersion(node) {
  if (submitted) return;
  const c = card(node);
  const prev = c.querySelector('.vnode.current');
  if (prev === node) return;
  applySelection(c, node);  // 楽観的に即反映
  if (!SERVED) return;      // file:// では表示切替のみ（保存はできない）
  const post = () => fetch(BASE + '/select-version', { method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ slide: c.dataset.slide, image: node.dataset.orig }) });
  try {
    let res = await post();
    if (!res.ok) {
      await new Promise(r => setTimeout(r, 600));  // クラウド同期の瞬断は1回のリトライで通ることが多い
      res = await post();
    }
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).error || ''; } catch (_) {}
      throw new Error(detail || ('HTTP ' + res.status));
    }
    lastSig = null;  // 自分の変更で自分のタブをリロードさせない
    flashToast(node.dataset.label + ' を確定版にしました');
  } catch (e) {
    if (prev) applySelection(c, prev);  // 失敗したら元に戻す
    alert('確定の保存に失敗しました: ' + e.message);
  }
}
function flashToast(msg) {
  const t = document.getElementById('ok-toast');
  t.textContent = '✓ ' + msg;
  t.classList.add('show');
  clearTimeout(flashToast._tm);
  flashToast._tm = setTimeout(() => t.classList.remove('show'), 2200);
}

/* --- 索引レールのスクロール連動 --- */
const railMap = {};
document.querySelectorAll('.rail a').forEach(a => railMap[a.getAttribute('href').slice(1)] = a);

/* --- 並べ替え（カードの↑↓ / レールのドラッグ&ドロップ） --- */
function renumber() {
  [...document.querySelectorAll('main article.proof')].forEach((a, i) => {
    const ord = String(i + 1).padStart(2, '0');
    const o = a.querySelector('.ord'); if (o) o.textContent = ord;
    const ph = a.querySelector('.ph-num'); if (ph) ph.textContent = ord;
    const railItem = railMap[a.id];
    if (railItem) {
      railItem.dataset.ord = ord;
      const tag = railItem.querySelector('.tag');
      if (tag && !railItem.classList.contains('has-ink')) tag.textContent = ord;
    }
  });
}
async function postOrder() {
  if (!SERVED) return;
  const order = [...document.querySelectorAll('main article.proof')].map(a => a.dataset.slide);
  try {
    const res = await fetch(BASE + '/reorder', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ order }) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    lastSig = null;
    flashToast('並び順を保存しました');
  } catch (e) {
    alert('並び順の保存に失敗しました: ' + e.message + '\\n再読み込みして確認してください。');
  }
}
function moveSlide(btn, dir) {
  if (submitted) return;
  const c = card(btn);
  const sib = dir < 0 ? c.previousElementSibling : c.nextElementSibling;
  if (!sib || !sib.classList.contains('proof')) return;
  c.parentNode.insertBefore(c, dir < 0 ? sib : sib.nextSibling);
  const r = railMap[c.id], rs = railMap[sib.id];
  if (r && rs) r.parentNode.insertBefore(r, dir < 0 ? rs : rs.nextSibling);
  renumber(); postOrder();
  c.scrollIntoView({ block: 'nearest' });
}
let dragSrc = null;
function bindRailDrag() {
  if (!SERVED) return;
  document.querySelectorAll('.rail a[draggable]').forEach(a => {
    a.addEventListener('dragstart', e => {
      dragSrc = a; e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', a.getAttribute('href')); } catch (_) {}
      a.classList.add('dragging');
    });
    a.addEventListener('dragover', e => {
      if (!dragSrc || dragSrc === a) return;
      e.preventDefault();
      const rect = a.getBoundingClientRect();
      const before = e.clientY < rect.top + rect.height / 2;
      a.parentNode.insertBefore(dragSrc, before ? a : a.nextSibling);
    });
    a.addEventListener('dragend', () => {
      if (!dragSrc) return;
      dragSrc.classList.remove('dragging'); dragSrc = null;
      // レールの並び順に合わせて本文カードも並べ替える
      const main = document.querySelector('main');
      [...document.querySelectorAll('.rail a[draggable]')].forEach(x => {
        const art = document.getElementById(x.getAttribute('href').slice(1));
        if (art) main.appendChild(art);
      });
      renumber(); postOrder();
    });
  });
}
bindRailDrag();

/* --- プロンプトライブラリ表示 --- */
async function togglePrompt(btn) {
  const c = card(btn);
  const box = c.querySelector('.promptbox');
  if (!box.hidden) { box.hidden = true; return; }
  const pre = box.querySelector('pre');
  box.hidden = false;
  if (pre.dataset.loaded) return;
  pre.textContent = '読み込み中…';
  try {
    const res = await fetch(BASE + '/prompt?slide=' + encodeURIComponent(c.dataset.slide));
    const r = await res.json();
    if (!res.ok || !r.ok) throw new Error(r.error || ('HTTP ' + res.status));
    pre.textContent = r.prompt;
    pre.dataset.loaded = '1';
  } catch (e) {
    pre.textContent = 'プロンプトを取得できませんでした: ' + e.message;
  }
}
function copyPrompt(btn) {
  const pre = card(btn).querySelector('.promptbox pre');
  navigator.clipboard.writeText(pre.textContent).then(() => {
    btn.textContent = '✓ コピーしました';
    setTimeout(() => { btn.textContent = 'コピー'; }, 1500);
  }, () => alert('コピーに失敗しました'));
}

/* --- 削除（ソフトデリート + 元に戻す） --- */
let undoPending = null;
let undoTimer = null;
async function deleteSlide(btn) {
  if (submitted) return;
  const c = card(btn);
  const base = c.dataset.slide;
  const railItem = railMap[c.id];
  const memo = { card: c, railItem, nextCard: c.nextElementSibling,
                 nextRail: railItem ? railItem.nextElementSibling : null,
                 base, hadImage: !c.classList.contains('placeholder') };
  c.remove(); if (railItem) railItem.remove();
  if (memo.hadImage) TOTAL--;
  renumber(); updateTally();
  try {
    const res = await fetch(BASE + '/delete-slide', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ slide: base }) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    lastSig = null;
    undoPending = memo;
    document.getElementById('undo-msg').textContent = base + ' を削除しました';
    document.getElementById('undo-toast').classList.add('show');
    clearTimeout(undoTimer);
    undoTimer = setTimeout(() => {
      document.getElementById('undo-toast').classList.remove('show');
      undoPending = null;
    }, 8000);
  } catch (e) {
    restoreDom(memo);
    alert('削除に失敗しました: ' + e.message);
  }
}
function restoreDom(memo) {
  const main = document.querySelector('main');
  if (memo.nextCard && memo.nextCard.isConnected) main.insertBefore(memo.card, memo.nextCard);
  else main.appendChild(memo.card);
  const rail = document.querySelector('nav.rail');
  if (memo.railItem && rail) {
    if (memo.nextRail && memo.nextRail.isConnected) rail.insertBefore(memo.railItem, memo.nextRail);
    else rail.appendChild(memo.railItem);
  }
  if (memo.hadImage) TOTAL++;
  renumber(); updateTally();
}
async function undoDelete() {
  if (!undoPending) return;
  const memo = undoPending;
  undoPending = null;
  clearTimeout(undoTimer);
  document.getElementById('undo-toast').classList.remove('show');
  try {
    const res = await fetch(BASE + '/restore-slide', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ slide: memo.base }) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    lastSig = null;
    restoreDom(memo);
  } catch (e) {
    alert('復元に失敗しました: ' + e.message);
  }
}
const spy = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    document.querySelectorAll('.rail a.active').forEach(x => x.classList.remove('active'));
    const item = railMap[e.target.id];
    if (item) { item.classList.add('active'); item.scrollIntoView({ block: 'nearest' }); }
  });
}, { rootMargin: '-35% 0px -55% 0px' });
document.querySelectorAll('article.proof').forEach(a => spy.observe(a));

/* --- 修正指示 --- */
/* 添付画像（スライド毎）: base → [{name, data_b64}] */
const ATTACH = {};
function readImageB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result.split(',', 2)[1]);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}
async function addAttachments(c, files) {
  const imgs = [...files].filter(f => f.type.startsWith('image/'));
  if (!imgs.length) { alert('画像ファイル（PNG/JPG等）を添付してください'); return; }
  const base = c.dataset.slide;
  const list = ATTACH[base] = ATTACH[base] || [];
  for (const f of imgs.slice(0, 8 - list.length)) {
    list.push({ name: f.name, data_b64: await readImageB64(f), type: f.type });
  }
  renderChips(c);
  markCard(c.querySelector('textarea'));
}
function pickAttachments(input) {
  const c = card(input);
  if (input.files.length) addAttachments(c, input.files);
  input.value = '';
}
function renderChips(c) {
  const wrap = c.querySelector('.attach-chips');
  if (!wrap) return;
  const base = c.dataset.slide;
  const list = ATTACH[base] || [];
  wrap.innerHTML = list.map((a, i) =>
    '<span class="chip"><img src="data:' + (a.type || 'image/png') + ';base64,' + a.data_b64 +
    '" alt="" title="' + a.name.replace(/"/g, '&quot;') + '">' +
    '<button onclick="removeAttachment(this,' + i + ')" title="添付を外す" aria-label="添付を外す">×</button></span>'
  ).join('');
}
function removeAttachment(btn, idx) {
  if (submitted) return;
  const c = card(btn);
  (ATTACH[c.dataset.slide] || []).splice(idx, 1);
  renderChips(c);
  markCard(c.querySelector('textarea'));
}
/* 赤入れ欄への直接ドロップ = そのスライドへの参照画像添付
   （ドロップオーバーレイは pointer-events:none の視覚のみなので、ここに落ちる） */
function bindSlipDrop() {
  if (!SERVED) return;
  document.querySelectorAll('article.proof .slip').forEach(slip => {
    slip.addEventListener('dragover', e => {
      e.preventDefault(); e.stopPropagation();
      slip.classList.add('att-dragover');
    });
    slip.addEventListener('dragleave', () => slip.classList.remove('att-dragover'));
    slip.addEventListener('drop', e => {
      e.preventDefault(); e.stopPropagation();
      slip.classList.remove('att-dragover');
      dragDepth = 0;
      document.getElementById('drop-overlay').classList.remove('show');
      if (!submitted && e.dataTransfer.files.length) {
        addAttachments(card(slip), e.dataTransfer.files);
      }
    });
  });
}
bindSlipDrop();
function markCard(el) {
  const c = card(el);
  const text = c.querySelector('textarea').value.trim();
  const kr = c.querySelector('.rebuild-opt input');
  const keepref = !!(kr && kr.checked);
  const atts = (ATTACH[c.dataset.slide] || []).length;
  const inked = text.length > 0 || atts > 0;
  c.classList.toggle('has-ink', inked);
  c.querySelector('.state').textContent =
    !inked ? '校了' : (keepref ? '要修正' : '作り直し');
  const railItem = railMap[c.id];
  if (railItem) {
    railItem.classList.toggle('has-ink', inked);
    railItem.querySelector('.tag').textContent = inked ? '修正' : railItem.dataset.ord;
  }
  updateTally();
}
function updateTally() {
  const n = document.querySelectorAll('.proof.has-ink').length;
  document.getElementById('n-ink').textContent = n;
  document.getElementById('n-ok').textContent = Math.max(0, TOTAL - n);
}
function freeze(message) {
  submitted = true;
  document.getElementById('done-banner').classList.add('show');
  // 送信後は manifest を変える操作をすべて凍結する（送信済み内容との競合防止）
  document.querySelectorAll(
    'textarea, .slip .send, .rebuild-opt input, .attach-btn input, .attach-chips button, .hbtn, .vnode')
    .forEach(t => t.disabled = true);
  document.querySelectorAll('.rail a[draggable]').forEach(a => a.removeAttribute('draggable'));
  const btn = document.getElementById('submit-btn');
  btn.disabled = true; btn.textContent = message;
}
async function post(payload) {
  const res = await fetch(BASE + '/feedback', { method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload) });
  if (!res.ok) throw new Error(res.status);
}
/* デフォルト = 前の画像を参照せず作り直し（rebuild）。
   「🔗 前の画像を参照して微修正」にチェックしたスライドだけ差分編集になる。
   rebuild は専用配列に加え、rebuild キーを知らない旧エージェントにも依頼が
   届くよう feedback にもマーカー付きで複製する（新エージェントはマーカー行を除いて読む） */
const REBUILD_MARK = '【作り直し】前の画像を参照せず、ゼロから再生成する。';
function slipEntry(c) {
  const t = c.querySelector('textarea');
  if (!t) return null;  // プレースホルダーには記入欄が無い
  const text = t.value.trim();
  const kr = c.querySelector('.rebuild-opt input');
  return { base: c.dataset.slide, text, keepref: !!(kr && kr.checked),
           atts: ATTACH[c.dataset.slide] || [] };
}
function addEntry(payload, e) {
  if (!e.text && !e.atts.length) return;
  const text = e.text || '添付画像を参照して修正';
  if (e.keepref) {
    payload.feedback[e.base] = text;
  } else {
    payload.rebuild.push(e.base);
    payload.feedback[e.base] = REBUILD_MARK + '\\n' + text;
  }
  if (e.atts.length) {
    payload.attachments[e.base] = e.atts.map(a => ({ name: a.name, data_b64: a.data_b64 }));
  }
}
function addGlobal(payload) {
  const g = document.getElementById('global-instruction');
  if (g && g.value.trim()) {
    payload.global = g.value.trim();
    const gk = document.getElementById('global-keepref');
    payload.global_keep_reference = !!(gk && gk.checked);
  }
}
function emptyPayload() {
  return { session_dir: SESSION_DIR, feedback: {}, rebuild: [], attachments: {} };
}
async function sendOne(btn) {
  const e = slipEntry(card(btn));
  if (!e || (!e.text && !e.atts.length)) {
    alert('修正指示を書くか、参照画像を添付してから送信してください');
    return;
  }
  btn.disabled = true; btn.textContent = '…';
  try {
    const payload = emptyPayload();
    addEntry(payload, e);
    await post(payload);
    freeze('修正を依頼しました');
  } catch (err) {
    btn.disabled = false; btn.textContent = '⏎';
    alert('送信に失敗しました（サーバが終了している可能性）');
  }
}
function collectAll() {
  const payload = emptyPayload();
  document.querySelectorAll('article.proof[data-slide]').forEach(c => {
    const e = slipEntry(c);
    if (e) addEntry(payload, e);
  });
  addGlobal(payload);
  return payload;
}
async function submitAll() {
  const payload = collectAll();
  const n = Object.keys(payload.feedback).length + (payload.global ? 1 : 0);
  const btn = document.getElementById('submit-btn');
  if (SERVED) {
    btn.disabled = true; btn.textContent = '送信中…';
    try {
      await post(payload);
      freeze(n ? '修正を依頼しました（' + n + '件）' : '全スライド校了で送信しました');
    } catch (e) {
      btn.disabled = false; btn.textContent = 'まとめて修正依頼する';
      alert('送信に失敗しました（サーバが終了している可能性）。もう一度お試しください。');
    }
  } else {
    const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'slide_feedback.json';
    a.click();
    alert(n ? n + '枚分の修正指示をダウンロードしました。AIエージェントに渡してください'
            : '全スライド校了として書き出しました');
  }
}
</script>
</body>
</html>
"""

CARD_TEMPLATE = """    <article class="proof" id="p-__NAME__" data-slide="__NAME__">
      <div class="head">
        <span class="ord">__ORD__</span>
        <span class="id mono">__NAME__</span>
        <span class="state">校了</span>
        <span class="headtools">
          <button class="hbtn" onclick="moveSlide(this,-1)" title="1つ上へ" aria-label="1つ上へ">↑</button>
          <button class="hbtn" onclick="moveSlide(this,1)" title="1つ下へ" aria-label="1つ下へ">↓</button>
          <button class="hbtn del" onclick="deleteSlide(this)" title="このスライドを削除" aria-label="このスライドを削除">🗑</button>
        </span>
      </div>
      <div class="body__BODY_CLASS__">
        <div class="maincol">
          <img class="slide" src="__CUR_SRC__" alt="__NAME__" loading="lazy">
          <div class="tools">
            <span class="viewing-label">確定版: <b>__CUR_LABEL__</b></span>
            <button class="tool prompt-btn" onclick="togglePrompt(this)">☰ プロンプト</button>
            <a class="tool dl" href="__CUR_ORIG__" download="__CUR_FILE__">⤓ PNG保存</a>
          </div>
          <div class="promptbox" hidden>
            <div class="pbar"><span class="ptitle">Generation Prompt</span>
              <button class="pcopy" onclick="copyPrompt(this)">コピー</button></div>
            <pre></pre>
          </div>
        </div>
__VTREE__
      </div>
      <div class="slip">
        <div class="label"><span class="kanji">修正指示</span><span class="sub">FEEDBACK</span></div>
        <div class="slipmain">
          <textarea data-slide="__NAME__" placeholder="このスライドへの修正指示（空欄なら校了）" oninput="markCard(this)"></textarea>
          <div class="slipopts">
            <label class="attach-btn" title="参照画像を添付（この記入欄へのドラッグ&ドロップでも可）">🖼 + 画像<input type="file" accept="image/*" multiple hidden onchange="pickAttachments(this)"></label>
            <span class="attach-chips"></span>
            <label class="rebuild-opt" title="レイアウト・構図を保ったまま微修正したいときに。現在の画像を参照として渡します（デフォルトは参照せず、プロンプト+指示で再生成）">
              <input type="checkbox" data-keepref="__NAME__" onchange="markCard(this)"> 🔗 前の画像を参照して微修正</label>
          </div>
        </div>
        <button class="send" onclick="sendOne(this)" title="この修正を依頼" aria-label="この修正を依頼">⏎</button>
      </div>
    </article>"""

GLOBAL_PANEL_HTML = """    <section class="slip gslip">
      <div class="label"><span class="kanji">全体指示</span><span class="sub">ALL SLIDES</span></div>
      <div class="slipmain">
        <textarea id="global-instruction" placeholder="デッキ全体への指示（例: 全体的に余白を増やして / 文字をもっと大きく / トーンを明るく）"></textarea>
        <div class="slipopts">
          <label class="rebuild-opt" title="レイアウトを保ったまま全スライドを微修正したいとき">
            <input type="checkbox" id="global-keepref"> 🔗 前の画像を参照して微修正</label>
        </div>
      </div>
    </section>"""

RAIL_TEMPLATE = """    <a href="#p-__NAME__" data-ord="__ORD__" draggable="true" title="ドラッグで並べ替え"><span class="tag">__ORD__</span><img src="__RAIL_SRC__" alt="__NAME__ サムネイル"></a>"""

PLACEHOLDER_CARD_TEMPLATE = """    <article class="proof placeholder" id="p-__NAME__" data-slide="__NAME__">
      <div class="head">
        <span class="ord">__ORD__</span>
        <span class="id mono">__NAME__</span>
        <span class="state">__PH_STATE__</span>
      </div>
      <div class="body">
        <div class="maincol">
          <div class="ph-frame"><span class="ph-label"><span class="ph-num">__ORD__</span>__PH_STATE__</span></div>
        </div>
      </div>
    </article>"""

PLACEHOLDER_RAIL_TEMPLATE = """    <a href="#p-__NAME__" data-ord="__ORD__" draggable="true" title="ドラッグで並べ替え"><span class="tag">__ORD__</span><div class="ph-thumb"></div></a>"""


THUMB_MAIN_W = 1600   # メイン表示
THUMB_VER_W = 480     # バージョンタイムライン
THUMB_RAIL_W = 320    # 索引レール

CLOUD_MARKERS = ("/Library/CloudStorage/", "/Dropbox/", "/OneDrive")


def _spawn_child_server(target: str) -> str | None:
    """別セッションのダッシュボードを独立プロセスで起動し、その URL を返す。

    起動した子サーバは親と独立（start_new_session）なので、親が終了しても生き続ける。
    """
    import tempfile as _tf
    import time as _time
    url_tmp = _tf.mktemp(suffix=".url")
    subprocess.Popen([sys.executable, os.path.abspath(__file__),
                      "--session-dir", target, "--serve",
                      "--serve-timeout", "7200", "--no-open", "--url-file", url_tmp],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    child_url = None
    for _ in range(40):
        _time.sleep(0.25)
        if os.path.exists(url_tmp):
            with open(url_tmp, "r", encoding="utf-8") as f:
                child_url = f.read().strip()
            if child_url:
                break
    try:
        os.unlink(url_tmp)
    except OSError:
        pass
    return child_url


FEEDBACK_CURSOR = ".processed"  # feedback_history/ 内の処理済みカーソルファイル


def pending_feedback(session_dir: str) -> list:
    """未処理のフィードバック履歴ファイル（古い順の絶対パス）を返す。

    送信はすべて feedback_history/ にマイクロ秒付きファイル名で永続化される。
    処理済みカーソル（FEEDBACK_CURSOR に最後に処理したファイル名）より新しいものが
    「未処理」。mtime 比較ではなくキューなので、待ち受け開始前の送信・複数送信・
    編集中の追加送信を取りこぼさない。
    """
    session_dir = os.path.realpath(os.path.abspath(session_dir))
    hist_dir = os.path.join(session_dir, "feedback_history")
    if not os.path.isdir(hist_dir):
        return []
    cursor = ""
    try:
        with open(os.path.join(hist_dir, FEEDBACK_CURSOR), "r", encoding="utf-8") as f:
            cursor = f.read().strip()
    except OSError:
        pass
    try:
        names = sorted(n for n in os.listdir(hist_dir) if n.endswith(".json"))
    except OSError:
        return []
    return [os.path.join(hist_dir, n) for n in names if n > cursor]


def failed_feedback(session_dir: str) -> list:
    """自動処理ワーカーが失敗させた送信（dead-letter）の一覧（古い順の絶対パス）。"""
    failed_dir = os.path.join(os.path.realpath(os.path.abspath(session_dir)),
                              "feedback_history", "failed")
    if not os.path.isdir(failed_dir):
        return []
    try:
        names = sorted(n for n in os.listdir(failed_dir) if n.endswith(".json"))
    except OSError:
        return []
    return [os.path.join(failed_dir, n) for n in names]


def ack_feedback(session_dir: str) -> int:
    """未処理キューと dead-letter を処理済みにする。Returns: ack した件数。

    エージェントは未処理・失敗分を古い順に処理し、編集・検証が完了した時点でのみ
    これを呼ぶ（途中で呼ぶと未処理分が失われる）。dead-letter は failed/archived/ へ移す。
    """
    count = 0
    pend = pending_feedback(session_dir)
    if pend:
        hist_dir = os.path.dirname(pend[-1])
        with open(os.path.join(hist_dir, FEEDBACK_CURSOR), "w", encoding="utf-8") as f:
            f.write(os.path.basename(pend[-1]))
        count += len(pend)
    failed = failed_feedback(session_dir)
    if failed:
        import shutil
        archived = os.path.join(os.path.dirname(failed[0]), "archived")
        os.makedirs(archived, exist_ok=True)
        for p in failed:
            try:
                shutil.move(p, os.path.join(archived, os.path.basename(p)))
                count += 1
            except OSError:
                pass
    return count


def _pending_feedback_banner(session_dir: str) -> str:
    """未処理のフィードバックがあれば、デッキ上部に出すバナー HTML を返す。"""
    pend = pending_feedback(session_dir)
    if not pend:
        return ""
    import datetime as _dt
    try:
        sent = _dt.datetime.fromtimestamp(os.path.getmtime(pend[0])).strftime("%H:%M")
    except OSError:
        sent = "--:--"
    return (
        f'<div class="pending-banner">⏳ 未処理の修正指示 {len(pend)}件'
        f'（最初の送信 {sent}）— エージェントに「続きを」と伝えてください</div>'
    )


def build_html(session_dir: str, use_thumbs: bool = False, page: str = "deck",
               base_path: str = "", hub_mode: bool = False) -> str:
    """デッキまたはホーム画面を組み立てる。

    base_path は常駐 Hub の ``/s/<sid>`` のような URL 接頭辞。空文字の
    既定値を保つことで、従来の単一セッションサーバと静的 HTML は変わらない。
    """
    base_path = ("/" + base_path.strip("/")) if base_path.strip("/") else ""
    # Google Drive 等は同一フォルダに複数のパス表記（マイドライブ/My Drive等）があるため、
    # 実体パスに正規化してから相対パスを計算する
    session_dir = os.path.realpath(os.path.abspath(session_dir))
    manifest_path = os.path.join(session_dir, "manifest.json")
    manifest = load_manifest(manifest_path)
    slides = manifest.get("slides", {})

    # 並び順: ダッシュボードでの並べ替え（slide_order）優先・削除済みは出さない
    ordered = [(b, slides[b]) for b in ordered_bases(manifest)]

    cards, rail = [], []
    rendered_total = 0
    ord_no = 0
    for base, entry in ordered:
        current = entry.get("current_image")
        if not current or not os.path.exists(current):
            # 画像がまだ無いスライドはプレースホルダーで枠を出す（生成前から全体像が見える）
            ord_no += 1
            state = entry.get("state", "pending")
            ph_state = {"failed": "再試行待ち", "planned": "生成待ち"}.get(state, "生成中")
            subs = {"__NAME__": html.escape(base), "__ORD__": f"{ord_no:02d}",
                    "__PH_STATE__": ph_state}
            card, rail_item = PLACEHOLDER_CARD_TEMPLATE, PLACEHOLDER_RAIL_TEMPLATE
            for k, v in subs.items():
                card = card.replace(k, v)
                rail_item = rail_item.replace(k, v)
            cards.append(card)
            rail.append(rail_item)
            continue
        ord_no += 1
        rendered_total += 1
        versions = [v for v in (entry.get("versions") or [current]) if os.path.exists(v)]
        if current not in versions:
            versions.append(current)
        from urllib.parse import quote as _q
        # クラウド同期フォルダは同一実体に複数のパス表記を持ち得るため（Google Drive の
        # My Drive/マイドライブ等）、パス文字列の相対化はせず「images/<basename>」で解決する
        images_dir = os.path.join(session_dir, "images")

        def rel_image(p):
            cand = os.path.join("images", os.path.basename(p))
            if os.path.exists(os.path.join(session_dir, cand)):
                return cand
            return os.path.relpath(os.path.realpath(p), session_dir)

        seen_names = set()
        uniq = []
        for v in versions:
            name = os.path.basename(v)
            if name not in seen_names:
                seen_names.add(name)
                uniq.append(v)
        versions = uniq
        if os.path.basename(current) not in seen_names:
            versions.append(current)
        cur_rel = rel_image(current)

        def disp(rel, w):
            # 表示はサムネイル(serve時)、ダウンロード等は原本を使う
            return f"thumb/{_q(rel)}?w={w}" if use_thumbs else rel

        import re as _re

        def vnum(p):
            mnum = _re.search(r'_v(\d+)\.png$', os.path.basename(p))
            return int(mnum.group(1)) if mnum else 1

        cur_label = f"v{vnum(current)}"

        vnodes = []
        import datetime as _dtmod
        for v in versions:
            i = vnum(v)
            rel = rel_image(v)
            classes = "vnode"
            if v == current:
                classes += " current"
            try:
                # 生成時刻: 再生成同士は見た目が似るため、どれが新しいか一目で分かるように
                vtime = _dtmod.datetime.fromtimestamp(os.path.getmtime(v)).strftime("%H:%M")
            except OSError:
                vtime = ""
            vnodes.append(
                f'          <button class="{classes}" data-src="{html.escape(disp(rel, THUMB_MAIN_W))}" '
                f'data-orig="{html.escape(rel)}" '
                f'data-file="{html.escape(os.path.basename(v))}" data-label="v{i}" '
                f'onclick="selectVersion(this)">'
                f'<img src="{html.escape(disp(rel, THUMB_VER_W))}" loading="lazy" alt="v{i}">'
                f'<span class="vmeta"><span class="vname">v{i}</span>'
                f'<span class="badge">確定</span>'
                f'<span class="vtime">{vtime}</span></span></button>')

        multi = len(versions) > 1
        vtree = ""
        if multi:
            vtree = ('        <aside class="vtree">\n'
                     '          <p class="vtitle">VERSIONS</p>\n'
                     + "\n".join(vnodes) + "\n        </aside>")

        subs = {
            "__NAME__": html.escape(base),
            "__ORD__": f"{ord_no:02d}",
            "__CUR_SRC__": html.escape(disp(cur_rel, THUMB_MAIN_W)),
            "__RAIL_SRC__": html.escape(disp(cur_rel, THUMB_RAIL_W)),
            "__CUR_ORIG__": html.escape(cur_rel),
            "__CUR_FILE__": html.escape(os.path.basename(current)),
            "__CUR_LABEL__": cur_label,
            "__BODY_CLASS__": " with-versions" if multi else "",
            "__VTREE__": vtree,
        }
        card, rail_item = CARD_TEMPLATE, RAIL_TEMPLATE
        for k, v in subs.items():
            card = card.replace(k, v)
            rail_item = rail_item.replace(k, v)
        cards.append(card)
        rail.append(rail_item)

    # ハブ画面: /home で明示的に開くか、スライドが1枚もないときに表示される
    page_kind = "home" if (page == "home" or not cards) else "deck"
    landing = ""
    if page_kind == "home":
        import base64 as _b64
        ui_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "assets", "ui")

        def _data_uri(name):
            p = os.path.join(ui_dir, name)
            if os.path.exists(p):
                mime = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "image/png"
                with open(p, "rb") as image_file:
                    return f"data:{mime};base64," + _b64.b64encode(image_file.read()).decode()
            return None

        btn_import = _data_uri("btn_import.png")
        hero_banner = _data_uri("hero_ryoko.jpg")

        # 最近のセッション（現在のセッション以外・実在するもの）: note 風のカードグリッド
        recent_html = ""
        try:
            from session_registry import list_sessions as _ls
            _rows = [r for r in _ls(limit=12)
                     if os.path.realpath(r["path"]) != session_dir and r["exists"]
                     and r["slides"] > 0]  # 空セッションはカードにしない
            if _rows:
                from urllib.parse import quote as _qq
                from session_registry import session_id as _sid
                items = []
                for r in _rows:
                    # タイトルは「題名（プロジェクト / タイムスタンプ）」形式 → 題名を主役に
                    title = r["title"]
                    stem, _, rest = title.partition("（")
                    sub = rest[:-1] if rest.endswith("）") else rest
                    meta = (f'<span class="rmeta">{html.escape(r["updated_at"][:16].replace("T", " "))}'
                            f' · {r["slides"]}枚'
                            + (f' · {html.escape(sub)}' if sub else '')
                            + '</span>')
                    if hub_mode:
                        sid = r.get("sid") or _sid(r["path"])
                        href = f"/s/{sid}/"
                        items.append(
                            f'        <a class="rcard" href="{href}" title="このデッキを開く">'
                            f'<img class="rthumb" loading="lazy" alt="" '
                            f'src="/s/{sid}/session-thumb?w=480" '
                            f'onerror="this.style.visibility=\'hidden\'">'
                            f'<span class="rtitle">{html.escape(stem or title)}</span>'
                            f'{meta}</a>')
                    else:
                        items.append(
                            f'        <button class="rcard" onclick="openSession(this)" '
                            f'data-path="{html.escape(r["path"])}" title="このデッキを開く">'
                            f'<img class="rthumb" loading="lazy" alt="" '
                            f'src="/session-thumb?path={_qq(r["path"], safe="")}&amp;w=480" '
                            f'onerror="this.style.visibility=\'hidden\'">'
                            f'<span class="rtitle">{html.escape(stem or title)}</span>'
                            f'{meta}</button>')
                recent_html = ('      <section class="recent">\n'
                               '        <div class="sec-head"><span class="eyebrow">Recent Sessions</span>'
                               '<h3>最近のデッキ</h3></div>\n'
                               '        <div class="rgrid">\n'
                               + "\n".join(items) + "\n        </div>\n      </section>\n")
        except Exception:
            recent_html = ""

        backlink = (f'        <a class="backlink" href="{base_path or "/"}">開いているデッキへ戻る →</a>\n'
                    if cards else "")
        if btn_import:
            choices = f"""      <div class="choices bannerbtns single">
        <label class="banner-btn" for="file-input" title=".pptx / .pdf / 画像 を選択（ドロップでも可）">
          <img src="{btn_import}" alt="既存デッキを読み込む"></label>
      </div>"""
        else:
            choices = """      <div class="choices single">
        <label class="choice" for="file-input"><span class="big">📂</span>既存デッキを読み込む
          <small>.pptx / .pdf / 画像 をここにドロップ、<br>またはクリックして選択</small></label>
      </div>"""

        herobg = (f'        <img class="herobg" src="{hero_banner}" alt="">\n'
                  if hero_banner else "")
        landing = f"""    <section class="landing">
      <div class="hero">
{herobg}{backlink}        <div class="heroin">
          <span class="eyebrow">Tekion Group</span>
          <h2 class="htitle">TEKION<br>Slide Generator<span class="vbadge">V6</span></h2>
          <p class="lead">新しく作るときはエージェントに「◯◯のスライドを作って」と言うだけ。<br>既存デッキはここにドロップ。赤入れも書き出しも、この画面がハブになります。</p>
        </div>
      </div>
{choices}
{recent_html}      <section class="onboard">
        <div class="sec-head"><span class="eyebrow">How It Works</span><h3>使い方は3ステップ</h3></div>
        <div class="steps">
          <div class="step"><span class="n">1</span><h3>作る / 読み込む</h3>
            <p>エージェント（Claude / Codex）に<br><b>「◯◯のスライドを作って」</b>と話しかける。<br>既存デッキはこの画面にドロップ。</p></div>
          <div class="step"><span class="n">2</span><h3>赤入れで直す</h3>
            <p>気になるスライドの<b>赤い記入欄</b>に修正指示を書いて <b>⏎</b>。<br>AIが該当スライドだけ描き直し、<br>版を並べて比較・選択できる。</p></div>
          <div class="step"><span class="n">3</span><h3>持っていく</h3>
            <p>右上の <b>⤓ PPTX / ⤓ PDF</b> でダウンロード。<br>選択中の確定版で書き出される。</p></div>
        </div>
        <p class="brandhint">🎨 <b>自社のデザインにしたい？</b> エージェントに<b>「デザインを設定したい」</b>と言うと、
ロゴ・パワポのマスター・既存資料のスクショなどを渡すだけで、対話形式で自社プリセットを作成できます。
一度作れば、次回から「スライドを作って」だけで自社デザインが自動適用されます。
調整も「ロゴを左下に」「メインカラーを変えて」と言うだけ。</p>
      </section>
    </section>"""

    session_name = os.path.basename(os.path.abspath(session_dir))

    # TEKION ロゴ（あれば data URI で埋め込み、無ければテキストにフォールバック）
    import base64 as _b64logo
    logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "assets", "ui", "tekion_logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as logo_file:
            logo_uri = "data:image/png;base64," + _b64logo.b64encode(logo_file.read()).decode()
        brand_top = (f'    <img class="brandlogo" src="{logo_uri}" alt="TEKION Group">\n'
                     '    <span class="bdiv"></span>\n')
        sites_logo = f'  <img src="{logo_uri}" alt="TEKION Group">\n'
    else:
        brand_top = ''
        sites_logo = ''

    out = PAGE_TEMPLATE
    pending_banner = "" if page_kind == "home" else _pending_feedback_banner(session_dir)
    home_path = "/" if hub_mode else (base_path + "/home")
    for k, v in {
        "__BRAND_TOP__": brand_top,
        "__SITES_LOGO__": sites_logo,
        "__TITLE__": html.escape(f"スライドダッシュボード — {session_name}"),
        "__SESSION_NAME__": html.escape(session_name),
        "__PAGE_CLASS__": "page-home" if page_kind == "home" else "page-deck",
        "__HOME_PATH__": home_path,
        "__BASE_PATH__": base_path,
        "__BASE_PATH_JSON__": json.dumps(base_path),
        "__PENDING_BANNER__": pending_banner,
        "__COUNT__": str(rendered_total),
        "__RAIL__": "" if page_kind == "home" else "\n".join(rail),
        "__CARDS__": landing if page_kind == "home" else "\n".join(cards),
        "__GLOBAL_PANEL__": GLOBAL_PANEL_HTML if (page_kind == "deck" and rendered_total > 0) else "",
        "__SESSION_DIR_JSON__": json.dumps(os.path.abspath(session_dir), ensure_ascii=False),
    }.items():
        out = out.replace(k, v)
    return out


class DashboardService:
    """単一セッションに対するダッシュボード操作。

    従来サーバと常駐 Hub が同じ manifest 更新・書き出し・フィードバック保存
    ロジックを使うための薄いサービス層。HTTP の URL 解決と寿命管理は各サーバ側に残す。
    """

    def __init__(self, session_dir: str, manifest_lock: threading.Lock | None = None,
                 restrict_paths: bool = False):
        self.session_dir = os.path.realpath(os.path.abspath(session_dir))
        self.manifest_path = os.path.join(self.session_dir, "manifest.json")
        self.feedback_path = os.path.join(self.session_dir, "slide_feedback.json")
        self.manifest_lock = manifest_lock or threading.Lock()
        self.restrict_paths = restrict_paths

    def status(self) -> dict:
        with self.manifest_lock:
            manifest = load_manifest(self.manifest_path)
        counts: dict[str, int] = {}
        items = []
        for base, entry in manifest.get("slides", {}).items():
            state = entry.get("state", "unknown")
            if state == "removed":
                continue
            counts[state] = counts.get(state, 0) + 1
            items.append({
                "base": base,
                "state": state,
                "versions": len(entry.get("versions") or []),
                # 確定版の切替（別タブや自動ワーカーによる）も検知できるように含める
                "current": os.path.basename(entry.get("current_image") or ""),
            })
        items.sort(key=lambda item: item["base"])
        return {
            "total": len(items),
            "counts": counts,
            "slides": items,
            "order": manifest.get("slide_order") or [],
            "session": read_session_status(self.session_dir),
        }

    def prompt(self, base: str) -> tuple[dict, int]:
        with self.manifest_lock:
            manifest = load_manifest(self.manifest_path)
        entry = manifest.get("slides", {}).get(base)
        if entry is None:
            return {"ok": False, "error": f"unknown slide: {base}"}, 404
        candidates = [
            entry.get("prompt_file") or "",
            os.path.join(self.session_dir, "prompts", f"{base}.txt"),
        ]
        for path in candidates:
            real_path = os.path.realpath(path) if path else ""
            allowed = (
                not self.restrict_paths
                or real_path.startswith(self.session_dir + os.sep)
            )
            if real_path and allowed and os.path.isfile(real_path):
                try:
                    with open(real_path, "r", encoding="utf-8") as handle:
                        text = handle.read()
                except OSError as exc:
                    return {"ok": False, "error": str(exc)}, 500
                return {"ok": True, "prompt": text, "file": os.path.basename(real_path)}, 200
        return {
            "ok": False,
            "error": "プロンプトファイルが見つかりません（取り込みスライドには生成プロンプトがありません）",
        }, 404

    def select_version(self, slide: str, rel_image: str) -> tuple[dict, int]:
        image = os.path.realpath(os.path.join(self.session_dir, rel_image))
        if (not image.startswith(self.session_dir + os.sep)
                or not os.path.isfile(image)):
            return {"ok": False, "error": f"image not found: {rel_image}"}, 400
        with self.manifest_lock:
            if slide not in load_manifest(self.manifest_path).get("slides", {}):
                return {"ok": False, "error": f"unknown slide: {slide}"}, 404
            raw_candidate = os.path.join(os.path.dirname(image), "raw", os.path.basename(image))

            def _apply(manifest):
                if slide not in manifest.get("slides", {}):
                    return
                update_entry(
                    manifest,
                    slide,
                    current_image=image,
                    state="validated",
                    raw_image=(raw_candidate if os.path.exists(raw_candidate)
                               else manifest["slides"][slide].get("raw_image")),
                )

            locked_update(self.manifest_path, _apply)
        return {"ok": True}, 200

    def reorder(self, order) -> tuple[dict, int]:
        if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
            return {"ok": False, "error": "order must be a list of slide names"}, 400
        with self.manifest_lock:
            def _apply(manifest):
                known = manifest.get("slides", {})
                # 重複は最初の1件だけ採用し、未指定の既存スライドは ordered_bases が末尾に補う。
                seen = set()
                new_order = []
                for base in order:
                    if base in known and base not in seen:
                        seen.add(base)
                        new_order.append(base)
                manifest["slide_order"] = new_order

            locked_update(self.manifest_path, _apply)
        return {"ok": True}, 200

    def set_removed(self, slide: str, removed: bool) -> tuple[dict, int]:
        with self.manifest_lock:
            if slide not in load_manifest(self.manifest_path).get("slides", {}):
                return {"ok": False, "error": f"unknown slide: {slide}"}, 404

            def _apply(manifest):
                entry = manifest.get("slides", {}).get(slide)
                if not entry:
                    return
                if removed:
                    update_entry(
                        manifest,
                        slide,
                        state_before_removal=entry.get("state", "validated"),
                        state="removed",
                    )
                else:
                    update_entry(
                        manifest,
                        slide,
                        state=entry.get("state_before_removal", "validated"),
                    )

            locked_update(self.manifest_path, _apply)
        return {"ok": True}, 200

    @staticmethod
    def _safe_name(name: str, limit: int = 80) -> str:
        cleaned = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in name)
        return cleaned[:limit] or "ref"

    def save_feedback(self, payload: dict) -> None:
        os.makedirs(self.session_dir, exist_ok=True)
        # 添付画像（赤入れ欄に追加された参照画像）をファイルに落とし、パスへ置き換える
        attachments = payload.get("attachments")
        if isinstance(attachments, dict) and attachments:
            import base64
            from datetime import datetime as _dt
            assets_dir = os.path.join(self.session_dir, "feedback_assets")
            stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            saved_map = {}
            for base, items in attachments.items():
                if not isinstance(items, list):
                    continue
                saved = []
                for i, item in enumerate(items[:8]):  # 1スライド最大8枚
                    if not isinstance(item, dict) or not item.get("data_b64"):
                        continue
                    fname = (f"{stamp}_{self._safe_name(str(base))}_{i}_"
                             f"{self._safe_name(os.path.basename(str(item.get('name', 'ref.png'))))}")
                    path = os.path.join(assets_dir, fname)
                    try:
                        os.makedirs(assets_dir, exist_ok=True)
                        with open(path, "wb") as fh:
                            fh.write(base64.b64decode(item["data_b64"]))
                        saved.append(path)
                    except (OSError, ValueError):
                        continue
                if saved:
                    saved_map[str(base)] = saved
            payload["attachments"] = saved_map
        tmp_path = self.feedback_path + ".tmp"
        with self.manifest_lock:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.feedback_path)
            try:
                from datetime import datetime as _dt
                hist_dir = os.path.join(self.session_dir, "feedback_history")
                os.makedirs(hist_dir, exist_ok=True)
                history_path = os.path.join(
                    hist_dir,
                    _dt.now().strftime("%Y%m%d_%H%M%S_%f") + ".json",
                )
                with open(history_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
            except OSError:
                pass

    def export(self, kind: str) -> tuple[str | None, str | None]:
        if kind not in ("pptx", "pdf"):
            return None, None
        if self.restrict_paths:
            manifest = load_manifest(self.manifest_path)
            for base in ordered_bases(manifest):
                image = manifest["slides"][base].get("current_image")
                if image:
                    real_image = os.path.realpath(image)
                    if not real_image.startswith(self.session_dir + os.sep):
                        print(f"⚠️  export拒否: セッション外の画像参照 {base}")
                        return None, None
        session_name = os.path.basename(self.session_dir)
        out_path = os.path.join(self.session_dir, f"deck_export.{kind}")
        images_dir = os.path.join(self.session_dir, "images")
        with self.manifest_lock:
            try:
                if kind == "pptx":
                    from export_to_pptx import export_to_pptx
                    ok = export_to_pptx(
                        images_dir,
                        out_path,
                        manifest_path=self.manifest_path,
                        allow_partial=True,
                    )
                else:
                    from export_to_pdf import export_to_pdf
                    ok = export_to_pdf(
                        images_dir,
                        out_path,
                        manifest_path=self.manifest_path,
                        allow_partial=True,
                    )
            except Exception as exc:
                print(f"⚠️  export失敗: {exc}")
                ok = False
        return (out_path, f"{session_name}.{kind}") if ok else (None, None)

    def import_files(self, files: list, new_session: bool = False) -> dict:
        """base64 化されたアップロードを取り込み、結果と対象ディレクトリを返す。"""
        import base64
        if new_session:
            from datetime import datetime as _dt
            parent = os.path.dirname(self.session_dir)
            if any(marker in parent for marker in CLOUD_MARKERS):
                parent = os.path.expanduser("~/Documents/TEKION-Slide-Sessions/slides_output")
            target_dir = os.path.join(parent, _dt.now().strftime("%Y-%m-%d_%H%M%S_%f"))
            os.makedirs(target_dir, exist_ok=True)
        else:
            target_dir = self.session_dir
        imports_dir = os.path.join(target_dir, "imports")
        os.makedirs(imports_dir, exist_ok=True)
        added = skipped = 0
        from import_deck import IMAGE_EXTS, import_file
        for item in files:
            name = os.path.basename(str(item.get("name", "upload")))
            ext = os.path.splitext(name)[1].lower()
            if ext not in ({".pptx", ".pdf"} | IMAGE_EXTS):
                continue
            saved = os.path.join(imports_dir, name)
            with open(saved, "wb") as handle:
                handle.write(base64.b64decode(item.get("data_b64", ""), validate=True))
            with self.manifest_lock:
                result = import_file(saved, target_dir)
            added += len(result["added"])
            skipped += len(result["skipped"])
        if new_session and added > 0:
            from session_registry import upsert as _registry_upsert
            _registry_upsert(target_dir)
        return {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "target_dir": target_dir,
        }

    def thumbnail(self, rel: str, width: int, cover: bool = False) -> bytes | None:
        """セッション配下の画像を JPEG サムネイルにして返す。"""
        import hashlib
        if cover:
            manifest = load_manifest(self.manifest_path)
            orig = None
            for base in ordered_bases(manifest):
                image = manifest["slides"][base].get("current_image")
                candidate = os.path.realpath(image) if image else ""
                allowed = (
                    not self.restrict_paths
                    or candidate.startswith(self.session_dir + os.sep)
                )
                if allowed and os.path.isfile(candidate):
                    orig = candidate
                    break
            if not orig:
                return None
            rel_key = f"cover|{orig}"
        else:
            orig = os.path.realpath(os.path.join(self.session_dir, rel))
            if (not orig.startswith(self.session_dir + os.sep)
                    or not os.path.isfile(orig)):
                return None
            rel_key = rel
        width = max(64, min(2560, int(width)))
        cache_dir = os.path.join(self.session_dir, ".thumbs")
        os.makedirs(cache_dir, exist_ok=True)
        key = hashlib.sha1(
            f"{rel_key}|{width}|{os.path.getmtime(orig):.9f}".encode("utf-8")
        ).hexdigest()
        cached = os.path.join(cache_dir, f"{key}.jpg")
        if not os.path.isfile(cached):
            from PIL import Image
            with Image.open(orig) as image:
                image = image.convert("RGB")
                if image.width > width:
                    image = image.resize(
                        (width, int(image.height * width / image.width)),
                        Image.LANCZOS,
                    )
                image.save(cached, format="JPEG", quality=82, optimize=True)
        with open(cached, "rb") as handle:
            return handle.read()


def start_server(session_dir: str, timeout: int, open_browser: bool = True,
                 url_file: str | None = None, exit_on_feedback: bool = True):
    """ダッシュボードサーバを構築して返す（serve_forever は呼び出し側が回す）。

    Returns: SimpleNamespace(httpd, url, received: Event, feedback_path, timer)
    - GET /                : 最新の manifest から HTML を毎回組み立てて返す
    - GET /export/pptx|pdf : その時点の確定版でデッキを書き出してダウンロード（継続）
    - POST /select-version : 表示中バージョンを確定版に（manifest 即反映、継続）
    - POST /feedback       : 修正指示を保存。exit_on_feedback=True ならサーバ終了
      （= 前面実行のエージェントへの完了通知）。False（--persist）なら継続し、
      通知は --await-feedback の待ち受けプロセスが担う（タブが死なない）
    """
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    session_dir = os.path.realpath(os.path.abspath(session_dir))
    manifest_path = os.path.join(session_dir, "manifest.json")
    feedback_path = os.path.join(session_dir, "slide_feedback.json")
    received = threading.Event()
    manifest_lock = threading.Lock()
    service = DashboardService(session_dir, manifest_lock)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=session_dir, **kw)

        def _json_body(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _respond_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file_download(self, path: str, filename: str, mime: str):
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition",
                             f'attachment; filename*=UTF-8\'\'{filename}')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_thumb(self):
            """表示用サムネイルを生成・キャッシュして返す（7MB級PNGをそのまま並べない）。"""
            from urllib.parse import urlparse, parse_qs, unquote
            import hashlib
            parsed = urlparse(self.path)
            rel = unquote(parsed.path[len("/thumb/"):])
            width = 1600
            try:
                width = max(64, min(2560, int(parse_qs(parsed.query).get("w", ["1600"])[0])))
            except (ValueError, TypeError):
                pass
            orig = os.path.normpath(os.path.join(session_dir, rel))
            if not orig.startswith(session_dir + os.sep) or not os.path.exists(orig):
                self.send_error(404)
                return
            cache_dir = os.path.join(session_dir, ".thumbs")
            os.makedirs(cache_dir, exist_ok=True)
            key = hashlib.sha1(f"{rel}|{width}|{int(os.path.getmtime(orig))}".encode()).hexdigest()
            cached = os.path.join(cache_dir, f"{key}.jpg")
            if not os.path.exists(cached):
                from PIL import Image
                with Image.open(orig) as img:
                    img = img.convert("RGB")
                    if img.width > width:
                        img = img.resize((width, int(img.height * width / img.width)),
                                         Image.LANCZOS)
                    img.save(cached, format="JPEG", quality=82, optimize=True)
            with open(cached, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        def _serve_session_thumb(self):
            """他セッションの表紙サムネイル（スタート画面の RECENT SESSIONS 用）。

            任意パスの読み出しにならないよう、台帳（sessions.db）に登録済みの
            セッションだけを対象にする。
            """
            from urllib.parse import urlparse, parse_qs
            import hashlib
            q = parse_qs(urlparse(self.path).query)
            target = os.path.realpath(os.path.abspath((q.get("path") or [""])[0]))
            try:
                width = max(64, min(1024, int((q.get("w") or ["320"])[0])))
            except (ValueError, TypeError):
                width = 320
            try:
                from session_registry import is_registered
                if not is_registered(target):
                    self.send_error(403)
                    return
            except Exception:
                self.send_error(403)
                return
            manifest = load_manifest(os.path.join(target, "manifest.json"))
            cover = None
            for b in ordered_bases(manifest):
                img = manifest["slides"][b].get("current_image")
                if img and os.path.exists(img):
                    cover = img
                    break
            if not cover:
                self.send_error(404)
                return
            cache_dir = os.path.join(target, ".thumbs")
            os.makedirs(cache_dir, exist_ok=True)
            key = hashlib.sha1(
                f"cover|{cover}|{width}|{int(os.path.getmtime(cover))}".encode()).hexdigest()
            cached = os.path.join(cache_dir, f"{key}.jpg")
            if not os.path.exists(cached):
                from PIL import Image
                with Image.open(cover) as img:
                    img = img.convert("RGB")
                    if img.width > width:
                        img = img.resize((width, int(img.height * width / img.width)),
                                         Image.LANCZOS)
                    img.save(cached, format="JPEG", quality=80, optimize=True)
            with open(cached, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/thumb/"):
                try:
                    self._serve_thumb()
                except Exception as e:
                    print(f"⚠️  thumb失敗: {e}")
                    self.send_error(500)
                return
            if self.path.startswith("/session-thumb?"):
                try:
                    self._serve_session_thumb()
                except Exception as e:
                    print(f"⚠️  session-thumb失敗: {e}")
                    self.send_error(500)
                return
            if self.path.startswith("/prompt?"):
                # スライドの生成プロンプトを返す（プロンプトライブラリ表示用）
                from urllib.parse import urlparse, parse_qs
                base = (parse_qs(urlparse(self.path).query).get("slide") or [""])[0]
                result, status = service.prompt(base)
                self._respond_json(result, status)
                return
            if self.path in ("/", "/review.html", "/home"):
                kind = "home" if self.path == "/home" else "deck"
                body = build_html(session_dir, use_thumbs=True, page=kind).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/status":
                self._respond_json(service.status())
            elif self.path == "/sessions":
                try:
                    from session_registry import list_sessions
                    rows = [r for r in list_sessions(limit=15)
                            if os.path.realpath(r["path"]) != session_dir and r["exists"]]
                except Exception:
                    rows = []
                self._respond_json({"sessions": rows})
            elif self.path in ("/export/pptx", "/export/pdf"):
                kind = self.path.rsplit("/", 1)[1]
                out_path, filename = service.export(kind)
                if not out_path:
                    self.send_error(500, "export failed")
                    return
                from urllib.parse import quote
                mime = ("application/vnd.openxmlformats-officedocument.presentationml.presentation"
                        if kind == "pptx" else "application/pdf")
                self._send_file_download(out_path, quote(filename), mime)
                print(f"⤓ デッキを書き出してダウンロード: {filename}")
            else:
                super().do_GET()

        def do_POST(self):
            try:
                payload = self._json_body()
            except (json.JSONDecodeError, ValueError):
                self.send_error(400)
                return

            if self.path == "/select-version":
                try:
                    slide = payload.get("slide", "")
                    rel_image = payload.get("image", "")
                    result, status = service.select_version(slide, rel_image)
                    if status != 200:
                        self._respond_json(result, status)
                        return
                    print(f"📌 確定版を変更: {slide} → {os.path.basename(rel_image)}", flush=True)
                    self._respond_json(result)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self._respond_json({"ok": False,
                                        "error": f"{type(e).__name__}: {e}"}, 500)
                return

            if self.path == "/reorder":
                order = payload.get("order")
                result, status = service.reorder(order)
                if status == 200:
                    print(f"↕️  並び順を保存: {len(order)}枚", flush=True)
                self._respond_json(result, status)
                return

            if self.path in ("/delete-slide", "/restore-slide"):
                slide = payload.get("slide", "")
                restore = self.path == "/restore-slide"
                result, status = service.set_removed(slide, removed=not restore)
                if status == 200:
                    print(("↩️  スライドを復元: " if restore else "🗑  スライドを削除（復元可）: ")
                          + slide, flush=True)
                self._respond_json(result, status)
                return

            if self.path == "/import":
                import base64
                files = payload.get("files", [])
                # トップ（ハブ）からの読み込み = 新しいセッションとして開く。
                # デッキ画面からの読み込み = 現在のデッキ末尾に追加する。
                new_session = payload.get("mode") == "new_session"
                if new_session:
                    from datetime import datetime as _dt
                    parent = os.path.dirname(session_dir)
                    if any(mk in parent for mk in CLOUD_MARKERS):
                        parent = os.path.expanduser(
                            "~/Documents/TEKION-Slide-Sessions/slides_output")
                    target_dir = os.path.join(parent, _dt.now().strftime("%Y-%m-%d_%H%M%S"))
                    os.makedirs(target_dir, exist_ok=True)
                else:
                    target_dir = session_dir
                imports_dir = os.path.join(target_dir, "imports")
                os.makedirs(imports_dir, exist_ok=True)
                added = skipped = 0
                try:
                    from import_deck import import_file, IMAGE_EXTS
                    for item in files:
                        name = os.path.basename(str(item.get("name", "upload")))
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in ({".pptx", ".pdf"} | IMAGE_EXTS):
                            continue
                        saved = os.path.join(imports_dir, name)
                        with open(saved, "wb") as f:
                            f.write(base64.b64decode(item.get("data_b64", "")))
                        with manifest_lock:
                            result = import_file(saved, target_dir)
                        added += len(result["added"])
                        skipped += len(result["skipped"])
                        print(f"📥 取り込み: {name} → {len(result['added'])}枚")
                except Exception as e:
                    print(f"⚠️  取り込み失敗: {type(e).__name__}: {e}")
                    self._respond_json({"ok": False, "error": str(e)}, 500)
                    return
                url = None
                if new_session and added > 0:
                    try:
                        from session_registry import upsert as _registry_up
                        _registry_up(target_dir)
                    except Exception:
                        pass
                    url = _spawn_child_server(target_dir)
                    if not url:
                        self._respond_json({"ok": False,
                                            "error": "新しいセッションの起動に失敗しました"}, 500)
                        return
                    print(f"📂 新しいセッションとして取り込み: {target_dir} → {url}")
                self._respond_json({"ok": True, "added": added, "skipped": skipped, "url": url})
                return

            if self.path == "/open-session":
                target = os.path.realpath(str(payload.get("path", "")))
                if not os.path.exists(os.path.join(target, "manifest.json")):
                    self._respond_json({"ok": False, "error": "session not found"}, 404)
                    return
                child_url = _spawn_child_server(target)
                if not child_url:
                    self._respond_json({"ok": False, "error": "起動がタイムアウトしました"}, 500)
                    return
                print(f"📂 過去セッションを起動: {os.path.basename(target)} → {child_url}")
                self._respond_json({"ok": True, "url": child_url})
                return

            if self.path == "/feedback":
                service.save_feedback(payload)
                self._respond_json({"ok": True})
                received.set()
                if exit_on_feedback:
                    threading.Thread(target=httpd.shutdown, daemon=True).start()
                return

            self.send_error(404)

        def log_message(self, *a):
            pass

    if any(mk in session_dir for mk in CLOUD_MARKERS):
        print("⚠️  セッションがクラウド同期フォルダ配下にあります。同期による manifest の"
              "巻き戻り・書き込み瞬断が起き得ます。セッションはローカルディスクに作り、"
              "完成した PPTX/PDF だけをクラウドへ置くことを推奨します")

    # ポートの固定再利用: 前回と同じポートで開くことで、開きっぱなしのタブが
    # サーバ再起動後に自動復帰できる（Codex 等、サーバを常駐できない環境のため）
    port_file = os.path.join(session_dir, ".dashboard_port")
    want_port = 0
    try:
        with open(port_file, "r", encoding="utf-8") as f:
            want_port = int(f.read().strip())
    except (OSError, ValueError):
        pass
    fallback = False
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", want_port), Handler)
    except OSError:  # 使用中・権限等 → 空きポートに退避
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        fallback = True
    port = httpd.server_address[1]
    # 退避で開いた場合は記録を上書きしない（記録ポートには別のサーバが生きているか、
    # タブがそのポートでの復帰を待っている可能性がある）
    if not fallback:
        try:
            with open(port_file, "w", encoding="utf-8") as f:
                f.write(str(port))
        except OSError:
            pass
    url = f"http://127.0.0.1:{port}/"
    print(f"🌐 スライドダッシュボードを開きます: {url}")
    if url_file:
        try:
            with open(url_file, "w", encoding="utf-8") as f:
                f.write(url)
        except OSError:
            pass
    try:
        from session_registry import upsert as _registry_upsert
        _registry_upsert(session_dir)
    except Exception:
        pass
    print("   修正指示が送信されるとこのプロセスは終了し、指示が保存されます")

    if open_browser:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, url], check=False)
    else:
        print("   （--no-open 指定のため OS ブラウザは開いていません。上記 URL をエージェントの内蔵ブラウザで開いてください）")

    timer = threading.Timer(timeout, lambda: (print("⏰ タイムアウト（フィードバック未受信）"),
                                              httpd.shutdown()))
    timer.daemon = True
    timer.start()

    from types import SimpleNamespace
    return SimpleNamespace(httpd=httpd, url=url, received=received,
                           feedback_path=feedback_path, timer=timer)


def report_feedback(handle) -> int:
    """受信済みフィードバックの要約を表示する。Returns: exit code (0=受信, 2=未受信)"""
    if not handle.received.is_set():
        return 2
    with open(handle.feedback_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    fb = data.get("feedback", {})
    rebuild = data.get("rebuild", [])
    attachments = data.get("attachments", {}) or {}
    global_note = (data.get("global") or "").strip()
    print(f"✅ フィードバック受信: {handle.feedback_path}")
    targets = sorted(set(fb) | set(rebuild) | set(attachments))
    if targets:
        marks = [(b + " [作り直し]" if b in rebuild else b)
                 + (" [添付あり]" if b in attachments else "") for b in targets]
        print(f"   要修正 {len(targets)}枚: {', '.join(marks)}")
    if global_note:
        print(f"   全体指示: {global_note[:120]}")
    if not targets and not global_note:
        print("   全スライド校了")
    return 0


def serve(session_dir: str, timeout: int, open_browser: bool = True,
          url_file: str | None = None, persist: bool = False) -> int:
    """ダッシュボードを開く。

    persist=False: フィードバック受信で終了（前面実行の完了 = 通知）
    persist=True : 受信しても常駐し続ける（通知は --await-feedback プロセスが担う）
    """
    handle = start_server(session_dir, timeout, open_browser, url_file=url_file,
                          exit_on_feedback=not persist)
    if not persist:
        # ユーザーが常駐ハブ（別プロセス）から送信した場合は自前サーバの POST が来ないため、
        # 未処理キュー（feedback_history のカーソル以降）も監視して「受信で終了」を成立させる
        import time as _time
        watch_dir = os.path.realpath(os.path.abspath(session_dir))

        def _watch_external():
            while not handle.received.is_set():
                if pending_feedback(watch_dir):
                    handle.received.set()
                    handle.httpd.shutdown()
                    return
                _time.sleep(2)

        threading.Thread(target=_watch_external, daemon=True).start()
    handle.httpd.serve_forever()
    handle.timer.cancel()
    if persist:
        return 0  # タイムアウトまで常駐するのが正常動作
    return report_feedback(handle)


def await_feedback(session_dir: str, timeout: int) -> int:
    """修正指示の未処理キューを待つ軽量プロセス。

    未処理のフィードバック（feedback_history/ のカーソル以降）が1件でもあれば、
    全件を古い順に stdout へ出して exit 0（= 完了通知でエージェントが動く）。
    **起動前に送られていた未処理分も拾う**（mtime 起点ではなくキュー判定）。
    エージェントは処理後に `--ack-feedback` でカーソルを進め、待ち受けを再起動する。
    """
    import time
    session_dir = os.path.realpath(os.path.abspath(session_dir))
    start = time.time()
    print("⏳ 修正指示の送信を待っています（ダッシュボードは開いたまま使えます）", flush=True)
    while time.time() - start < timeout:
        pend = pending_feedback(session_dir)
        if pend:
            time.sleep(0.3)  # 書き込み完了を待つ
            pend = pending_feedback(session_dir)
            print(f"✅ 未処理フィードバック {len(pend)}件（古い順に処理し、"
                  "完了後に --ack-feedback を実行してください）:")
            for p in pend:
                print(f"--- {os.path.basename(p)}")
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        print(f.read())
                except OSError as e:
                    print(f"(読み取り失敗: {e})")
            return 0
        time.sleep(1)
    print("⏰ タイムアウト（フィードバック未受信）")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Render deck review page from manifest (v6)")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--serve", action="store_true",
                    help="ローカルサーバで開き、修正指示の送信まで待つ")
    ap.add_argument("--persist", action="store_true",
                    help="--serve を常駐化: 修正指示を受信しても終了しない"
                         "（通知は --await-feedback プロセスが担う）")
    ap.add_argument("--await-feedback", action="store_true",
                    help="修正指示の未処理キューを待ち、あれば全件出力して終了する軽量プロセス")
    ap.add_argument("--pending", action="store_true",
                    help="未処理フィードバックの一覧を表示して終了（0=あり, 2=なし）")
    ap.add_argument("--ack-feedback", action="store_true",
                    help="未処理フィードバックを処理済みにする（編集・検証の完了後に実行）")
    ap.add_argument("--serve-timeout", type=int, default=3600, help="--serve の待ち上限秒")
    ap.add_argument("--no-open", action="store_true",
                    help="OS ブラウザを自動で開かない（エージェントの内蔵ブラウザで開く場合用）")
    ap.add_argument("--url-file", help="起動時にダッシュボードURLをこのファイルへ書き出す")
    ap.add_argument("--output", help="静的モードの出力先（デフォルト: <session-dir>/review.html）")
    ap.add_argument("--open", action="store_true", help="静的モードで生成後にブラウザで開く")
    args = ap.parse_args()

    if args.ack_feedback:
        n = ack_feedback(args.session_dir)
        print(f"✅ {n}件のフィードバックを処理済みにしました")
        return 0

    if args.pending:
        pend = pending_feedback(args.session_dir)
        failed = failed_feedback(args.session_dir)
        if not pend and not failed:
            print("未処理のフィードバックはありません")
            return 2
        if pend:
            print(f"未処理フィードバック {len(pend)}件（古い順）:")
            for p in pend:
                print(f"--- {os.path.basename(p)}")
                with open(p, "r", encoding="utf-8") as f:
                    print(f.read())
        if failed:
            print(f"[自動処理失敗] 引き継ぎ待ち {len(failed)}件（古い順）:")
            for p in failed:
                print(f"--- {os.path.basename(p)}")
                with open(p, "r", encoding="utf-8") as f:
                    print(f.read())
        return 0

    if args.await_feedback:
        return await_feedback(args.session_dir, args.serve_timeout)

    if args.serve:
        if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_THREAD_ID"):
            print("ℹ️  Codex 環境を検知: このサーバはバックグラウンド化するとコマンド終了時に"
                  "殺されます。前面実行のまま送信を待つか、生成なら --with-dashboard を使ってください")
        return serve(args.session_dir, args.serve_timeout, open_browser=not args.no_open,
                     url_file=args.url_file, persist=args.persist)

    out_path = args.output or os.path.join(args.session_dir, "review.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_html(args.session_dir))
    print(f"✅ レビューページ生成: {out_path}")
    if args.open:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, out_path], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

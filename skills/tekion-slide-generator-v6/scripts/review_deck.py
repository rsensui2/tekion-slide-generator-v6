#!/usr/bin/env python3
"""TEKION Slide Generator v6 - デッキレビューア「校正室」（Phase 8）

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
from manifest_utils import load_manifest, read_session_status, save_manifest, update_entry

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --paper: #ffffff;        /* 紙: カード面 */
    --desk: #f4f6fa;         /* 机: ページ背景 */
    --ink: #1e293b;          /* 本文 */
    --sub: #64748b;          /* 補助 */
    --line: #e2e8f0;         /* 罫線 */
    --blue: #104f9e;         /* TEKION プライマリ = 選択 */
    --blue-tint: #eff4fe;
    --red: #d93b31;          /* 赤入れ = 修正指示の印 */
    --red-tint: #fdeeec;
    color-scheme: light;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--desk); color: var(--ink);
    font-family: "Hiragino Sans", "Noto Sans JP", sans-serif;
    font-feature-settings: "palt";
  }
  .mono { font-family: "SF Mono", Menlo, monospace; }
  html { scroll-behavior: smooth; }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }

  header.top {
    position: sticky; top: 0; z-index: 20;
    display: flex; align-items: center; gap: 14px;
    padding: 13px 28px;
    background: #ffffffd9; backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--line);
  }
  .brand { display: flex; flex-direction: column; gap: 2px; }
  .brand .eyebrow {
    font-size: 10px; letter-spacing: .32em; color: var(--blue);
    text-transform: uppercase; font-weight: 600;
  }
  .brand h1 { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: .02em; }
  .brand h1 .session { color: var(--sub); font-weight: 400; margin-left: .6em; font-size: 12.5px; }

  .tally { margin-left: auto; display: flex; align-items: baseline; gap: 18px; }
  .tally .item { display: flex; align-items: baseline; gap: 7px; }
  .tally .n { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--blue); }
  .tally .label { font-size: 11px; color: var(--sub); letter-spacing: .12em; }
  .tally .ink .n { color: var(--red); }

  button.export {
    background: var(--blue); color: #fff; border: 0;
    padding: 11px 22px; border-radius: 8px;
    font-size: 13.5px; font-weight: 600; letter-spacing: .04em; cursor: pointer;
    font-family: inherit; box-shadow: 0 1px 2px #104f9e33;
  }
  button.export:hover { background: #1a5fb8; }
  button.export:disabled { background: var(--sub); cursor: default; }
  a.hdr-tool {
    display: inline-flex; align-items: center; gap: 6px;
    border: 1px solid var(--line); background: var(--paper); color: var(--ink);
    font-size: 12px; font-weight: 600; padding: 8px 14px; border-radius: 8px;
    text-decoration: none;
  }
  a.hdr-tool:hover { border-color: var(--blue); color: var(--blue); }
  a.hdr-tool.busy { pointer-events: none; opacity: .5; }
  button:focus-visible, textarea:focus-visible, .rail a:focus-visible,
  a.tool:focus-visible, .vnode:focus-visible {
    outline: 2px solid var(--blue); outline-offset: 2px;
  }

  .banner {
    display: none; align-items: center; gap: 10px;
    margin: 16px 28px 0; padding: 14px 18px;
    background: var(--blue-tint); border: 1px solid #c7d9f5; border-radius: 8px;
    color: var(--blue); font-size: 14px; font-weight: 600;
  }
  .banner.show { display: flex; }

  .wrap { display: grid; grid-template-columns: 190px minmax(0, 1fr);
          max-width: 1560px; margin: 0 auto; }

  /* 索引レール: 現在地がスクロールに連動してハイライトされる */
  nav.rail {
    position: sticky; top: 59px; align-self: start;
    max-height: calc(100vh - 59px); overflow-y: auto;
    padding: 24px 12px 24px 24px;
    display: flex; flex-direction: column; gap: 12px;
  }
  .rail a { display: block; text-decoration: none; color: inherit; position: relative;
            border-radius: 6px; }
  .rail img {
    display: block; width: 100%; border-radius: 4px;
    border: 1px solid var(--line); box-shadow: 0 1px 3px #0f254608;
  }
  .rail a:hover img { border-color: var(--blue); }
  .rail a.active img { box-shadow: 0 0 0 2.5px var(--blue); }
  .rail .tag {
    position: absolute; top: 6px; left: 6px;
    font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 3px;
    background: #fff; color: var(--sub); letter-spacing: .08em;
    border: 1px solid var(--line);
  }
  .rail a.active .tag { background: var(--blue); color: #fff; border-color: var(--blue); }
  .rail a.has-ink img { border-color: var(--red); border-width: 2px; }
  .rail a.has-ink .tag { background: var(--red); color: #fff; border-color: var(--red); }

  main { padding: 24px 28px 80px; display: flex; flex-direction: column; gap: 36px; }

  article.proof {
    background: var(--paper); border: 1px solid var(--line); border-radius: 12px;
    box-shadow: 0 1px 3px #0f254610, 0 8px 24px #0f25460a;
    overflow: hidden; transition: border-color .2s;
    scroll-margin-top: 78px; /* 固定ヘッダーに隠れないオフセット */
  }
  article.proof.has-ink { border-color: var(--red); }

  .proof .head {
    display: flex; align-items: baseline; gap: 14px; padding: 16px 24px 12px;
  }
  .proof .ord {
    font-size: 26px; font-weight: 250; color: #b6c2d4;
    font-variant-numeric: tabular-nums; line-height: 1;
  }
  .proof .id { font-size: 13px; color: var(--ink); font-weight: 600; letter-spacing: .02em; }
  .proof .state { margin-left: auto; font-size: 11px; letter-spacing: .18em;
                  color: var(--sub); font-weight: 600; }
  .proof.has-ink .state { color: var(--red); }

  /* 本体: メイン画像 + バージョンタイムライン（右カラム） */
  .proof .body { display: grid; grid-template-columns: minmax(0,1fr); gap: 16px; padding: 0 24px; }
  .proof .body.with-versions { grid-template-columns: minmax(0,1fr) 240px; }
  .proof img.slide {
    display: block; width: 100%; height: auto; border-radius: 6px;
    border: 1px solid var(--line);
  }
  .proof .maincol .tools { display: flex; align-items: center; gap: 8px; padding-top: 10px; }
  .tool {
    display: inline-flex; align-items: center; gap: 6px;
    border: 1px solid var(--line); background: var(--paper); color: var(--ink);
    font-size: 12px; font-weight: 600; padding: 6px 14px; border-radius: 8px;
    cursor: pointer; font-family: inherit; text-decoration: none;
  }
  .tool:hover { border-color: var(--blue); color: var(--blue); }
  .tool.promote { border-color: var(--blue); color: var(--blue); background: var(--blue-tint); }
  .tool.promote:hover { background: #e0ecfd; }
  .tool[hidden] { display: none; }
  .viewing-label { font-size: 12px; color: var(--sub); font-weight: 600; margin-right: auto; }
  .viewing-label b { color: var(--blue); }

  /* バージョンタイムライン: 縦の接続線で「派生」を可視化 */
  aside.vtree { position: relative; padding-left: 18px; }
  aside.vtree::before {
    content: ""; position: absolute; left: 5px; top: 14px; bottom: 14px;
    width: 2px; background: var(--line); border-radius: 1px;
  }
  .vtree .vtitle { font-size: 10px; letter-spacing: .2em; color: var(--sub);
                   font-weight: 700; margin: 0 0 10px; }
  .vnode { position: relative; margin-bottom: 14px; cursor: pointer; border: 0;
           background: none; padding: 0; width: 100%; text-align: left; font-family: inherit; }
  .vnode::before {  /* タイムラインの節 */
    content: ""; position: absolute; left: -17.5px; top: 12px;
    width: 9px; height: 9px; border-radius: 50%;
    background: var(--paper); border: 2px solid var(--sub);
  }
  .vnode.current::before { background: var(--blue); border-color: var(--blue); }
  .vnode img { display: block; width: 100%; border-radius: 5px;
               border: 1px solid var(--line); transition: box-shadow .15s; }
  .vnode:hover img { border-color: var(--blue); }
  .vnode.current img { box-shadow: 0 0 0 3px var(--blue); border-color: var(--blue); }
  .vnode .vmeta { display: flex; align-items: center; gap: 6px; padding: 5px 2px 0; }
  .vnode .vname { font-size: 11px; font-weight: 700; color: var(--sub); }
  .vnode.current .vname { color: var(--blue); }
  .vnode .badge {
    font-size: 9px; font-weight: 700; letter-spacing: .1em;
    padding: 2px 7px; border-radius: 999px; visibility: hidden;
    background: var(--blue); color: #fff;
  }
  .vnode.current .badge { visibility: visible; }

  /* 修正指示スリップ + 送信 */
  /* 赤入れ欄: 常に赤ペンの体裁（書いた文字も赤） */
  .slip { display: grid; grid-template-columns: 96px minmax(0,1fr) auto;
          margin: 16px 24px 24px; border: 1.5px solid #f0c9c5; border-radius: 10px;
          background: var(--paper); overflow: hidden;
          border-left: 4px solid var(--red); transition: border-color .2s, background .2s; }
  .slip:focus-within { border-color: var(--red); box-shadow: 0 0 0 3px #d93b3122; }
  .proof.has-ink .slip { border-color: var(--red); background: var(--red-tint); }
  .slip .label {
    display: flex; flex-direction: column; justify-content: center; gap: 3px;
    padding: 12px 0 12px 18px; border-right: 1px solid #f0c9c5;
  }
  .slip .label .kanji { font-size: 14px; font-weight: 700; letter-spacing: .28em; color: var(--red); }
  .slip .label .sub { font-size: 9px; color: #e0958f; letter-spacing: .14em; }
  .slip textarea {
    width: 100%; min-height: 68px; resize: vertical; border: 0; background: transparent;
    color: var(--red); padding: 15px 18px; font-size: 15px; line-height: 1.75;
    font-family: inherit; font-weight: 600;
  }
  .slip textarea::placeholder { color: #d8a5a1; font-weight: 400; }
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
    background: #1e293bd9; backdrop-filter: blur(4px) grayscale(1);
    align-items: center; justify-content: center;
  }
  #dead-overlay.show { display: flex; }
  #dead-overlay .box {
    background: var(--paper); border-radius: 16px; padding: 36px 44px;
    max-width: 460px; text-align: center; box-shadow: 0 20px 60px #0009;
  }
  #dead-overlay h2 { margin: 0 0 10px; font-size: 18px; }
  #dead-overlay p { margin: 0; color: var(--sub); font-size: 13.5px; line-height: 1.9; }

  footer.hint { text-align: center; color: var(--sub); font-size: 12.5px;
                padding: 0 24px 48px; letter-spacing: .03em; }

  /* 生成実況 */
  .genprog { font-size: 12.5px; font-weight: 700; color: var(--blue);
             background: var(--blue-tint); border: 1px solid #c7d9f5;
             padding: 7px 14px; border-radius: 999px; }
  .genprog .dot { animation: blink 1.2s infinite; }
  @keyframes blink { 50% { opacity: .2; } }
  #reload-banner {
    display: none; position: fixed; right: 24px; bottom: 24px; z-index: 40;
    background: var(--blue); color: #fff; border: 0; border-radius: 10px;
    padding: 14px 20px; font-size: 13.5px; font-weight: 700; cursor: pointer;
    box-shadow: 0 6px 20px #104f9e55; font-family: inherit;
  }
  #reload-banner.show { display: block; }

  /* スタート画面（スライドが1枚もないとき） */
  .landing { max-width: 860px; margin: 8vh auto 0; padding: 0 24px; text-align: center; }
  .landing h2 { font-size: 22px; font-weight: 700; margin: 0 0 8px; }
  .landing p.lead { color: var(--sub); font-size: 14px; margin: 0 0 32px; }
  .choices { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .choice { display: block; border: 1.5px dashed #b9c6d8; border-radius: 14px;
            background: var(--paper); padding: 40px 28px; cursor: pointer;
            font-size: 16px; font-weight: 700; color: var(--ink); line-height: 1.6; }
  .choice small { display: block; margin-top: 10px; font-size: 12.5px; font-weight: 400;
                  color: var(--sub); line-height: 1.8; }
  .choice:hover { border-color: var(--blue); color: var(--blue); }
  .choice.passive { cursor: default; border-style: solid; border-color: var(--line); }
  .choice.passive:hover { border-color: var(--line); color: var(--ink); }
  .choice .big { font-size: 28px; display: block; margin-bottom: 8px; }
  @media (max-width: 700px) { .choices { grid-template-columns: 1fr; } }

  .brandlogo { height: 15px; width: auto; display: block; margin-bottom: 3px; }
  footer.sites {
    display: flex; align-items: center; justify-content: center; gap: 8px;
    padding: 0 24px 44px; font-size: 12px; color: var(--sub); flex-wrap: wrap;
  }
  footer.sites img { height: 14px; width: auto; opacity: .85; margin-right: 6px; }
  footer.sites a { color: var(--sub); text-decoration: none; font-weight: 600; }
  footer.sites a:hover { color: var(--blue); text-decoration: underline; }
  footer.sites .sep { color: #c4ccd6; }

  /* ステージ実況ヒーロー */
  .stage-hero {
    display: none; align-items: center; gap: 16px;
    max-width: 720px; margin: 20px auto 0; padding: 20px 26px;
    background: var(--paper); border: 1.5px solid #c7d9f5; border-radius: 14px;
    box-shadow: 0 4px 16px #104f9e14;
  }
  .stage-hero.show { display: flex; }
  .stage-hero .pulse {
    width: 44px; height: 44px; border-radius: 50%; flex: 0 0 auto;
    background: radial-gradient(circle at 35% 35%, #3d7fd9, var(--blue));
    animation: heartbeat 1.6s ease-in-out infinite;
  }
  .stage-hero .stxt { display: flex; flex-direction: column; gap: 3px; }
  .stage-hero .stitle { font-size: 16px; font-weight: 700; color: var(--blue); }
  .stage-hero .sdetail { font-size: 12.5px; color: var(--sub); }
  .stage-hero .sdots::after { content: ""; animation: dots 1.5s steps(4) infinite; }
  @keyframes heartbeat { 0%,100% { transform: scale(1); opacity: 1; }
                         50% { transform: scale(1.18); opacity: .75; } }
  @keyframes dots { 0% { content: ""; } 25% { content: "."; } 50% { content: ".."; } 75% { content: "..."; } }

  /* 生成待ちプレースホルダーカード */
  .ph-frame { position: relative; aspect-ratio: 16 / 9; border-radius: 6px;
              border: 1.5px dashed #c3cede; overflow: hidden;
              background: linear-gradient(110deg, #eef2f8 35%, #f8fafd 50%, #eef2f8 65%);
              background-size: 220% 100%; animation: shimmer 1.8s linear infinite; }
  @keyframes shimmer { to { background-position-x: -220%; } }
  .ph-frame .ph-label { position: absolute; inset: 0; display: flex; flex-direction: column;
                        align-items: center; justify-content: center; gap: 8px;
                        color: #9aa8bb; font-size: 13px; font-weight: 600; }
  .ph-frame .ph-num { font-size: 34px; font-weight: 200; color: #c3cede; }
  .proof.placeholder { border-style: dashed; box-shadow: none; }
  .proof.placeholder .state { color: #9aa8bb; }
  .rail .ph-thumb { aspect-ratio: 16 / 9; border-radius: 4px; border: 1.5px dashed #c3cede;
                    background: #eef2f8; }
  @media (prefers-reduced-motion: reduce) {
    .ph-frame, .stage-hero .pulse { animation: none; }
  }

  /* Ryoko バナーボタン（スタート画面・横並び） */
  .choices.banner { grid-template-columns: 1fr 1fr; gap: 20px; max-width: 900px; margin: 0 auto; }
  @media (max-width: 760px) { .choices.banner { grid-template-columns: 1fr; } }
  .banner-btn { display: block; border: 0; padding: 0; background: none; cursor: pointer;
                border-radius: 18px; overflow: hidden; width: 100%; line-height: 0;
                box-shadow: 0 6px 18px #0f254626; transition: transform .15s, box-shadow .15s; }
  .banner-btn img { display: block; width: 100%; height: auto; }
  .banner-btn:hover { transform: translateY(-3px); box-shadow: 0 10px 28px #0f254633; }
  .banner-btn:active { transform: translateY(0); }
  .create-hint { margin: 18px auto 0; max-width: 640px; padding: 16px 20px;
                 background: var(--orange-tint, #fef4e6); border: 1px solid #f0c9a0;
                 border-radius: 10px; color: #9a5b00; font-size: 14px; font-weight: 600;
                 line-height: 1.8; }
  .create-hint[hidden] { display: none; }

  /* ファイル読み込み（ドラッグ&ドロップ） */
  label.hdr-tool { cursor: pointer; }
  #drop-overlay {
    display: none; position: fixed; inset: 0; z-index: 50;
    background: #104f9ecc; backdrop-filter: blur(3px);
    align-items: center; justify-content: center;
  }
  #drop-overlay.show { display: flex; }
  #drop-overlay .box {
    border: 3px dashed #ffffffaa; border-radius: 16px; padding: 48px 64px;
    color: #fff; font-size: 18px; font-weight: 700; letter-spacing: .06em;
    text-align: center; line-height: 2;
  }
  #drop-overlay .box small { font-size: 12.5px; font-weight: 400; opacity: .85; }

  @media (prefers-reduced-motion: no-preference) {
    article.proof { animation: rise .45s ease both; }
    article.proof:nth-child(2) { animation-delay: .05s; }
    article.proof:nth-child(3) { animation-delay: .1s; }
    article.proof:nth-child(n+4) { animation-delay: .15s; }
    @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
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
  }
</style>
</head>
<body>
<header class="top">
  <div class="brand">
__BRAND_TOP__
    <h1>スライドダッシュボード<span class="session mono">__SESSION_NAME__</span></h1>
  </div>
  <span class="genprog mono" id="gen-progress" hidden></span>
  <div class="tally">
    <span class="item ok"><span class="n" id="n-ok">__COUNT__</span><span class="label">校了</span></span>
    <span class="item ink"><span class="n" id="n-ink">0</span><span class="label">要修正</span></span>
  </div>
  <label class="hdr-tool" id="import-btn">＋ 読み込み<input type="file" id="file-input" multiple accept=".pptx,.pdf,.png,.jpg,.jpeg,.webp" hidden></label>
  <a class="hdr-tool" id="dl-pptx" href="/export/pptx" onclick="busyExport(this)">⤓ PPTX</a>
  <a class="hdr-tool" id="dl-pdf" href="/export/pdf" onclick="busyExport(this)">⤓ PDF</a>
  <button class="export" id="submit-btn" onclick="submitAll()">まとめて修正依頼する</button>
</header>

<div class="banner" id="done-banner">✓ 送信しました — Claude が修正を開始します。このタブは閉じて構いません。</div>

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
  <h2>このタブは古いセッションです</h2>
  <p>ダッシュボードのサーバとの接続が切れました。<br>
  最新のタブを使うか、エージェントに「ダッシュボードを開いて」と<br>頼んで開き直してください。このタブは閉じて構いません。</p>
</div></div>

<button id="reload-banner" onclick="location.reload()">デッキが更新されました — 再読み込み</button>
<div id="drop-overlay"><div class="box">ここにドロップして読み込み<br>
<small>.pptx / .pdf は1枚ずつのスライドに分解 / PNG・JPG は1枚のスライドとして追加</small></div></div>

<footer class="hint">
  バージョンを選んで比較し、良い版を「確定にする」。修正指示は各スライドから即送信、または右上からまとめて依頼。
</footer>

<script>
const TOTAL = __COUNT__;
const SESSION_DIR = __SESSION_DIR_JSON__;
const SERVED = location.protocol.startsWith('http');
if (!SERVED) {
  ['dl-pptx', 'dl-pdf'].forEach(id => document.getElementById(id).style.display = 'none');
  document.querySelectorAll('.slip .send').forEach(b => b.style.display = 'none');
}
/* --- 生成実況: /status をポーリングして進捗表示・自動更新 --- */
let lastSig = null;
let submitted = false;
let pollFails = 0;
function hasUserInput() {
  if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') return true;
  return [...document.querySelectorAll('textarea[data-slide]')].some(t => t.value.trim());
}
const STAGE_LABELS = {
  planning:  ['スライド構成を執筆中', 'Claude が内容を設計しています'],
  prompting: ['画像プロンプトを生成中', 'デザイン指示を組み立てています'],
  prompted:  ['画像生成の開始を待機中', 'まもなく並列生成が始まります'],
  generating: ['スライドを生成中', ''],
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
  const preStages = ['planning', 'prompting', 'prompted'];
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
  if (!SERVED || submitted) return;
  try {
    const res = await fetch('/status');
    const st = await res.json();
    updateGenProgress(st);
    const sig = st.slides.map(s => s.base + ':' + s.state + ':' + s.versions).join('|');
    if (lastSig === null) { lastSig = sig; return; }
    if (sig !== lastSig) {
      lastSig = sig;
      if (!hasUserInput()) location.reload();
      else document.getElementById('reload-banner').classList.add('show');
    }
    pollFails = 0;
  } catch (e) {
    // 連続で応答が無ければサーバ消失 = このタブは古い。明示して操作を止める
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
  const valid = files.filter(f => IMPORT_EXTS.some(x => f.name.toLowerCase().endsWith(x)));
  if (!valid.length) { alert('対応形式: ' + IMPORT_EXTS.join(' / ')); return; }
  const btn = document.getElementById('import-btn');
  btn.textContent = '読み込み中…';
  try {
    const payload = { files: [] };
    for (const f of valid) payload.files.push({ name: f.name, data_b64: await readAsB64(f) });
    const res = await fetch('/import', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    const result = await res.json();
    if (!res.ok || !result.ok) throw new Error(result.error || res.status);
    alert(result.added + '枚を取り込みました' +
          (result.skipped ? '（' + result.skipped + '枚は画像が抽出できずスキップ）' : ''));
    location.reload();
  } catch (e) {
    btn.textContent = '＋ 読み込み';
    alert('読み込みに失敗しました: ' + e.message);
  }
}
function toggleCreateHint() {
  const el = document.getElementById('create-hint');
  if (el) el.hidden = !el.hidden;
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
  const c = card(node);
  const prev = c.querySelector('.vnode.current');
  if (prev === node) return;
  applySelection(c, node);  // 楽観的に即反映
  if (!SERVED) return;      // file:// では表示切替のみ（保存はできない）
  const post = () => fetch('/select-version', { method: 'POST',
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
  } catch (e) {
    if (prev) applySelection(c, prev);  // 失敗したら元に戻す
    alert('確定の保存に失敗しました: ' + e.message);
  }
}

/* --- 索引レールのスクロール連動 --- */
const railMap = {};
document.querySelectorAll('.rail a').forEach(a => railMap[a.getAttribute('href').slice(1)] = a);
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
function markCard(el) {
  const inked = el.value.trim().length > 0;
  const c = card(el);
  c.classList.toggle('has-ink', inked);
  c.querySelector('.state').textContent = inked ? '要修正' : '校了';
  const railItem = railMap[c.id];
  if (railItem) {
    railItem.classList.toggle('has-ink', inked);
    railItem.querySelector('.tag').textContent = inked ? '修正' : railItem.dataset.ord;
  }
  const n = document.querySelectorAll('.proof.has-ink').length;
  document.getElementById('n-ink').textContent = n;
  document.getElementById('n-ok').textContent = TOTAL - n;
}
function freeze(message) {
  submitted = true;
  document.getElementById('done-banner').classList.add('show');
  document.querySelectorAll('textarea, .slip .send').forEach(t => t.disabled = true);
  const btn = document.getElementById('submit-btn');
  btn.disabled = true; btn.textContent = message;
}
async function post(payload) {
  const res = await fetch('/feedback', { method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload) });
  if (!res.ok) throw new Error(res.status);
}
async function sendOne(btn) {
  const c = card(btn);
  const text = c.querySelector('textarea').value.trim();
  if (!text) { alert('修正指示を書いてから送信してください'); return; }
  btn.disabled = true; btn.textContent = '…';
  try {
    await post({ session_dir: SESSION_DIR, feedback: { [c.dataset.slide]: text } });
    freeze('修正を依頼しました');
  } catch (e) {
    btn.disabled = false; btn.textContent = '⏎';
    alert('送信に失敗しました（サーバが終了している可能性）');
  }
}
function collectAll() {
  const out = {};
  document.querySelectorAll('textarea[data-slide]').forEach(t => {
    if (t.value.trim()) out[t.dataset.slide] = t.value.trim();
  });
  return { session_dir: SESSION_DIR, feedback: out };
}
async function submitAll() {
  const payload = collectAll();
  const n = Object.keys(payload.feedback).length;
  const btn = document.getElementById('submit-btn');
  if (SERVED) {
    btn.disabled = true; btn.textContent = '送信中…';
    try {
      await post(payload);
      freeze(n ? n + '枚の修正を依頼しました' : '全スライド校了で送信しました');
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
    alert(n ? n + '枚分の修正指示をダウンロードしました。Claude に渡してください'
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
      </div>
      <div class="body__BODY_CLASS__">
        <div class="maincol">
          <img class="slide" src="__CUR_SRC__" alt="__NAME__" loading="lazy">
          <div class="tools">
            <span class="viewing-label">確定版: <b>__CUR_LABEL__</b></span>
            <a class="tool dl" href="__CUR_ORIG__" download="__CUR_FILE__">⤓ PNG保存</a>
          </div>
        </div>
__VTREE__
      </div>
      <div class="slip">
        <div class="label"><span class="kanji">修正指示</span><span class="sub">FEEDBACK</span></div>
        <textarea data-slide="__NAME__" placeholder="このスライドへの修正指示（空欄なら校了）" oninput="markCard(this)"></textarea>
        <button class="send" onclick="sendOne(this)" title="この修正を依頼" aria-label="この修正を依頼">⏎</button>
      </div>
    </article>"""

RAIL_TEMPLATE = """    <a href="#p-__NAME__" data-ord="__ORD__"><span class="tag">__ORD__</span><img src="__RAIL_SRC__" alt="__NAME__ サムネイル"></a>"""

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

PLACEHOLDER_RAIL_TEMPLATE = """    <a href="#p-__NAME__" data-ord="__ORD__"><span class="tag">__ORD__</span><div class="ph-thumb"></div></a>"""


THUMB_MAIN_W = 1600   # メイン表示
THUMB_VER_W = 480     # バージョンタイムライン
THUMB_RAIL_W = 320    # 索引レール


def build_html(session_dir: str, use_thumbs: bool = False) -> str:
    manifest_path = os.path.join(session_dir, "manifest.json")
    manifest = load_manifest(manifest_path)
    slides = manifest.get("slides", {})

    def natural_key(s: str):
        import re
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"([0-9]+)", s)]

    ordered = sorted(slides.items(),
                     key=lambda kv: (0 if "course_title" in kv[0] else 1, natural_key(kv[0])))

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
        cur_rel = os.path.relpath(current, session_dir)

        def disp(rel, w):
            # 表示はサムネイル(serve時)、ダウンロード等は原本を使う
            return f"thumb/{_q(rel)}?w={w}" if use_thumbs else rel

        cur_label = f"v{versions.index(current) + 1}"

        vnodes = []
        for i, v in enumerate(versions, start=1):
            rel = os.path.relpath(v, session_dir)
            classes = "vnode"
            if v == current:
                classes += " current"
            vnodes.append(
                f'          <button class="{classes}" data-src="{html.escape(disp(rel, THUMB_MAIN_W))}" '
                f'data-orig="{html.escape(rel)}" '
                f'data-file="{html.escape(os.path.basename(v))}" data-label="v{i}" '
                f'onclick="selectVersion(this)">'
                f'<img src="{html.escape(disp(rel, THUMB_VER_W))}" loading="lazy" alt="v{i}">'
                f'<span class="vmeta"><span class="vname">v{i}</span>'
                f'<span class="badge">確定</span></span></button>')

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

    landing = ""
    if not cards:
        import base64 as _b64
        ui_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "assets", "ui")

        def _data_uri(name):
            p = os.path.join(ui_dir, name)
            if os.path.exists(p):
                return "data:image/png;base64," + _b64.b64encode(open(p, "rb").read()).decode()
            return None

        btn_import = _data_uri("btn_import.png")
        btn_create = _data_uri("btn_create.png")
        if btn_import and btn_create:
            landing = f"""    <section class="landing">
      <h2>デッキをはじめる</h2>
      <p class="lead">既存デッキの改修も、ゼロからの生成も、ここが起点になります。</p>
      <div class="choices banner">
        <label class="banner-btn" for="file-input" title=".pptx / .pdf / 画像 を選択（ドロップでも可）">
          <img src="{btn_import}" alt="既存デッキを読み込む"></label>
        <button class="banner-btn" onclick="toggleCreateHint()" title="新しく作る">
          <img src="{btn_create}" alt="新しく作る"></button>
      </div>
      <p class="create-hint" id="create-hint" hidden>✨ Cursor / Claude に「◯◯のスライドを作って」と指示してください。<br>生成が始まると、ここに実況が流れます。</p>
    </section>"""
        else:
            landing = """    <section class="landing">
      <h2>デッキをはじめる</h2>
      <p class="lead">既存デッキの改修も、ゼロからの生成も、ここが起点になります。</p>
      <div class="choices">
        <label class="choice" for="file-input"><span class="big">📂</span>既存デッキを読み込む
          <small>.pptx / .pdf / 画像 をここにドロップ、<br>またはクリックして選択</small></label>
        <div class="choice passive"><span class="big">✨</span>新しく作る
          <small>Cursor / Claude にそのまま指示してください。<br>生成が始まると、ここに実況が流れます</small></div>
      </div>
    </section>"""

    session_name = os.path.basename(os.path.abspath(session_dir))

    # TEKION ロゴ（あれば data URI で埋め込み、無ければテキストにフォールバック）
    import base64 as _b64logo
    logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "assets", "ui", "tekion_logo.png")
    if os.path.exists(logo_path):
        logo_uri = "data:image/png;base64," + _b64logo.b64encode(open(logo_path, "rb").read()).decode()
        brand_top = f'    <img class="brandlogo" src="{logo_uri}" alt="TEKION Group">'
        sites_logo = f'  <img src="{logo_uri}" alt="TEKION Group">\n'
    else:
        brand_top = '    <span class="eyebrow">TEKION Slide Generator</span>'
        sites_logo = ''

    page = PAGE_TEMPLATE
    for k, v in {
        "__BRAND_TOP__": brand_top,
        "__SITES_LOGO__": sites_logo,
        "__TITLE__": html.escape(f"スライドダッシュボード — {session_name}"),
        "__SESSION_NAME__": html.escape(session_name),
        "__COUNT__": str(rendered_total),
        "__RAIL__": "\n".join(rail),
        "__CARDS__": landing if landing else "\n".join(cards),
        "__SESSION_DIR_JSON__": json.dumps(os.path.abspath(session_dir), ensure_ascii=False),
    }.items():
        page = page.replace(k, v)
    return page


def start_server(session_dir: str, timeout: int, open_browser: bool = True):
    """ダッシュボードサーバを構築して返す（serve_forever は呼び出し側が回す）。

    Returns: SimpleNamespace(httpd, url, received: Event, feedback_path, timer)
    - GET /                : 最新の manifest から HTML を毎回組み立てて返す
    - GET /export/pptx|pdf : その時点の確定版でデッキを書き出してダウンロード（継続）
    - POST /select-version : 表示中バージョンを確定版に（manifest 即反映、継続）
    - POST /feedback       : 修正指示を保存してサーバ終了（= 完了通知でエージェントが動く）
    """
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    session_dir = os.path.abspath(session_dir)
    manifest_path = os.path.join(session_dir, "manifest.json")
    feedback_path = os.path.join(session_dir, "slide_feedback.json")
    received = threading.Event()
    manifest_lock = threading.Lock()

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

        def do_GET(self):
            if self.path.startswith("/thumb/"):
                try:
                    self._serve_thumb()
                except Exception as e:
                    print(f"⚠️  thumb失敗: {e}")
                    self.send_error(500)
                return
            if self.path in ("/", "/review.html"):
                body = build_html(session_dir, use_thumbs=True).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/status":
                with manifest_lock:
                    manifest = load_manifest(manifest_path)
                slides = manifest.get("slides", {})
                counts = {}
                items = []
                for base, e in slides.items():
                    state = e.get("state", "unknown")
                    counts[state] = counts.get(state, 0) + 1
                    items.append({"base": base, "state": state,
                                  "versions": len(e.get("versions") or [])})
                items.sort(key=lambda x: x["base"])
                self._respond_json({"total": len(items), "counts": counts, "slides": items,
                                    "session": read_session_status(session_dir)})
            elif self.path in ("/export/pptx", "/export/pdf"):
                kind = self.path.rsplit("/", 1)[1]
                session_name = os.path.basename(session_dir)
                out_path = os.path.join(session_dir, f"deck_export.{kind}")
                images_dir = os.path.join(session_dir, "images")
                with manifest_lock:
                    try:
                        if kind == "pptx":
                            from export_to_pptx import export_to_pptx
                            ok = export_to_pptx(images_dir, out_path,
                                                manifest_path=manifest_path, allow_partial=True)
                        else:
                            from export_to_pdf import export_to_pdf
                            ok = export_to_pdf(images_dir, out_path,
                                               manifest_path=manifest_path, allow_partial=True)
                    except Exception as e:
                        print(f"⚠️  export失敗: {e}")
                        ok = False
                if not ok:
                    self.send_error(500, "export failed")
                    return
                from urllib.parse import quote
                mime = ("application/vnd.openxmlformats-officedocument.presentationml.presentation"
                        if kind == "pptx" else "application/pdf")
                self._send_file_download(out_path, quote(f"{session_name}.{kind}"), mime)
                print(f"⤓ デッキを書き出してダウンロード: {session_name}.{kind}")
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
                    image = os.path.normpath(os.path.join(session_dir, rel_image))
                    # session_dir 配下の実在ファイルのみ許可
                    if not image.startswith(session_dir + os.sep) or not os.path.exists(image):
                        self._respond_json({"ok": False, "error": f"image not found: {rel_image}"}, 400)
                        return
                    with manifest_lock:
                        manifest = load_manifest(manifest_path)
                        if slide not in manifest.get("slides", {}):
                            self._respond_json({"ok": False, "error": f"unknown slide: {slide}"}, 404)
                            return
                        raw_candidate = os.path.join(os.path.dirname(image), "raw",
                                                     os.path.basename(image))
                        update_entry(manifest, slide, current_image=image, state="validated",
                                     raw_image=raw_candidate if os.path.exists(raw_candidate)
                                     else manifest["slides"][slide].get("raw_image"))
                        save_manifest(manifest_path, manifest)
                    print(f"📌 確定版を変更: {slide} → {os.path.basename(image)}", flush=True)
                    self._respond_json({"ok": True})
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    self._respond_json({"ok": False,
                                        "error": f"{type(e).__name__}: {e}"}, 500)
                return

            if self.path == "/import":
                import base64
                files = payload.get("files", [])
                imports_dir = os.path.join(session_dir, "imports")
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
                            result = import_file(saved, session_dir)
                        added += len(result["added"])
                        skipped += len(result["skipped"])
                        print(f"📥 取り込み: {name} → {len(result['added'])}枚")
                except Exception as e:
                    print(f"⚠️  取り込み失敗: {type(e).__name__}: {e}")
                    self._respond_json({"ok": False, "error": str(e)}, 500)
                    return
                self._respond_json({"ok": True, "added": added, "skipped": skipped})
                return

            if self.path == "/feedback":
                with open(feedback_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                self._respond_json({"ok": True})
                received.set()
                threading.Thread(target=httpd.shutdown, daemon=True).start()
                return

            self.send_error(404)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"🌐 校正室を開きます: {url}")
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
        fb = json.load(f).get("feedback", {})
    print(f"✅ フィードバック受信: {handle.feedback_path}")
    if fb:
        print(f"   要修正 {len(fb)}枚: {', '.join(sorted(fb))}")
    else:
        print("   全スライド校了")
    return 0


def serve(session_dir: str, timeout: int, open_browser: bool = True) -> int:
    """ダッシュボードを開き、フィードバック受信までブロックする。"""
    handle = start_server(session_dir, timeout, open_browser)
    handle.httpd.serve_forever()
    handle.timer.cancel()
    return report_feedback(handle)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render deck review page from manifest (v6)")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--serve", action="store_true",
                    help="ローカルサーバで開き、修正指示の送信まで待つ")
    ap.add_argument("--serve-timeout", type=int, default=3600, help="--serve の待ち上限秒")
    ap.add_argument("--no-open", action="store_true",
                    help="OS ブラウザを自動で開かない（エージェントの内蔵ブラウザで開く場合用）")
    ap.add_argument("--output", help="静的モードの出力先（デフォルト: <session-dir>/review.html）")
    ap.add_argument("--open", action="store_true", help="静的モードで生成後にブラウザで開く")
    args = ap.parse_args()

    if args.serve:
        if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_THREAD_ID"):
            print("ℹ️  Codex 環境を検知: このサーバはバックグラウンド化するとコマンド終了時に"
                  "殺されます。前面実行のまま送信を待つか、生成なら --with-dashboard を使ってください")
        return serve(args.session_dir, args.serve_timeout, open_browser=not args.no_open)

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

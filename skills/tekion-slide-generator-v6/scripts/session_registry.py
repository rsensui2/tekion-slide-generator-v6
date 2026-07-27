#!/usr/bin/env python3
"""TEKION Slide Generator v6 - グローバルセッション台帳。

すべてのセッション（デッキ）を ~/.tekion-slides/sessions.db (SQLite) に自動記録する。
manifest が保存されるたびに upsert されるため、どのフォルダに作られたセッションでも
後から検索・再開できる。プロジェクトフォルダの構成に依存しない。

使い方（エージェント向け）:
    # 一覧・検索（タイトル/パスの部分一致、日数絞り込み）
    python3 session_registry.py --list
    python3 session_registry.py --list --query シンデレラ
    python3 session_registry.py --list --days 7

    # 既存フォルダの一括取り込み（初回移行用）
    python3 session_registry.py --scan ~/Desktop --scan ~/Documents

    # 見つけたセッションを開く:
    python3 review_deck.py --session-dir <path> --serve
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("TEKION_SLIDES_HOME", "~/.tekion-slides")).expanduser() / "sessions.db"

_SCHEMA = """CREATE TABLE IF NOT EXISTS sessions (
    path TEXT PRIMARY KEY,
    title TEXT,
    slides INTEGER,
    created_at TEXT,
    updated_at TEXT
)"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=3)
    conn.execute(_SCHEMA)
    return conn


_GENERIC = re.compile(r"^(00_cover|course_title|9[89]_|\d+_?$)")


def derive_title(session_dir: str, manifest: dict | None) -> str:
    """デッキの題名を推定する。優先: 作品/構成JSONの題名 → スライド名 → フォルダ名。"""
    stem = None
    # 0) 台帳のプロファイルが題名を持っていればそれが最優先（作者が書いた正式な題名）
    if manifest:
        declared = ((manifest.get("deck_profile") or {}).get("title") or "").strip()
        if declared:
            return f"{declared[:60]}（{Path(session_dir).name}）"
    # 1) slides_plan.json の表紙タイトル（最も正確）
    plan_path = os.path.join(session_dir, "json", "slides_plan.json")
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        slides = plan.get("slides", plan if isinstance(plan, list) else [])
        if slides:
            t = (slides[0].get("title") or "").strip()
            if t:
                stem = t[:40]
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    # 2) スライド名の日本語ステム
    if not stem and manifest:
        stems = []
        for base in manifest.get("slides", {}):
            s = re.sub(r"_\d+$", "", base)          # 連番を除去
            s = re.sub(r"^\d+_", "", s)             # 先頭番号を除去
            if s and not _GENERIC.match(base) and not s.isascii():
                stems.append(s)
        if stems:
            # 最頻の日本語ステム（例: さるかに合戦 / AI駆動開発経営Vol3_...）
            stem = max(set(stems), key=stems.count)
    parent = Path(session_dir).parent
    project = parent.parent.name if parent.name == "slides_output" else parent.name
    ts = Path(session_dir).name
    if stem:
        return f"{stem}（{project} / {ts}）"
    return f"{project} / {ts}"


def upsert(session_dir: str, manifest: dict | None = None) -> None:
    """セッションを台帳に登録/更新する（ベストエフォート。呼び出し元を止めない）。"""
    session_dir = os.path.realpath(os.path.abspath(session_dir))
    if manifest is None:
        mp = os.path.join(session_dir, "manifest.json")
        try:
            with open(mp, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            manifest = {}
    slides = len(manifest.get("slides", {}))
    title = derive_title(session_dir, manifest)
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO sessions(path, title, slides, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 title=excluded.title, slides=excluded.slides, updated_at=excluded.updated_at""",
            (session_dir, title, slides, now, now))


def session_id(session_dir: str) -> str:
    """実体パスから、URL に使う安定したセッション ID を返す。"""
    real = os.path.realpath(os.path.abspath(session_dir))
    return hashlib.sha1(real.encode("utf-8")).hexdigest()[:12]


def resolve_session_id(sid: str) -> str | None:
    """台帳から sid を逆引きする。

    sid はパスそのものを公開しないための識別子であり、台帳に登録済みで
    manifest.json が現在も存在するセッションだけを返す。万一 12 桁 SID が
    衝突した場合は、誤ったセッションを配信しないよう解決失敗にする。
    """
    if not re.fullmatch(r"[0-9a-f]{12}", sid or ""):
        return None
    autodiscover()  # 別ランタイムが作ったセッションの URL も解決できるようにする
    with _conn() as conn:
        rows = conn.execute("SELECT path FROM sessions").fetchall()
    matches = []
    for (path,) in rows:
        real = os.path.realpath(os.path.abspath(path))
        if session_id(real) == sid and os.path.isfile(os.path.join(real, "manifest.json")):
            matches.append(real)
    return matches[0] if len(set(matches)) == 1 else None


# 別ランタイム（コンテナ等）が作ったセッションを拾うための監視ルート。台帳 DB は
# ランタイムごとに別物になるが、セッションのディレクトリ自体が共有ストレージ上に
# あるなら、ここを浅くスキャンすればハブの一覧に自動で現れる。
#
# 既定は空（このツール単体では何も覗かない）。設定は2通り:
#   環境変数 TEKION_WATCH_ROOTS に : 区切りのパス
#   ~/.tekion-slides/watch_roots に1行1パス（常駐ハブは launchd 起動で環境変数を
#   持てないため、こちらが実用的）
_AUTODISCOVER_INTERVAL = 60.0
_last_autodiscover = 0.0


def watch_roots() -> list[str]:
    roots = [p for p in os.environ.get("TEKION_WATCH_ROOTS", "").split(os.pathsep)
             if p.strip()]
    config = DB_PATH.parent / "watch_roots"
    try:
        with config.open("r", encoding="utf-8") as handle:
            roots += [line.strip() for line in handle
                      if line.strip() and not line.startswith("#")]
    except OSError:
        pass
    return roots


def autodiscover() -> None:
    """監視ルートを浅くスキャンし、未登録のセッションを台帳に取り込む（ベストエフォート）。"""
    global _last_autodiscover
    import time
    now = time.monotonic()
    if now - _last_autodiscover < _AUTODISCOVER_INTERVAL:
        return
    _last_autodiscover = now
    for root in watch_roots():
        path = Path(root).expanduser()
        if not path.is_dir():
            continue
        try:
            scan(str(path), max_depth=2)
        except Exception:
            pass  # 一覧表示を止めない


def list_sessions(query: str | None = None, days: int | None = None, limit: int = 30) -> list[dict]:
    autodiscover()
    sql = "SELECT path, title, slides, created_at, updated_at FROM sessions"
    cond, params = [], []
    if query:
        cond.append("(title LIKE ? OR path LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    if days:
        cond.append("updated_at >= ?")
        params.append((datetime.now() - timedelta(days=days)).isoformat(timespec="seconds"))
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for path, title, slides, created, updated in rows:
        result.append({"path": path, "sid": session_id(path), "title": title, "slides": slides,
                       "created_at": created, "updated_at": updated,
                       "exists": os.path.exists(os.path.join(path, "manifest.json"))})
    return result


def is_registered(path: str) -> bool:
    """台帳に登録済みのセッションか（他セッションのサムネイル配信の安全確認に使う）。"""
    path = os.path.realpath(os.path.abspath(path))
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM sessions WHERE path=?", (path,)).fetchone()
    return bool(row)


def _declares_profile(manifest_path: str) -> bool:
    """台帳が deck_profile を名乗っているか（セッションディレクトリの印）。"""
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and isinstance(data.get("deck_profile"), dict)
    except (OSError, json.JSONDecodeError):
        return False


def scan(root: str, max_depth: int = 6) -> int:
    """root 以下の manifest.json を探して一括登録する（初回移行用）。"""
    root_path = Path(root).expanduser()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root_path):
        depth = len(Path(dirpath).relative_to(root_path).parts)
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if d not in ("node_modules", ".git", "__pycache__", ".thumbs", "raw",
                                    "images", "superseded")]
        # 「そのディレクトリが何であるか」の印を必ず1つ求める（manifest.json という
        # 名前だけを条件にすると、無関係な JSON 置き場まで拾ってしまう）。
        # 印は2つ: 従来の slides_output/<timestamp>/ か、台帳が自分でプロファイルを
        # 名乗っていること。後者があるので、別ジャンルのデッキも印の追加なしに載る
        if "manifest.json" in filenames and (
                Path(dirpath).parent.name == "slides_output"
                or _declares_profile(os.path.join(dirpath, "manifest.json"))):
            try:
                upsert(dirpath)
                count += 1
            except Exception:
                pass
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="Global session registry (v6)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--query")
    ap.add_argument("--days", type=int)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--scan", action="append", help="ルート以下の既存セッションを一括登録（複数可）")
    ap.add_argument("--register", help="単一セッションを登録")
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    args = ap.parse_args()

    if args.register:
        # 登録直後からハブ（/s/<sid>/）が配信できるよう、空の manifest を先に作る。
        # ハブの SID 解決は「manifest.json が実在する登録済みセッションのみ」の
        # fail-closed 設計のため、これが無いと生成開始まで 404 になる
        target = os.path.realpath(os.path.abspath(args.register))
        mp = os.path.join(target, "manifest.json")
        if os.path.isdir(target) and not os.path.exists(mp):
            try:
                with open(mp, "w", encoding="utf-8") as f:
                    json.dump({"version": 1,
                               "created_at": datetime.now().isoformat(timespec="seconds"),
                               "slides": {}}, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
        upsert(args.register)
        print(session_id(args.register))
        return 0
    if args.scan:
        total = sum(scan(r) for r in args.scan)
        print(f"✅ {total} セッションを登録しました → {DB_PATH}")
        return 0

    rows = list_sessions(args.query, args.days, args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("セッションはまだ登録されていません（--scan で既存フォルダを取り込めます）")
        return 0
    for r in rows:
        mark = "" if r["exists"] else " [missing]"
        print(f"{r['updated_at']}  {r['slides']:>3}枚  {r['title']}{mark}")
        print(f"    {r['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

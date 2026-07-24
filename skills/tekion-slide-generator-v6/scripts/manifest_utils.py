#!/usr/bin/env python3
"""TEKION Slide Generator v6 - セッション manifest ユーティリティ。

manifest.json はセッションの「台帳」。スライド1枚ごとに状態・プロンプトハッシュ・
試行履歴・確定画像（current_image）・バージョン一覧を記録する。

これにより:
  - resume: 生成済み(validated)かつプロンプト未変更のスライドをスキップできる
  - 検証スイープ: 欠損・破損スライドだけを特定して再生成できる
  - export: 「確定版のみ」を確実に書き出せる（失敗版・古い版の混入を防ぐ）

状態遷移:
  pending → generating → generated → validated
                       ↘ failed（retryable / terminal は last_error_kind で区別）
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from typing import Optional

MANIFEST_VERSION = 2

# 検証しきい値: これ未満のPNGは「白紙・破損の疑い」として failed 扱いにする
MIN_IMAGE_BYTES = 30_000
ASPECT_TOLERANCE = 0.05  # 16:9 からの許容ずれ（正規化後は通常ぴったり）


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def prompt_hash(prompt_text: str, extra: str = "") -> str:
    """プロンプト本文（+アンカー等の付加条件）の SHA256。再生成要否の判定に使う。"""
    h = hashlib.sha256()
    h.update(prompt_text.encode("utf-8"))
    if extra:
        h.update(b"\x00")
        h.update(extra.encode("utf-8"))
    return h.hexdigest()


def load_manifest(path: str) -> dict:
    """manifest を読み込む。無ければ空の台帳を返す。"""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "slides" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass  # 壊れた manifest は作り直す（画像はディスク検証で拾える）
    return {"version": MANIFEST_VERSION, "created_at": now_iso(), "slides": {}}


def save_manifest(path: str, manifest: dict) -> None:
    """atomic write（一時ファイル→rename）。並列ワーカーからの保存でも壊れない。

    Google Drive / Dropbox 等のクラウド同期フォルダでは rename が同期の瞬間に
    一時的に失敗することがあるため、短いリトライで吸収する。
    """
    import time as _time
    manifest = {**manifest, "updated_at": now_iso()}
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    last_err = None
    for attempt in range(3):
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".manifest.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            try:
                from session_registry import upsert as _registry_upsert
                _registry_upsert(dir_name, manifest)
            except Exception:
                pass  # 台帳はベストエフォート
            return
        except OSError as e:
            last_err = e
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            _time.sleep(0.25 * (attempt + 1))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    raise last_err


def get_entry(manifest: dict, slide_base: str) -> dict:
    """スライドのエントリを取得（無ければ初期化して返す）。"""
    slides = manifest.setdefault("slides", {})
    if slide_base not in slides:
        slides[slide_base] = {
            "state": "pending",
            "prompt_sha256": None,
            "current_image": None,
            "raw_image": None,
            "versions": [],
            "attempts": 0,
            "last_error": None,
            "last_error_kind": None,
            "updated_at": now_iso(),
        }
    return slides[slide_base]


def update_entry(manifest: dict, slide_base: str, **fields) -> dict:
    entry = get_entry(manifest, slide_base)
    entry.update(fields)
    entry["updated_at"] = now_iso()
    return entry


def validate_image(path: str, min_bytes: int = MIN_IMAGE_BYTES) -> Optional[str]:
    """生成画像を機械検査する。問題なければ None、問題があれば理由文字列を返す。

    チェック: 存在 / 最小サイズ / PNGとしてデコード可能 / 16:9近傍 / ほぼ単色でない。
    「ほぼ単色」は白紙・真っ黒などの生成失敗を拾うための安全網。
    """
    if not path or not os.path.exists(path):
        return "file not found"
    size = os.path.getsize(path)
    if size < min_bytes:
        return f"file too small ({size} bytes < {min_bytes})"
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as img:
            img.load()
            w, h = img.size
            ratio = w / h
            target = 16 / 9
            if abs(ratio - target) / target > ASPECT_TOLERANCE:
                return f"aspect ratio {ratio:.3f} deviates from 16:9"
            # ほぼ単色チェック（縮小してから標準偏差を見る。コストは無視できる）
            small = img.convert("L").resize((64, 36))
            stddev = ImageStat.Stat(small).stddev[0]
            if stddev < 3.0:
                return f"image is nearly uniform (stddev={stddev:.2f}) — likely blank"
    except Exception as e:
        return f"decode error: {type(e).__name__}: {e}"
    return None


def classify_error(error_msg: str) -> str:
    """エラー文字列を分類する。

    Returns:
        "auth_terminal"  — 再ログインが必要。リトライしても無駄（全体を止めるべき）
        "rate_limited"   — レート制限。並列を下げてバックオフすべき
        "retryable"      — 一時的エラー。通常リトライで回復し得る
    """
    msg = (error_msg or "").lower()
    if any(k in msg for k in ("token_revoked", "refresh_token_reused", "401", "unauthorized",
                              "re-login", "codex login")):
        return "auth_terminal"
    if any(k in msg for k in ("429", "rate limit", "rate_limit", "throttle", "too many requests",
                              "usage limit", "quota")):
        return "rate_limited"
    return "retryable"


def next_version_path(images_dir: str, slide_base: str, versions: list) -> tuple[str, int]:
    """次のバージョンの出力パスを決める。v1 は `<base>.png`、以降 `<base>_vN.png`。"""
    n = len(versions) + 1
    if n == 1:
        return os.path.join(images_dir, f"{slide_base}.png"), 1
    return os.path.join(images_dir, f"{slide_base}_v{n}.png"), n


def ordered_bases(manifest: dict, include_removed: bool = False) -> list:
    """表示・エクスポートで使うスライドの並び順を返す。

    ダッシュボードでの並べ替え結果（manifest の slide_order）を最優先し、
    リストに無いスライドは従来の自然順（course_title 先頭）で末尾に続ける。
    ダッシュボードで削除されたスライド（state=removed）は既定で除外する。
    """
    import re
    slides = manifest.get("slides", {})

    def natural_key(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"([0-9]+)", s)]

    order = [b for b in manifest.get("slide_order", []) if b in slides]
    seen = set(order)
    rest = sorted((b for b in slides if b not in seen),
                  key=lambda b: (0 if "course_title" in b else 1, natural_key(b)))
    bases = order + rest
    if not include_removed:
        bases = [b for b in bases if slides[b].get("state") != "removed"]
    return bases


def collect_current_images(manifest_path: str, allow_partial: bool = False):
    """manifest から export 対象の確定版（current_image）を順序付きで収集する。

    「ディレクトリの最新ファイル」ではなく「検証済みと記録された版」を返すため、
    失敗版・古い版・raw が混入しない。未完成スライドがあれば allow_partial で
    ない限り None を返す（部分デッキを「成功」として出力しないため）。
    並び順はダッシュボードの並べ替え（slide_order）を反映し、削除済みは含めない。
    """
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    slides = manifest.get("slides", {})
    bases = ordered_bases(manifest)
    incomplete = sorted(b for b in bases
                        if slides[b].get("state") != "validated"
                        or not slides[b].get("current_image"))
    if incomplete and not allow_partial:
        return None, incomplete

    files = []
    for base in bases:
        entry = slides[base]
        img = entry.get("current_image")
        if entry.get("state") == "validated" and img and os.path.exists(img):
            files.append(img)
    return files, incomplete


def set_session_status(session_dir: str, stage: str, detail: str = "", total=None) -> None:
    """セッションの現在ステージを書き出す（ダッシュボードの実況表示が読む）。

    stage: planning / prompting / generating / done / attention
    """
    status = {"stage": stage, "detail": detail, "updated_at": now_iso()}
    if total is not None:
        status["total"] = total
    path = os.path.join(session_dir, "session_status.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
    except OSError:
        pass  # 実況はベストエフォート。本処理を止めない


def read_session_status(session_dir: str) -> dict:
    path = os.path.join(session_dir, "session_status.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def summarize(manifest: dict) -> dict:
    """状態別のスライド数を集計する。"""
    counts: dict[str, int] = {}
    for entry in manifest.get("slides", {}).values():
        state = entry.get("state", "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts

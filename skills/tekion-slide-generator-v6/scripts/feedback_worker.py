#!/usr/bin/env python3
"""TEKION Slides Hub - フィードバック自動処理ワーカー。

ハブが修正指示（/feedback）を受けるたびに起動する。チャット（エージェント）が
いなくても赤入れループが回る:

  送信 → ハブが保存 → このワーカーが未処理キューを古い順に処理
      → edit_slide.py（作り直し/指示編集/添付参照/全体指示の展開）
      → 処理済みカーソルを進める → タブに自動反映

指示のテキストは edit_slide にそのまま渡るため、標準的な赤入れに LLM の
オーケストレーションは不要（画像生成自体は edit_slide 内の Codex サブスク枠）。
構成変更などワーカーで扱えない依頼は、チャットで「続きを」と頼めばエージェントが
`--pending` から引き継げる（このワーカーは ack 済みにしない失敗分をログに残す）。

多重起動は .worker.lock（pid）で防ぐ。ハブは送信のたびに起動を試みてよい。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from manifest_utils import load_manifest, ordered_bases, set_session_status  # noqa: E402
from review_deck import FEEDBACK_CURSOR, pending_feedback  # noqa: E402

REBUILD_MARK = "【作り直し】前の画像を参照せず、ゼロから再生成する。"
EDIT_PARALLEL = 3  # スライド編集の同時実行数（生成はサブスク枠なので控えめに）


def _lock_path(session_dir: str) -> str:
    hist = os.path.join(session_dir, "feedback_history")
    os.makedirs(hist, exist_ok=True)
    return os.path.join(hist, ".worker.lock")


def acquire_lock(session_dir: str) -> bool:
    lock = _lock_path(session_dir)
    if os.path.exists(lock):
        try:
            with open(lock, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # 生存確認
            return False  # 稼働中のワーカーがいる
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass  # 死んだロック → 奪取
    with open(lock, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock(session_dir: str) -> None:
    try:
        os.unlink(_lock_path(session_dir))
    except OSError:
        pass


def _ack_upto(session_dir: str, history_file: str) -> None:
    """処理し終えた履歴ファイルまでカーソルを進める（処理中に届いた新着は残す）。"""
    hist_dir = os.path.join(session_dir, "feedback_history")
    with open(os.path.join(hist_dir, FEEDBACK_CURSOR), "w", encoding="utf-8") as f:
        f.write(os.path.basename(history_file))


def _strip_marker(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip() != REBUILD_MARK.strip()]
    return "\n".join(lines).strip()


def _resolve_brand_env() -> tuple[str, dict]:
    """resolve_brand の結果を (logo, 環境変数) に展開する。"""
    from resolve_brand import resolve
    result = resolve()
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)  # サブスク枠を守る（残っていると従量課金）
    env["SLIDE_LOGO_POSITION"] = result["logo_position"]
    env["SLIDE_LOGO_SCALE"] = str(result["logo_scale"])
    if result.get("footer_text") is not None:
        env["SLIDE_FOOTER_TEXT"] = result["footer_text"]
    return result["logo"], env


def build_jobs(session_dir: str, payload: dict) -> list[dict]:
    """1回の送信ペイロードを edit_slide 呼び出しのリストに展開する。"""
    fb = payload.get("feedback") or {}
    rebuild = set(payload.get("rebuild") or [])
    attachments = payload.get("attachments") or {}
    global_note = (payload.get("global") or "").strip()
    global_keepref = bool(payload.get("global_keep_reference"))

    manifest = load_manifest(os.path.join(session_dir, "manifest.json"))
    known = set(manifest.get("slides", {}))

    jobs = []
    covered = set()
    for base, text in fb.items():
        if base not in known:
            print(f"⚠️  不明なスライドをスキップ: {base}")
            continue
        instruction = _strip_marker(text)
        if global_note:
            instruction = (instruction + "\n" + global_note).strip()
        att = attachments.get(base) or []
        jobs.append({"base": base, "instruction": instruction,
                     "rebuild": base in rebuild,
                     "reference": att[0] if att else None,
                     "extra_refs": att[1:]})
        covered.add(base)

    if global_note:
        for base in ordered_bases(manifest):
            if base in covered:
                continue
            jobs.append({"base": base, "instruction": global_note,
                         "rebuild": not global_keepref,
                         "reference": None, "extra_refs": []})
    return jobs


def run_job(session_dir: str, job: dict, logo: str, env: dict, provider: str) -> tuple[str, bool, str]:
    cmd = [sys.executable, os.path.join(SCRIPTS_DIR, "edit_slide.py"),
           "--session-dir", session_dir, "--slide", job["base"],
           "--provider", provider]
    if job["rebuild"]:
        cmd.append("--rebuild")
    if job["instruction"]:
        cmd += ["--instruction", job["instruction"]]
    elif not job["rebuild"]:
        return job["base"], False, "指示が空（微修正モードでは指示が必要）"
    if job["reference"] and os.path.exists(job["reference"]):
        cmd += ["--reference-image", job["reference"]]
        for extra in job["extra_refs"]:
            job["instruction"] += ""  # 追加分は1枚目のみ参照（複数添付は指示文で言及済み）
    if logo:
        cmd += ["--logo", logo]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
        ok = r.returncode == 0
        detail = (r.stdout + r.stderr)[-400:] if not ok else ""
        return job["base"], ok, detail
    except subprocess.TimeoutExpired:
        return job["base"], False, "timeout (900s)"


def process(session_dir: str, provider: str) -> int:
    session_dir = os.path.realpath(os.path.abspath(session_dir))
    if not acquire_lock(session_dir):
        print("ℹ️  既にワーカーが稼働中。終了します")
        return 0
    failures = []
    try:
        logo, env = _resolve_brand_env()
        while True:
            pend = pending_feedback(session_dir)
            if not pend:
                break
            for hist_file in pend:
                try:
                    with open(hist_file, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    print(f"⚠️  読めない履歴をスキップ: {hist_file} ({e})")
                    _ack_upto(session_dir, hist_file)
                    continue
                jobs = build_jobs(session_dir, payload)
                if jobs:
                    set_session_status(session_dir, "editing",
                                       f"修正指示を処理中（{len(jobs)}枚）")
                    print(f"🛠  {os.path.basename(hist_file)}: {len(jobs)}枚を処理")
                    with ThreadPoolExecutor(max_workers=EDIT_PARALLEL) as pool:
                        for base, ok, detail in pool.map(
                                lambda j: run_job(session_dir, j, logo, env, provider), jobs):
                            print(("✓ " if ok else "✗ ") + base + ("" if ok else f" — {detail}"))
                            if not ok:
                                failures.append(base)
                else:
                    print(f"（{os.path.basename(hist_file)}: 処理対象なし = 校了送信）")
                _ack_upto(session_dir, hist_file)  # 処理済み分だけ進める（新着は次周で）
        if failures:
            set_session_status(session_dir, "attention",
                               f"一部の修正が失敗: {', '.join(sorted(set(failures)))}。"
                               "チャットで「続きを」と頼むと引き継げます")
            return 1
        set_session_status(session_dir, "done", "修正指示の反映が完了")
        return 0
    finally:
        release_lock(session_dir)


def main() -> int:
    ap = argparse.ArgumentParser(description="Hub feedback auto-worker (v6)")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--provider", default=os.environ.get("TEKION_WORKER_PROVIDER", "codex"),
                    choices=["codex", "openai", "mock"])
    args = ap.parse_args()
    return process(args.session_dir, args.provider)


if __name__ == "__main__":
    raise SystemExit(main())

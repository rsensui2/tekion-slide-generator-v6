#!/usr/bin/env python3
"""TEKION Slides Hub - フィードバック自動処理ワーカー。

ハブが修正指示（/feedback）を受けるたびに起動する。チャット（エージェント）が
いなくても赤入れループが回る:

  送信 → ハブが保存 → このワーカーが未処理キューを古い順に処理
      → edit_slide.py（作り直し/指示編集/添付参照/全体指示の展開）
      → 処理済みカーソルを進める → タブに自動反映

指示のテキストは edit_slide にそのまま渡るため、標準的な赤入れに LLM の
オーケストレーションは不要（画像生成自体は edit_slide 内の Codex サブスク枠）。

失敗を含む送信は ack せず feedback_history/failed/ へ移す（dead-letter）。
チャットで「続きを」と頼めば、エージェントが `--pending` で失敗分ごと引き継げる。
多重起動は .worker.lock の flock で直列化する（後発は先行の完了を待ってから
キューを再確認するため、取りこぼし窓がない）。ハブは送信のたびに起動してよい。
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

from manifest_utils import (get_profile, load_manifest, ordered_bases,  # noqa: E402
                            set_session_status)
from review_deck import FEEDBACK_CURSOR, pending_feedback  # noqa: E402

REBUILD_MARK = "【作り直し】前の画像を参照せず、ゼロから再生成する。"
EDIT_PARALLEL = 3  # スライド編集の同時実行数（生成はサブスク枠なので控えめに）


def _lock_path(session_dir: str) -> str:
    hist = os.path.join(session_dir, "feedback_history")
    os.makedirs(hist, exist_ok=True)
    return os.path.join(hist, ".worker.lock")


def acquire_lock(session_dir: str):
    """flock をブロッキング取得し、ファイルハンドルを返す（プロセス生存中保持）。

    先行ワーカーがいる場合は終了を待ってから進む。こうすると
    「先行がキュー空を確認して終了する瞬間に新着が届き、後発はロックを見て
    即終了 → 誰も処理しない」という取りこぼし窓が消える（後発はロック獲得後に
    必ずキューを再確認するため）。ハンドルを閉じればロックは自動解放される。
    """
    import fcntl
    lf = open(_lock_path(session_dir), "w")
    fcntl.flock(lf, fcntl.LOCK_EX)
    lf.write(str(os.getpid()))
    lf.flush()
    return lf


def _ack_upto(session_dir: str, history_file: str) -> None:
    """処理し終えた履歴ファイルまでカーソルを進める（処理中に届いた新着は残す）。"""
    hist_dir = os.path.join(session_dir, "feedback_history")
    with open(os.path.join(hist_dir, FEEDBACK_CURSOR), "w", encoding="utf-8") as f:
        f.write(os.path.basename(history_file))


def _strip_marker(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip() != REBUILD_MARK.strip()]
    return "\n".join(lines).strip()


def _resolve_brand_env(provider: str, profile: dict) -> tuple[str, dict]:
    """resolve_brand の結果を (logo, 環境変数) に展開する。

    ブランドのロゴ・フッターはスライド固有の仕上げなので、それ以外の
    プロファイルでは解決自体を行わない。
    """
    env = dict(os.environ)
    if provider == "codex":
        env.pop("OPENAI_API_KEY", None)  # サブスク枠を守る（残っていると従量課金）
    if profile["kind"] != "slides":
        return "", env
    from resolve_brand import resolve
    result = resolve()
    env["SLIDE_LOGO_POSITION"] = result["logo_position"]
    env["SLIDE_LOGO_SCALE"] = str(result["logo_scale"])
    if result.get("footer_text") is not None:
        env["SLIDE_FOOTER_TEXT"] = result["footer_text"]
    return result["logo"], env


def resolve_editor(profile: dict) -> str:
    """プロファイルの編集スクリプトの絶対パスを返す。

    editor_dir が無ければ v6 自身の scripts/ を見る（従来のスライド編集）。
    """
    editor = os.path.basename(profile.get("editor") or "edit_slide.py")
    base_dir = profile.get("editor_dir") or SCRIPTS_DIR
    path = os.path.join(os.path.expanduser(base_dir), editor)
    if not os.path.exists(path):
        raise FileNotFoundError(f"編集スクリプトが見つかりません: {path}")
    return path


def build_jobs(session_dir: str, payload: dict) -> list[dict]:
    """1回の送信ペイロードを edit_slide 呼び出しのリストに展開する。"""
    fb = payload.get("feedback") or {}
    rebuild = set(payload.get("rebuild") or [])
    attachments = payload.get("attachments") or {}
    global_note = (payload.get("global") or "").strip()
    global_keepref = bool(payload.get("global_keep_reference"))

    manifest = load_manifest(os.path.join(session_dir, "manifest.json"))
    known = set(manifest.get("slides", {}))

    def _has_prompt(base: str) -> bool:
        entry = manifest["slides"].get(base) or {}
        pf = entry.get("prompt_file")
        if pf and os.path.exists(pf):
            return True
        return os.path.exists(os.path.join(session_dir, "prompts", f"{base}.txt"))

    def _mode_rebuild(base: str, want_rebuild: bool) -> bool:
        # 取り込みスライドには生成プロンプトが無く、「作り直し」は指示文だけからの
        # 生成になって元デザインが失われる → 参照つき微修正へ自動フォールバック
        if want_rebuild and not _has_prompt(base):
            print(f"ℹ️  {base}: プロンプトが無い（取り込みスライド）ため微修正モードで処理")
            return False
        return want_rebuild

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
        if len(att) > 1:
            names = ", ".join(os.path.basename(a) for a in att[1:])
            instruction += (f"\n（参照画像のほか、ユーザーは {names} も添付している。"
                            "指示文でそれらに言及があれば考慮すること）")
        want_rebuild = (base in rebuild) or (bool(global_note) and not global_keepref)
        jobs.append({"base": base, "instruction": instruction,
                     "rebuild": _mode_rebuild(base, want_rebuild),
                     "reference": att[0] if att else None})
        covered.add(base)

    if global_note:
        for base in ordered_bases(manifest):
            if base in covered:
                continue
            jobs.append({"base": base, "instruction": global_note,
                         "rebuild": _mode_rebuild(base, not global_keepref),
                         "reference": None})
    return jobs


def run_job(session_dir: str, job: dict, logo: str, env: dict, provider: str,
            editor: str) -> tuple[str, bool, str]:
    cmd = [sys.executable, editor,
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
    if logo:
        cmd += ["--logo", logo]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
        ok = r.returncode == 0
        detail = (r.stdout + r.stderr)[-400:] if not ok else ""
        return job["base"], ok, detail
    except subprocess.TimeoutExpired:
        return job["base"], False, "timeout (900s)"


def _dead_letter(session_dir: str, hist_file: str) -> None:
    """処理に失敗した送信を failed/ へ移す（キューからは外れ、引き継ぎ用に残る）。

    ファイルを動かすだけでキューから消えるため、カーソルは進めない。
    エージェントは `review_deck.py --pending` で失敗分も確認できる。
    """
    import shutil
    failed_dir = os.path.join(session_dir, "feedback_history", "failed")
    os.makedirs(failed_dir, exist_ok=True)
    try:
        shutil.move(hist_file, os.path.join(failed_dir, os.path.basename(hist_file)))
    except OSError as e:
        print(f"⚠️  dead-letter への移動に失敗: {e}")


def process(session_dir: str, provider: str) -> int:
    session_dir = os.path.realpath(os.path.abspath(session_dir))
    lock_handle = acquire_lock(session_dir)  # 先行ワーカーの終了を待って必ず再確認する
    failures = []
    try:
        profile = get_profile(load_manifest(os.path.join(session_dir, "manifest.json")))
        logo, env = _resolve_brand_env(provider, profile)
        editor = resolve_editor(profile)
        while True:
            pend = pending_feedback(session_dir)
            if not pend:
                break
            for hist_file in pend:
                try:
                    with open(hist_file, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    print(f"⚠️  読めない履歴を dead-letter へ: {hist_file} ({e})")
                    _dead_letter(session_dir, hist_file)
                    continue
                jobs = build_jobs(session_dir, payload)
                if not jobs:
                    print(f"（{os.path.basename(hist_file)}: 処理対象なし = 校了送信）")
                    _ack_upto(session_dir, hist_file)
                    continue
                set_session_status(session_dir, "editing",
                                   f"修正指示を処理中（{len(jobs)}枚）")
                print(f"🛠  {os.path.basename(hist_file)}: {len(jobs)}枚を処理")
                payload_failed = []
                with ThreadPoolExecutor(max_workers=EDIT_PARALLEL) as pool:
                    for base, ok, detail in pool.map(
                            lambda j: run_job(session_dir, j, logo, env, provider, editor),
                            jobs):
                        print(("✓ " if ok else "✗ ") + base + ("" if ok else f" — {detail}"))
                        if not ok:
                            payload_failed.append(base)
                if payload_failed:
                    # 全ジョブ成功した送信だけ ack。失敗を含む送信は failed/ に残し、
                    # エージェント（「続きを」）が引き継げるようにする
                    failures.extend(payload_failed)
                    _dead_letter(session_dir, hist_file)
                else:
                    _ack_upto(session_dir, hist_file)
        if failures:
            set_session_status(session_dir, "attention",
                               f"一部の修正が失敗: {', '.join(sorted(set(failures)))}。"
                               "チャットで「続きを」と頼むと引き継げます")
            return 1
        set_session_status(session_dir, "done", "修正指示の反映が完了")
        return 0
    finally:
        lock_handle.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Hub feedback auto-worker (v6)")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--provider", default=os.environ.get("TEKION_WORKER_PROVIDER", "codex"),
                    choices=["codex", "openai", "mock"])
    args = ap.parse_args()
    return process(args.session_dir, args.provider)


if __name__ == "__main__":
    raise SystemExit(main())

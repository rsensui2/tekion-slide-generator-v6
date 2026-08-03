#!/usr/bin/env python3
"""Codex 駆動ブリッジ — gpt-image-2 を Codex（ChatGPT/Codex サブスク枠）で叩く。

このモジュールの役割は「プロンプト1枚 → 画像バイト列」を Codex 経由で得ること。
画像生成のバックエンドは Codex CLI 内蔵の gpt-image-2。課金モードを選べる:
- subscription（既定）: ChatGPT/Codex サブスク枠。`OPENAI_API_KEY` を**除去**して起動
  （環境にキーが残っていて気づかず従量課金される事故を防ぐ安全策）。
- api: OpenAI API 従量課金。`OPENAI_API_KEY` を使って起動する。
モードは generate_image(billing=...) / 環境変数 CODEX_SLIDES_BILLING で指定し、
実行時にどちらかをログへ明示する（黙って剥がさない）。

2 つのバックエンドを持つ:

- ``exec``        : ``codex exec --full-auto`` を 1 回叩く一発実行。依存なし・確実。
                    バッチ画像生成では app-server より管理が容易で安定（既定）。
- ``app-server``  : ``codex app-server`` を stdio JSON-RPC で駆動する常駐サーバ方式。
                    対話履歴を引き継ぎたい/外部アプリから叩きたい用途向け。experimental。

いずれも「生成した最終画像を指定パスに保存させる」ことを Codex に明示指示し、
保存されなければ ``~/.codex/generated_images`` から新規生成物を回収するフォールバックを持つ。

公開 API:
    generate_image(prompt, output_path, ...) -> CodexResult
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# --- 定数 -------------------------------------------------------------------

CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
REAL_CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
GENERATED_IMAGES_DIR = REAL_CODEX_HOME / "generated_images"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# warmup が更新した「新鮮な auth.json」のパスを並列ワーカー（別プロセス）へ渡す環境変数。
# macOS の ProcessPool=spawn / subprocess ワーカーはモジュールグローバルを引き継がないため、
# モジュール変数ではなく環境変数で伝播する（これが無いとワーカーが実 auth.json をコピー元にしてしまう）。
WARM_AUTH_ENV = "CODEX_SLIDES_WARM_AUTH"


def _auth_source() -> Path:
    """隔離 CODEX_HOME に入れる auth.json のコピー元を返す。

    warmup 済みなら「warmup が更新した新鮮コピー」、未済なら実ホームの auth.json。
    どちらの場合も**実 auth.json は読むだけ**で、v5 が実 auth.json を書き換えることはない。
    """
    warm = os.environ.get(WARM_AUTH_ENV)
    if warm and os.path.exists(warm):
        return Path(warm)
    return REAL_CODEX_HOME / "auth.json"


def _cleanup_warm_auth() -> None:
    """終了時に warmup が残した auth コピー（認証情報）を削除する。"""
    warm = os.environ.get(WARM_AUTH_ENV)
    if warm and os.path.exists(warm):
        try:
            os.remove(warm)
        except OSError:
            pass


atexit.register(_cleanup_warm_auth)

# サブスク枠の画像生成ターンは枠消費が速い（公式: 通常の3-5倍）。
# 並列はスキル側で抑えるが、1枚あたりの上限時間は長めに確保する。
DEFAULT_EXEC_TIMEOUT = 420  # 秒


@dataclass
class CodexResult:
    """Codex 画像生成の結果。"""
    ok: bool
    image_bytes: Optional[bytes] = None
    error: Optional[str] = None
    attempts: int = 1
    backend: str = "exec"
    saved_path: Optional[str] = None
    metadata: dict = field(default_factory=dict)


# --- 共通ヘルパ -------------------------------------------------------------

def _codex_env(codex_home: Optional[str] = None, use_api_key: bool = False) -> dict:
    """Codex 実行用の環境変数を作る（課金モードを制御）。

    Codex は公式仕様で「OPENAI_API_KEY があれば API 従量課金、無ければサブスク枠」。
    - use_api_key=False（既定/サブスク枠）: OPENAI_API_KEY を**除去**する。これは、
      環境にキーが残っていて気づかず従量課金される事故を防ぐ安全策。
    - use_api_key=True（API 課金モード）: キーをそのまま残す（Codex が API 課金で生成）。
    codex_home を渡すと CODEX_HOME を上書きし、生成物を隔離する。
    """
    if use_api_key:
        env = dict(os.environ)
    else:
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    if codex_home:
        env["CODEX_HOME"] = codex_home
    return env


def _real_model() -> str:
    """実 ~/.codex/config.toml のトップレベル model を読む（無ければ gpt-5.5）。"""
    cfg = REAL_CODEX_HOME / "config.toml"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*model\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "gpt-5.5"


def _make_isolated_home() -> str:
    """並列実行の衝突を避けるための独立 CODEX_HOME を作る。

    認証（auth.json）は実ホームから**コピー**する（symlink にしない）。これが重要:
    symlink にすると並列プロセスが同じ実 auth.json に対して OAuth トークン更新を
    同時に書き込み、使い捨て refresh token を奪い合って**トークンを失効**させる
    （refresh_token_reused → token_revoked）。コピーなら各プロセスの更新は自分の
    複製内に閉じ、実ホームの auth.json は決して壊れない。
    なお並列前に warmup_auth() でアクセストークンを更新しておけば、バッチ中に
    トークン更新自体が走らず競合も起きない（推奨運用）。

    生成画像は <home>/generated_images に隔離され、他プロセスとの取り違えも起きない。
    MCP サーバ等の重い設定は持ち込まない最小 config.toml を置く。
    """
    home = tempfile.mkdtemp(prefix="codex_slides_home_")
    src_auth = _auth_source()   # 実ホーム固定をやめ、warmup済みなら新鮮コピーを使う
    if src_auth.exists():
        shutil.copy2(src_auth, os.path.join(home, "auth.json"))
    with open(os.path.join(home, "config.toml"), "w", encoding="utf-8") as f:
        f.write(f'model = "{_real_model()}"\n')
        f.write('model_reasoning_effort = "low"\n')
    return home


def warmup_auth(timeout: int = 90) -> bool:
    """並列ファンアウト前にアクセストークンを更新する（**実 auth.json は書き換えない**）。

    実ホームで直接 codex を走らせるとトークンがローテーションされ、常駐の Codex アプリ等が
    握る旧トークンが失効して再認証地獄になる。これを避けるため、warmup も**隔離ホームの中**で
    実行し、トークン更新を隔離コピー側に閉じ込める。更新後の新鮮 auth.json を退避し、
    環境変数 WARM_AUTH_ENV で以降の並列ワーカー（別プロセス）のコピー元として伝播する。
    → v5 が実 auth.json を進める主体にならず、Codex アプリとの競合（再認証）が消える。

    画像生成はせず軽量プロンプトで済ませる。成功可否を返す。
    """
    home = _make_isolated_home()   # この時点では _auth_source()=実ホーム（読むだけ）
    try:
        proc = subprocess.run(
            [CODEX_BIN, "exec", "--skip-git-repo-check", "-c", "model_reasoning_effort=low", "ok"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            env=_codex_env(home),   # CODEX_HOME を隔離ホームに上書き＝実 auth.json を触らない
        )
        blob = (proc.stderr or "") + (proc.stdout or "")
        if any(s in blob for s in ("token_revoked", "refresh_token_reused", "sign in again")):
            print("❌ warmup: Codex 認証が無効です。`codex login` で再ログインしてください。", file=sys.stderr)
            return False
        if proc.returncode != 0:
            return False
        # 更新後の新鮮 auth.json を退避し、以降のワーカーのコピー元にする（環境変数で子プロセスへ伝播）
        warm_home_auth = os.path.join(home, "auth.json")
        if os.path.exists(warm_home_auth):
            fd, warm_path = tempfile.mkstemp(prefix="codex_slides_warmauth_", suffix=".json")
            os.close(fd)
            shutil.copy2(warm_home_auth, warm_path)
            old = os.environ.get(WARM_AUTH_ENV)
            if old and os.path.exists(old):
                try:
                    os.remove(old)
                except OSError:
                    pass
            os.environ[WARM_AUTH_ENV] = warm_path
        return True
    except Exception as e:
        print(f"⚠️  warmup 失敗: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _collect_image_in(generated_dir: Path) -> Optional[Path]:
    """指定 generated_images ディレクトリ内の最新画像を返す（隔離前提で1枚のはず）。"""
    if not generated_dir.exists():
        return None
    pngs = [p for p in generated_dir.rglob("*")
            if p.suffix.lower() in IMAGE_EXTS and p.is_file()]
    if not pngs:
        return None
    return max(pngs, key=lambda p: p.stat().st_mtime)


def _is_portrait(aspect: str) -> bool:
    """アスペクト表記が縦長かどうか。

    2:3 のような縦長を指定しても指示文が「横長スライド」と言ってしまい、
    モデルに矛盾した要件を渡していた（縦長デッキで実際に効いていた）。
    """
    try:
        w_text, _, h_text = str(aspect).partition(":")
        return float(w_text) < float(h_text)
    except (TypeError, ValueError):
        return False


def _build_instruction(prompt: str, output_path: str, image_size: str, aspect: str,
                       reference_count: int = 0) -> str:
    """Codex に渡す画像生成指示文を組み立てる。

    「生成」と「指定パスへの保存」だけを行わせ、余計な作業をさせない。
    reference_count > 0 のときは、添付画像を見た目の正として使う指示を加える。
    """
    ref_lines = ""
    if reference_count:
        ref_lines = (
            "- このプロンプトには参照画像が添付されている。画像生成ツールに参照入力として渡し、"
            "描く人物・キャラクター・ロゴ等の見た目（画風・髪型・顔立ち・服装・アクセサリー・配色）を"
            "添付画像に忠実に一致させること\n"
        )
    return (
        "画像を1枚だけ生成するタスクです。次の要件を厳密に守ってください。\n"
        f"- アスペクト比: {aspect}"
        f"（{'縦長ページ' if _is_portrait(aspect) else '横長スライド'}）\n"
        f"- 解像度: {image_size} 相当の高精細\n"
        "- 組み込みの画像生成ツール（$imagegen / gpt-image-2）を使うこと\n"
        + ref_lines +
        f"- 生成した最終画像を、必ず次の絶対パスへ保存（コピー）すること: {output_path}\n"
        "- 保存先ディレクトリが無ければ作成してよい\n"
        "- 画像生成と保存以外の作業（説明文の出力・余分なファイル作成・コード実行）はしないこと\n"
        "\n"
        "# 画像プロンプト\n"
        f"{prompt}\n"
    )


def _read_valid_image(path: str) -> Optional[bytes]:
    """画像として開けるか検証しつつバイト列を読む。壊れていれば None。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


# --- exec バックエンド ------------------------------------------------------

def _generate_via_exec(
    prompt: str,
    output_path: str,
    image_size: str,
    aspect: str,
    timeout: int,
    billing: str = "subscription",
    reference_images: Optional[list] = None,
) -> CodexResult:
    """``codex exec --full-auto`` を1回実行して画像を得る。

    reference_images を渡すと ``codex exec -i`` で添付し、gpt-image-2 の参照入力として
    使わせる（キャラクター・ロゴ等の見た目を固定する用途）。
    """
    refs = [p for p in (reference_images or []) if p]
    instruction = _build_instruction(prompt, output_path, image_size, aspect,
                                     reference_count=len(refs))
    out_dir = os.path.dirname(os.path.abspath(output_path)) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    # 各実行を独立 CODEX_HOME に隔離（並列時の画像取り違えを防止）
    codex_home = _make_isolated_home()

    cmd = [
        CODEX_BIN, "exec",
        "--skip-git-repo-check",
        "--full-auto",
        # 画像生成ターンはエージェントの深い推論を必要としない。reasoning を軽くして
        # 1枚あたりのターン時間を短縮する（画像そのものの品質は gpt-image-2 が担う）。
        "-c", "model_reasoning_effort=low",
        "-C", out_dir,
    ]
    stdin_input = None
    if refs:
        for ref in refs:
            cmd.extend(["-i", ref])
        # `-i <FILE>...` は複数ファイルを取るため、直後の位置引数プロンプトを
        # 画像パスとして飲み込んでしまう。参照画像がある場合はプロンプトを
        # stdin("-") で渡す必要がある。
        cmd.append("-")
        stdin_input = instruction
    else:
        cmd.append(instruction)
    slide_name = os.path.basename(output_path)
    use_api = (billing == "api")
    ref_label = f" +ref×{len(refs)}" if refs else ""
    print(f"🎨 Codex(exec)生成開始: {slide_name} ({aspect}/{image_size}{ref_label}) [{_billing_label(billing)}]",
          file=sys.stderr)

    # 再生成時に「前回の古い画像」を今回の成功と誤認しないため、実行前の状態を記録する
    pre_mtime = os.path.getmtime(output_path) if os.path.exists(output_path) else None

    try:
        proc = subprocess.run(
            cmd,
            input=stdin_input,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_codex_env(codex_home, use_api_key=use_api),
        )

        # 1) 指定パスに「今回の実行で」保存されていればそれを採用
        img = None
        if os.path.exists(output_path):
            if pre_mtime is None or os.path.getmtime(output_path) > pre_mtime:
                img = _read_valid_image(output_path)
        saved = output_path if img else None

        # 2) フォールバック: 隔離 home の generated_images から回収（衝突なし）
        if img is None:
            newest = _collect_image_in(Path(codex_home) / "generated_images")
            if newest is not None:
                img = _read_valid_image(str(newest))
                saved = str(newest)

        if img is None:
            detail = (proc.stderr or proc.stdout or "").strip()
            return CodexResult(
                ok=False,
                error=f"画像が見つかりません (rc={proc.returncode}). {detail[:400]}",
                backend="exec",
            )

        return CodexResult(ok=True, image_bytes=img, backend="exec", saved_path=saved)
    except subprocess.TimeoutExpired:
        return CodexResult(ok=False, error=f"Timeout ({timeout}s)", backend="exec")
    finally:
        shutil.rmtree(codex_home, ignore_errors=True)


# --- app-server バックエンド ------------------------------------------------

def _generate_via_app_server(
    prompt: str,
    output_path: str,
    image_size: str,
    aspect: str,
    timeout: int,
    billing: str = "subscription",
) -> CodexResult:
    """``codex app-server`` を stdio JSON-RPC で駆動して画像を得る（experimental）。

    initialize → initialized → thread/start → turn/start を送り、turn/completed
    まで通知を読む。生成画像は指定パス保存 or generated_images から回収する。
    """
    instruction = _build_instruction(prompt, output_path, image_size, aspect)
    out_dir = os.path.dirname(os.path.abspath(output_path)) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    codex_home = _make_isolated_home()
    slide_name = os.path.basename(output_path)
    print(f"🎨 Codex(app-server)生成開始: {slide_name} ({aspect}/{image_size}) [{_billing_label(billing)}]",
          file=sys.stderr)

    proc = subprocess.Popen(
        [CODEX_BIN, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_codex_env(codex_home, use_api_key=(billing == "api")),
    )

    _req_id = {"n": 0}

    def _next_id() -> int:
        _req_id["n"] += 1
        return _req_id["n"]

    def _send(method: str, params: dict, *, notify: bool = False) -> Optional[int]:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        rid = None
        if not notify:
            rid = _next_id()
            msg["id"] = rid
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        return rid

    def _read_until(predicate, deadline: float) -> Optional[dict]:
        """predicate(msg) が True を返すメッセージまで stdout を読む。"""
        assert proc.stdout is not None
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if predicate(msg):
                return msg
        return None

    deadline = time.time() + timeout
    try:
        init_id = _send("initialize", {"clientInfo": {"name": "tekion-slide-generator", "version": "1.0.0"}})
        _read_until(lambda m: m.get("id") == init_id, deadline)
        _send("initialized", {}, notify=True)

        start_id = _send("thread/start", {"cwd": out_dir})
        started = _read_until(lambda m: m.get("id") == start_id, deadline)
        thread_id = None
        if started and isinstance(started.get("result"), dict):
            thread_id = started["result"].get("threadId") or started["result"].get("thread_id")
        if not thread_id:
            raise RuntimeError("thread/start が threadId を返しませんでした")

        _send("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": instruction}],
        })
        # turn/completed 通知まで待つ
        _read_until(
            lambda m: m.get("method") in ("turn/completed", "turn/failed")
            or (isinstance(m.get("error"), dict)),
            deadline,
        )
    except Exception as e:
        try:
            proc.terminate()
        except Exception:
            pass
        shutil.rmtree(codex_home, ignore_errors=True)
        return CodexResult(ok=False, error=f"app-server: {type(e).__name__}: {e}", backend="app-server")
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    try:
        img = _read_valid_image(output_path) if os.path.exists(output_path) else None
        saved = output_path if img else None
        if img is None:
            newest = _collect_image_in(Path(codex_home) / "generated_images")
            if newest is not None:
                img = _read_valid_image(str(newest))
                saved = str(newest)
    finally:
        shutil.rmtree(codex_home, ignore_errors=True)
    if img is None:
        return CodexResult(ok=False, error="app-server: 画像が見つかりません", backend="app-server")
    return CodexResult(ok=True, image_bytes=img, backend="app-server", saved_path=saved)


# --- 公開 API ---------------------------------------------------------------

def resolve_backend(backend: str = "auto") -> str:
    """使用バックエンドを解決する。

    優先順位: 明示引数 > 環境変数 CODEX_SLIDES_BACKEND > 既定(exec)。
    """
    if backend and backend != "auto":
        return backend
    env_backend = os.environ.get("CODEX_SLIDES_BACKEND", "").strip().lower()
    if env_backend in ("exec", "app-server"):
        return env_backend
    return "exec"


def resolve_billing(billing: str = "auto") -> str:
    """課金モードを解決する。

    優先順位: 明示引数 > 環境変数 CODEX_SLIDES_BILLING > 既定(subscription)。
    - "subscription": ChatGPT/Codex サブスク枠（OPENAI_API_KEY を除去）。既定。
    - "api":          OpenAI API 従量課金（OPENAI_API_KEY を使う）。
    """
    if billing and billing != "auto":
        return billing
    env_billing = os.environ.get("CODEX_SLIDES_BILLING", "").strip().lower()
    if env_billing in ("subscription", "api"):
        return env_billing
    return "subscription"


def _billing_label(billing: str) -> str:
    if billing == "api":
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        return "API従量課金" if has_key else "API指定だがキー無し→サブスク枠"
    return "サブスク枠"


def generate_image(
    prompt: str,
    output_path: str,
    *,
    image_size: str = "2K",
    aspect: str = "16:9",
    backend: str = "auto",
    billing: str = "auto",
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: int = DEFAULT_EXEC_TIMEOUT,
    reference_images: Optional[list] = None,
) -> CodexResult:
    """Codex で画像を1枚生成し、バイト列を返す。

    Args:
        prompt: 画像生成プロンプト本文
        output_path: Codex に保存させたい最終パス（フッター等の後処理は呼び出し側で）
        image_size: 共通解像度ラベル（512px/1K/2K/4K）
        aspect: アスペクト比指示（既定 16:9）
        backend: "auto" | "exec" | "app-server"
        billing: "auto" | "subscription"（既定・サブスク枠）| "api"（OpenAI API 従量課金）
        max_retries: 失敗時の再試行回数
        retry_delay: 初回リトライ待機秒（指数バックオフ）
        timeout: 1回あたりの上限秒
        reference_images: 参照画像パスのリスト。キャラクター・ロゴ等の見た目の正として
            gpt-image-2 に渡す。app-server バックエンドは未対応のため、指定時は exec に迂回する

    Returns:
        CodexResult（ok / image_bytes / error / attempts / backend）
    """
    chosen = resolve_backend(backend)
    bill = resolve_billing(billing)

    refs = [p for p in (reference_images or []) if p]
    missing = [p for p in refs if not os.path.exists(p)]
    if missing:
        for p in missing:
            print(f"⚠️  参照画像が見つかりません（スキップ）: {p}", file=sys.stderr)
        refs = [p for p in refs if os.path.exists(p)]

    if refs and chosen == "app-server":
        # app-server の turn/start に画像 item を渡す口はまだ実装していない。
        # 参照画像が要るときは exec（codex exec -i）に迂回する。
        print("ℹ️  参照画像は app-server 未対応のため exec バックエンドで実行します", file=sys.stderr)
        chosen = "exec"

    runner = _generate_via_app_server if chosen == "app-server" else _generate_via_exec
    runner_kwargs = {"reference_images": refs} if chosen == "exec" and refs else {}

    last_error = None
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            wait = retry_delay * (2 ** (attempt - 2))
            print(f"⏳ リトライ {attempt}/{max_retries} (待機 {wait:.1f}s): {os.path.basename(output_path)}",
                  file=sys.stderr)
            time.sleep(wait)
        result = runner(prompt, output_path, image_size, aspect, timeout, bill, **runner_kwargs)
        result.attempts = attempt
        if result.ok:
            return result
        last_error = result.error
        print(f"⚠️  Codex生成失敗 ({chosen}/{bill}) try{attempt}: {last_error}", file=sys.stderr)

    return CodexResult(ok=False, error=last_error or "unknown", attempts=max_retries, backend=chosen)


# --- CLI（疎通テスト用） ----------------------------------------------------

def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Codex 画像生成ブリッジ 単体テスト")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--image-size", default="2K")
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--backend", default="auto", choices=["auto", "exec", "app-server"])
    ap.add_argument("--billing", default="auto", choices=["auto", "subscription", "api"],
                    help="subscription=サブスク枠(既定) / api=OpenAI API従量課金")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--reference-image", action="append", dest="reference_images",
                    metavar="FILE", default=None,
                    help="参照画像（複数指定可）。キャラクター・ロゴ等の見た目の正として gpt-image-2 に渡す")
    args = ap.parse_args()

    res = generate_image(
        args.prompt, args.output,
        image_size=args.image_size, aspect=args.aspect,
        backend=args.backend, billing=args.billing, max_retries=args.max_retries,
        reference_images=args.reference_images,
    )
    if res.ok and res.image_bytes:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        with open(args.output, "wb") as f:
            f.write(res.image_bytes)
        print(f"✅ OK backend={res.backend} saved={args.output} ({len(res.image_bytes):,} bytes) "
              f"(codex_saved={res.saved_path})", file=sys.stderr)
        return 0
    print(f"❌ FAILED backend={res.backend}: {res.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main())

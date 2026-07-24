#!/usr/bin/env python3
"""
TEKION Slide Generator v6 - 並列スライド画像生成オーケストレータ
Phase 7: プロンプトファイル群からスライド画像を並列生成する

v6 の設計思想 — 「全枚数、確実に、同じ顔で。」
  1. ゼロ欠損保証: manifest（台帳）+ 生成後の機械検証 + 検証スイープで、
     「N枚頼んだらN枚返る」を仕組みで保証する。
  2. 初手フルスロットル: 既定 --max-parallel auto は「枚数ぶん全部」を一斉に
     ファンアウトする（上限 --parallel-cap）。実測でレート制限に当たっていない以上、
     様子見の意味はない。当たったら AIMD（成功で+1 / 制限で半減）で自動減速する。
  3. resume: 中断・失敗後の再実行は、validated 済み & プロンプト未変更のスライドを
     スキップして残りだけ生成する。同じコマンドの再実行が常に安全。

エラー分類:
  - auth_terminal（token_revoked 等）: リトライ無意味。即座に全体停止して再ログインを促す
  - rate_limited（429等）: 並列半減 + バックオフして続行
  - retryable: 通常リトライ・スイープで回収
"""

import os
import sys
import glob
import json
import random
import argparse
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest_utils import (
    classify_error, get_entry, load_manifest, next_version_path, prompt_hash,
    save_manifest, set_session_status, summarize, update_entry, validate_image,
)

PARALLEL_CAP_DEFAULT = 20  # 実測: 2K・並列20で throttle なし（20枚/68秒）
PARALLEL_FLOOR = 2

STYLE_ANCHOR_INSTRUCTION = (
    "\n\n[Style Anchor]\n"
    "添付のスタイルアンカー画像は、このデッキ全体の「デザインの憲法」である。"
    "配色・タイポグラフィの性格・図形言語・質感・余白のリズムをこの画像に忠実に合わせること。"
    "ただし、アンカー画像内の文字・被写体・構図をそのまま複製してはならない。"
    "参照するのはデザイン言語のみで、内容は [Content Data] に従う。\n"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Generate slides in parallel from prompt files (v6 - manifest/sweep/adaptive)'
    )
    parser.add_argument('--prompts-dir', required=True, help='Directory containing prompt text files (*.txt)')
    parser.add_argument('--output-dir', required=True, help='Output directory for slide images')
    parser.add_argument('--manifest', help='Manifest JSON path (default: <output-dir>/../manifest.json)')
    parser.add_argument('--api-key', default='', help='API key (Gemini/OpenAI用。codexはサブスク枠のため不要)')
    parser.add_argument('--provider', default='codex', choices=['gemini', 'openai', 'codex', 'mock'],
                        help='画像生成プロバイダ（mock はテスト用・即時ダミー生成）')
    parser.add_argument('--max-parallel', default='auto',
                        help='並列数。auto=枚数ぶん一斉ファンアウト（上限 --parallel-cap）。数値指定も可')
    parser.add_argument('--parallel-cap', type=int, default=PARALLEL_CAP_DEFAULT,
                        help=f'auto時の並列上限（デフォルト: {PARALLEL_CAP_DEFAULT}。実測でthrottleなしの実証値）')
    parser.add_argument('--max-retries', type=int, default=2, help='子プロセス内のリトライ回数（デフォルト: 2）')
    parser.add_argument('--sweep-rounds', type=int, default=2,
                        help='メインパス後の検証スイープ回数（デフォルト: 2。欠損・破損スライドのみ再生成）')
    parser.add_argument('--max-attempts', type=int, default=5,
                        help='オーケストレータレベルの1スライド総試行上限（デフォルト: 5。乗算爆発の防止）')
    parser.add_argument('--force', action='store_true',
                        help='resume を無効化して全スライドを再生成する')
    parser.add_argument('--per-slide-timeout', type=int, default=None,
                        help='1スライド生成の上限秒（未指定: gemini/openai=240, codex=600, mock=60）')
    parser.add_argument('--logo', help='Logo image path to include in each slide generation')
    parser.add_argument('--image-size', default='2K', choices=['512px', '1K', '2K', '4K'], help='Output resolution (default: 2K)')
    parser.add_argument('--with-dashboard', action='store_true',
                        help='生成と同一プロセスでダッシュボードを起動し、生成後はレビュー送信まで待つ'
                             '（サンドボックスが子プロセスを殺す環境=Codex等での推奨。1コマンドで実況+レビューが完結）')
    parser.add_argument('--dashboard-no-open', action='store_true',
                        help='[with-dashboard] OSブラウザを自動で開かない（内蔵ブラウザで開く場合用）')
    parser.add_argument('--dashboard-timeout', type=int, default=7200,
                        help='[with-dashboard] レビュー待ちの上限秒（デフォルト: 7200）')
    parser.add_argument('--style-anchor',
                        help='スタイルアンカー画像（全スライドに参照画像として渡し、デッキ全体の一貫性を担保）')
    # Gemini固有
    parser.add_argument('--thinking-level', default='High', choices=['minimal', 'High'],
                        help='[Gemini] Thinkingレベル（デフォルト: High）')
    parser.add_argument('--grounding-map', help='[Gemini] Path to grounding map JSON (slide_name → true/false)')
    # 共通
    parser.add_argument('--reference-image-map', help='Path to reference image map JSON (slide_name_pattern → image_path)')
    # OpenAI固有
    parser.add_argument('--quality', default='medium', choices=['auto', 'low', 'medium', 'high'],
                        help='[OpenAI] 画質（デフォルト: medium）')
    parser.add_argument('--input-fidelity', default='high', choices=['low', 'high'],
                        help='[OpenAI] 参考画像への忠実度（デフォルト: high）')
    parser.add_argument('--background', default='auto', choices=['auto', 'transparent', 'opaque'],
                        help='[OpenAI] 背景処理（デフォルト: auto）')
    return parser.parse_args()


class AdaptiveGate:
    """AIMD 並列制御ゲート。

    初手は上限いっぱいで走り、rate_limited を観測したら半減（Multiplicative Decrease）、
    成功が3回続くごとに +1（Additive Increase）で回復する。将来 OpenAI 側が
    レート天井を変えても、固定並列数のように壊れず自動で追従する。
    """

    def __init__(self, initial: int, cap: int, floor: int = PARALLEL_FLOOR):
        self.limit = max(floor, min(initial, cap))
        self.cap = cap
        self.floor = floor
        self.active = 0
        self.success_streak = 0
        self.cond = threading.Condition()

    def acquire(self, abort_event: threading.Event) -> bool:
        with self.cond:
            while self.active >= self.limit:
                if abort_event.is_set():
                    return False
                self.cond.wait(timeout=1.0)
            if abort_event.is_set():
                return False
            self.active += 1
            return True

    def release(self, rate_limited: bool, success: bool) -> None:
        with self.cond:
            self.active -= 1
            if rate_limited:
                old = self.limit
                self.limit = max(self.floor, self.limit // 2)
                self.success_streak = 0
                if self.limit != old:
                    print(f"🛑 レート制限を検知 → 並列数を {old} → {self.limit} に半減", file=sys.stderr)
            elif success:
                self.success_streak += 1
                if self.success_streak >= 3 and self.limit < self.cap:
                    self.limit += 1
                    self.success_streak = 0
            self.cond.notify_all()


def load_map(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def find_reference_image(slide_base: str, ref_map: dict):
    """スライド名に対応するリファレンス画像パスを検索（完全一致優先、次に部分一致）"""
    if not ref_map:
        return None
    if slide_base in ref_map:
        return ref_map[slide_base]
    for pattern, image_path in ref_map.items():
        if pattern in slide_base or slide_base in pattern:
            return image_path
    return None


def extract_slide_info(prompt_filename):
    """プロンプトファイル名から接頭辞とスライド番号を抽出"""
    import re
    match = re.search(r'^(.+)_(\d+)\.txt$', prompt_filename)
    if match:
        return (match.group(1), match.group(2))
    return ("slide", "000")


def run_child(task: dict, args, retry_script: str, per_slide_timeout: int) -> tuple:
    """1スライドを子プロセスで生成する。Returns (success, error_msg)."""
    cmd = [
        sys.executable, retry_script,
        '--provider', args.provider,
        '--prompt', task['prompt'],
        '--output', task['output_path'],
        '--api-key', args.api_key,
        '--max-retries', str(args.max_retries),
        '--image-size', args.image_size,
    ]
    if args.provider == 'gemini':
        cmd.extend(['--thinking-level', args.thinking_level])
        if task['grounding']:
            cmd.append('--grounding')
    elif args.provider == 'openai':
        cmd.extend([
            '--quality', args.quality,
            '--input-fidelity', args.input_fidelity,
            '--background', args.background,
        ])
    if args.logo:
        cmd.extend(['--logo', args.logo])
    if task['reference_image']:
        cmd.extend(['--reference-image', task['reference_image']])
    if task['style_anchor']:
        cmd.extend(['--extra-reference-image', task['style_anchor']])
    if task['raw_dir']:
        cmd.extend(['--raw-dir', task['raw_dir']])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=per_slide_timeout)
        if result.returncode == 0:
            return (True, None)
        return (False, result.stderr or result.stdout or 'unknown error')
    except subprocess.TimeoutExpired:
        return (False, f"Timeout ({per_slide_timeout}s)")
    except Exception as e:
        return (False, str(e))


def worker(task: dict, args, retry_script: str, per_slide_timeout: int,
           gate: AdaptiveGate, abort_event: threading.Event) -> dict:
    """ゲートを通ってから子プロセスを実行し、結果と分類を返す。"""
    if not gate.acquire(abort_event):
        return {'slide_base': task['slide_base'], 'skipped': True}
    rate_limited = False
    success = False
    error_msg = None
    try:
        success, error_msg = run_child(task, args, retry_script, per_slide_timeout)
        if success:
            # 生成物の機械検証（デコード・サイズ・アスペクト・白紙検知）
            problem = validate_image(task['output_path'])
            if problem:
                success = False
                error_msg = f"validation failed: {problem}"
        if not success:
            kind = classify_error(error_msg)
            rate_limited = (kind == 'rate_limited')
            if kind == 'auth_terminal':
                abort_event.set()
            return {'slide_base': task['slide_base'], 'success': False,
                    'error': error_msg, 'kind': kind}
        return {'slide_base': task['slide_base'], 'success': True}
    finally:
        gate.release(rate_limited=rate_limited, success=success)


def build_tasks(prompt_files, args, manifest, images_dir, raw_dir,
                grounding_map, ref_image_map):
    """manifest と突き合わせて、生成が必要なタスクだけを組み立てる。"""
    tasks = []
    skipped = 0
    anchor = args.style_anchor if args.style_anchor and os.path.exists(args.style_anchor) else None
    if args.style_anchor and not anchor:
        print(f"⚠️  スタイルアンカーが見つかりません（無視）: {args.style_anchor}", file=sys.stderr)

    for prompt_file in prompt_files:
        prefix, slide_num = extract_slide_info(os.path.basename(prompt_file))
        slide_base = f"{prefix}_{slide_num}"
        output_path = os.path.join(images_dir, f"{slide_base}.png")

        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt = f.read()
        if anchor:
            prompt += STYLE_ANCHOR_INSTRUCTION
        p_hash = prompt_hash(prompt, extra=os.path.basename(anchor) if anchor else "")

        entry = get_entry(manifest, slide_base)
        # ダッシュボードで削除されたスライドは再生成で復活させない（--force は例外）
        if entry.get('state') == 'removed' and not args.force:
            skipped += 1
            continue
        # resume 判定: validated 済み・プロンプト未変更・実ファイルも健在ならスキップ
        if (not args.force
                and entry.get('state') == 'validated'
                and entry.get('prompt_sha256') == p_hash
                and entry.get('current_image')
                and validate_image(entry['current_image']) is None):
            skipped += 1
            continue

        # 健全な既存版があるスライドの再生成（プロンプト変更・--force）は、<base>.png を
        # 上書きせず次の版番号で保存する（過去版・バージョン比較・ロールバックを守る）
        current = entry.get('current_image')
        if current and os.path.exists(current) and validate_image(current) is None:
            versions = [v for v in (entry.get('versions') or [current]) if os.path.exists(v)]
            if current not in versions:
                versions.append(current)
            output_path, _ver = next_version_path(images_dir, slide_base, versions)

        update_entry(manifest, slide_base,
                     state='pending', prompt_sha256=p_hash,
                     prompt_file=os.path.abspath(prompt_file))
        tasks.append({
            'slide_base': slide_base,
            'prompt': prompt,
            'prompt_file': prompt_file,
            'output_path': output_path,
            'grounding': grounding_map.get(slide_base, False),
            'reference_image': find_reference_image(slide_base, ref_image_map),
            'style_anchor': anchor,
            'raw_dir': raw_dir,
        })
    return tasks, skipped


def run_pass(tasks, args, retry_script, per_slide_timeout, gate, abort_event,
             manifest, manifest_path, label: str) -> list:
    """1パスぶんの並列生成を実行し、失敗タスクのリストを返す。"""
    if not tasks:
        return []
    print(f"\n▶ {label}: {len(tasks)}枚を並列生成（gate limit={gate.limit}）")
    failed_tasks = []
    completed = 0
    with ThreadPoolExecutor(max_workers=gate.cap) as executor:
        futures = {
            executor.submit(worker, task, args, retry_script, per_slide_timeout,
                            gate, abort_event): task
            for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            result = future.result()
            slide_base = result['slide_base']
            completed += 1

            if result.get('skipped'):
                update_entry(manifest, slide_base, state='failed',
                             last_error='aborted before start', last_error_kind='aborted')
                failed_tasks.append(task)
                continue

            entry = get_entry(manifest, slide_base)
            attempts = entry.get('attempts', 0) + 1
            if result['success']:
                versions = entry.get('versions', [])
                if task['output_path'] not in versions:
                    versions = versions + [task['output_path']]
                raw_path = (os.path.join(task['raw_dir'], os.path.basename(task['output_path']))
                            if task['raw_dir'] else None)
                update_entry(manifest, slide_base,
                             state='validated', attempts=attempts,
                             current_image=task['output_path'],
                             raw_image=raw_path if raw_path and os.path.exists(raw_path) else entry.get('raw_image'),
                             versions=versions,
                             last_error=None, last_error_kind=None)
                print(f"✓ [{completed}/{len(tasks)}] {slide_base}.png")
            else:
                update_entry(manifest, slide_base,
                             state='failed', attempts=attempts,
                             last_error=(result.get('error') or '')[:500],
                             last_error_kind=result.get('kind'))
                failed_tasks.append(task)
                short_err = (result.get('error') or 'unknown').strip().splitlines()[-1][:120]
                print(f"✗ [{completed}/{len(tasks)}] {slide_base}.png - {short_err}")

            save_manifest(manifest_path, manifest)
    return failed_tasks


def wait_for_review(dashboard, timeout: int) -> None:
    """レビュー送信（または timeout）までブロックし、受信サマリーを表示する。

    ユーザーが常駐ハブ（別プロセス）から送信した場合は自前サーバの received が
    立たないため、slide_feedback.json の更新もあわせて監視する。
    """
    dashboard.timer.cancel()
    print(f"\n📊 レビュー待ち: {dashboard.url}")
    print("   ダッシュボードで赤入れして「修正を依頼する」を押すと、このコマンドが終了します")
    from review_deck import pending_feedback
    session_dir = os.path.dirname(dashboard.feedback_path)
    start = time.time()
    try:
        while time.time() - start < timeout and not dashboard.received.is_set():
            if dashboard.received.wait(timeout=2):
                break
            # ハブ経由の送信・生成中に届いていた未処理分もキューで検知する
            if pending_feedback(session_dir):
                dashboard.received.set()
                break
    except KeyboardInterrupt:
        pass
    if not dashboard.received.is_set():
        print("⏰ レビュー待ちタイムアウト")
    try:
        dashboard.httpd.shutdown()
    except Exception:
        pass
    from review_deck import report_feedback
    if report_feedback(dashboard) == 0:
        print("   → slide_feedback.json を読んで差分編集に進んでください")


def main():
    args = parse_args()

    model_label = {
        "gemini": "gemini-3.1-flash-image-preview",
        "openai": "gpt-image-2 (OpenAI API)",
        "codex": "gpt-image-2 (Codex subscription)",
        "mock": "mock (test placeholder)",
    }.get(args.provider, "gpt-image-2")

    os.makedirs(args.output_dir, exist_ok=True)
    raw_dir = os.path.join(args.output_dir, 'raw')
    os.makedirs(raw_dir, exist_ok=True)

    manifest_path = args.manifest or os.path.join(
        os.path.dirname(os.path.abspath(args.output_dir)), 'manifest.json')
    manifest = load_manifest(manifest_path)

    prompt_files = sorted(
        glob.glob(os.path.join(args.prompts_dir, '*.txt')),
        key=lambda x: extract_slide_info(os.path.basename(x))[1]
    )
    if not prompt_files:
        print(f"Error: No prompt files (*.txt) found in {args.prompts_dir}")
        sys.exit(1)

    grounding_map = load_map(args.grounding_map)
    ref_image_map = load_map(args.reference_image_map)

    tasks, skipped = build_tasks(prompt_files, args, manifest, args.output_dir, raw_dir,
                                 grounding_map, ref_image_map)
    save_manifest(manifest_path, manifest)

    # ダッシュボードは何より先に起動する（認証ウォームアップや生成を待たせない）
    dashboard = None
    if args.with_dashboard:
        try:
            from review_deck import start_server
            dashboard = start_server(os.path.dirname(manifest_path),
                                     timeout=args.dashboard_timeout,
                                     open_browser=not args.dashboard_no_open)
            threading.Thread(target=dashboard.httpd.serve_forever, daemon=True).start()
            print(f"📊 ダッシュボード: {dashboard.url}（生成の実況が見えます）")
        except Exception as e:
            print(f"⚠️  ダッシュボード起動失敗（生成は続行）: {e}")

    # 並列数の決定: auto = 枚数ぶん全部（上限cap）。数値指定はそのまま尊重
    if str(args.max_parallel).lower() == 'auto':
        initial_parallel = min(len(tasks), args.parallel_cap) if tasks else 1
    else:
        initial_parallel = max(1, int(args.max_parallel))

    per_slide_timeout = args.per_slide_timeout
    if per_slide_timeout is None:
        per_slide_timeout = {'codex': 600, 'mock': 60}.get(args.provider, 240)

    print("=" * 70)
    print(f"Phase 7: Parallel Slide Generation (v6 - provider={args.provider})")
    print("=" * 70)
    print(f"Provider:          {args.provider} / {model_label}")
    print(f"Prompts directory: {args.prompts_dir}")
    print(f"Output directory:  {args.output_dir}")
    print(f"Manifest:          {manifest_path}")
    print(f"Image size:        {args.image_size}")
    print(f"Parallel:          {initial_parallel} (cap={args.parallel_cap}, AIMD adaptive)")
    print(f"Sweep rounds:      {args.sweep_rounds}")
    print(f"Per-slide timeout: {per_slide_timeout}s")
    if args.logo:
        print(f"Logo:              {args.logo}")
    if args.style_anchor:
        print(f"Style anchor:      {args.style_anchor}")
    if skipped:
        print(f"Resume:            {skipped}枚は validated 済みのためスキップ")
    print("=" * 70)

    if not tasks:
        print("\n✓ 全スライドが生成済みです（resume: 何もすることがありません）")
        if dashboard is not None:
            wait_for_review(dashboard, args.dashboard_timeout)
        return 0

    script_dir = os.path.dirname(os.path.abspath(__file__))
    retry_script = os.path.join(script_dir, 'generate_slide_with_retry.py')
    if not os.path.exists(retry_script):
        print(f"Error: {retry_script} not found")
        sys.exit(1)

    # codex: 並列ファンアウト前にトークンを更新（refresh token 競合による失効を防ぐ）
    if args.provider == 'codex':
        try:
            from codex_app_server_client import warmup_auth
            print("\n🔑 Codex 認証ウォームアップ中（並列前にトークン更新）...")
            if not warmup_auth():
                print("❌ Codex 認証が無効です。`codex login` で再ログインしてから再実行してください。")
                sys.exit(2)
            print("✓ 認証OK。並列生成を開始します。")
        except SystemExit:
            raise
        except Exception as e:
            print(f"⚠️  ウォームアップをスキップ（{e}）。続行します。")

    if ((os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_THREAD_ID"))
            and not args.with_dashboard and args.provider != 'mock'):
        print("=" * 70)
        print("⚠️  Codex 環境を検知しました。このサンドボックスでは別コマンドで立てた")
        print("   ダッシュボードが生き残れません。--with-dashboard を付けて再実行すると、")
        print("   生成の実況とレビュー受付がこのコマンド1つで完結します（強く推奨）")
        print("=" * 70)

    gate = AdaptiveGate(initial=initial_parallel, cap=args.parallel_cap)
    abort_event = threading.Event()
    start_time = time.time()
    session_dir_for_status = os.path.dirname(manifest_path)
    set_session_status(session_dir_for_status, "generating",
                       f"{len(tasks)}枚を並列生成中", total=len(prompt_files))

    # ---- メインパス ----
    failed = run_pass(tasks, args, retry_script, per_slide_timeout, gate, abort_event,
                      manifest, manifest_path, label="メインパス")

    # ---- 検証スイープ: 欠損スライドだけを再生成（ゼロ欠損保証） ----
    for round_no in range(1, args.sweep_rounds + 1):
        if abort_event.is_set() or not failed:
            break
        # 総試行上限を超えたスライドは打ち切り（乗算爆発の防止）
        retry_tasks = []
        for task in failed:
            entry = get_entry(manifest, task['slide_base'])
            if entry.get('last_error_kind') == 'auth_terminal':
                continue
            if entry.get('attempts', 0) >= args.max_attempts:
                print(f"⏭  {task['slide_base']}: 総試行上限（{args.max_attempts}回）に達したため打ち切り")
                continue
            retry_tasks.append(task)
        if not retry_tasks:
            break
        # 一時的な障害（レート制限の嵐）が過ぎるのを待ってから再挑戦（jitter付き）
        backoff = min(30.0, 5.0 * round_no) + random.uniform(0, 3)
        print(f"\n🔁 検証スイープ {round_no}/{args.sweep_rounds}: "
              f"{len(retry_tasks)}枚を再生成（{backoff:.0f}秒待機後）")
        time.sleep(backoff)
        failed = run_pass(retry_tasks, args, retry_script, per_slide_timeout, gate, abort_event,
                          manifest, manifest_path, label=f"スイープ {round_no}")

    elapsed = time.time() - start_time
    counts = summarize(manifest)
    validated = counts.get('validated', 0)
    total_in_manifest = len(manifest.get('slides', {}))

    print("\n" + "=" * 70)
    print("Generation Summary (v6)")
    print("=" * 70)
    print(f"Elapsed:              {elapsed:.0f}s")
    print(f"Slides in manifest:   {total_in_manifest}")
    print(f"Validated:            {validated}")
    print(f"Failed:               {counts.get('failed', 0)}")
    print(f"Manifest:             {manifest_path}")
    print("=" * 70)

    if abort_event.is_set():
        print("\n❌ 認証エラーで中断しました。`codex login` で再ログイン後、"
              "同じコマンドを再実行してください（生成済みスライドは resume でスキップされます）。")
        sys.exit(2)

    set_session_status(session_dir_for_status,
                       "attention" if failed or abort_event.is_set() else "done",
                       "一部スライドが未完成（再実行で回収されます）" if failed else "全スライド生成完了")

    if dashboard is not None:
        wait_for_review(dashboard, args.dashboard_timeout)

    if failed:
        print("\n⚠️  未完成のスライドがあります:")
        for task in failed:
            entry = get_entry(manifest, task['slide_base'])
            print(f"  - {task['slide_base']}: {(entry.get('last_error') or '')[:150]}")
        print("\n同じコマンドを再実行すると、未完成分だけが再生成されます（resume）。")
        sys.exit(1)

    print("\n✓ 全スライド生成・検証完了（ゼロ欠損）")
    return 0


if __name__ == '__main__':
    sys.exit(main())

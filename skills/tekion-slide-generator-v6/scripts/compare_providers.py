#!/usr/bin/env python3
"""同じプロンプトで Gemini と OpenAI を並列実行し、比較出力を生成する。

出力構成:
    <output-dir>/
      ├── gemini/   ← Gemini 生成結果
      ├── openai/   ← OpenAI 生成結果
      └── comparison.md  ← 比較メタデータ

使用方法:
    # prompts-dir 配下の全プロンプトを両プロバイダで生成
    python3 compare_providers.py \
      --prompts-dir ${SESSION}/prompts \
      --output-dir ${SESSION}/compare \
      --gemini-api-key "${GEMINI_API_KEY}" \
      --openai-api-key "${OPENAI_API_KEY}" \
      --image-size 2K

    # 特定のプロンプトのみ（先頭5枚）
    python3 compare_providers.py --prompts-dir ... --limit 5
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
GENERATE_SCRIPT = SCRIPT_DIR / "generate_slide_with_retry.py"


def parse_args():
    p = argparse.ArgumentParser(description="Gemini と OpenAI を同じプロンプトで比較生成")
    p.add_argument("--prompts-dir", required=True, help="プロンプトファイル(*.txt)のディレクトリ")
    p.add_argument("--output-dir", required=True, help="比較結果の出力ディレクトリ")
    p.add_argument("--gemini-api-key", help="Gemini APIキー（省略時はGEMINI_API_KEY）")
    p.add_argument("--openai-api-key", help="OpenAI APIキー（省略時はOPENAI_API_KEY）")
    p.add_argument("--image-size", default="2K", choices=["512px", "1K", "2K", "4K"],
                   help="出力解像度（デフォルト: 2K）")
    p.add_argument("--limit", type=int, help="先頭N枚のみ生成（実測テスト用）")
    p.add_argument("--max-parallel-gemini", type=int, default=10, help="Gemini並列数")
    p.add_argument("--max-parallel-openai", type=int, default=5, help="OpenAI並列数（Tier 3推奨=5-10）")
    p.add_argument("--max-retries", type=int, default=3, help="リトライ回数")
    p.add_argument("--logo", help="ロゴ画像パス（両プロバイダに渡す）")
    p.add_argument("--providers", nargs="+", default=["gemini", "openai"],
                   choices=["gemini", "openai"],
                   help="比較対象プロバイダ（デフォルト: gemini openai）")
    return p.parse_args()


def _resolve_api_key(provider: str, cli_value, env_var: str) -> str:
    key = cli_value or os.environ.get(env_var, "")
    if not key:
        print(f"❌ {env_var} が未設定です", file=sys.stderr)
        sys.exit(1)
    return key


def _generate_one(
    prompt_file: Path,
    output_path: Path,
    provider: str,
    api_key: str,
    image_size: str,
    max_retries: int,
    logo: str | None,
) -> tuple[bool, str, float, str]:
    """1枚生成し、(成功, プロバイダ, 経過秒, エラーメッセージ) を返す。"""
    start = time.time()
    try:
        prompt_text = prompt_file.read_text(encoding="utf-8")
    except Exception as e:
        return (False, provider, 0.0, f"prompt read error: {e}")

    cmd = [
        sys.executable, str(GENERATE_SCRIPT),
        "--provider", provider,
        "--prompt", prompt_text,
        "--output", str(output_path),
        "--api-key", api_key,
        "--max-retries", str(max_retries),
        "--image-size", image_size,
    ]
    if logo:
        cmd.extend(["--logo", logo])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        elapsed = time.time() - start
        if result.returncode == 0:
            return (True, provider, elapsed, "")
        err = result.stderr.strip().splitlines()
        tail = " | ".join(err[-3:]) if err else "unknown"
        return (False, provider, elapsed, tail)
    except subprocess.TimeoutExpired:
        return (False, provider, time.time() - start, "timeout")
    except Exception as e:
        return (False, provider, time.time() - start, f"{type(e).__name__}: {e}")


def main():
    args = parse_args()

    if not GENERATE_SCRIPT.exists():
        print(f"❌ 生成スクリプト不在: {GENERATE_SCRIPT}", file=sys.stderr)
        sys.exit(1)

    prompts_dir = Path(args.prompts_dir)
    if not prompts_dir.is_dir():
        print(f"❌ prompts-dir が見つかりません: {prompts_dir}", file=sys.stderr)
        sys.exit(1)

    prompt_files = sorted(prompts_dir.glob("*.txt"))
    if args.limit:
        prompt_files = prompt_files[: args.limit]
    if not prompt_files:
        print(f"❌ {prompts_dir} に .txt プロンプトが見つかりません", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # プロバイダ別のAPIキーと並列数
    provider_config = {}
    if "gemini" in args.providers:
        provider_config["gemini"] = {
            "api_key": _resolve_api_key("gemini", args.gemini_api_key, "GEMINI_API_KEY"),
            "parallel": args.max_parallel_gemini,
        }
        (output_dir / "gemini").mkdir(exist_ok=True)
    if "openai" in args.providers:
        provider_config["openai"] = {
            "api_key": _resolve_api_key("openai", args.openai_api_key, "OPENAI_API_KEY"),
            "parallel": args.max_parallel_openai,
        }
        (output_dir / "openai").mkdir(exist_ok=True)

    print("=" * 70)
    print("🔬 Compare Providers")
    print("=" * 70)
    print(f"Prompts:     {len(prompt_files)}")
    print(f"Providers:   {', '.join(args.providers)}")
    print(f"Image size:  {args.image_size}")
    print(f"Output:      {output_dir}")
    print("=" * 70)

    results: dict[str, list[dict]] = {p: [] for p in args.providers}
    overall_start = time.time()

    for provider in args.providers:
        cfg = provider_config[provider]
        print(f"\n▶ [{provider}] 生成開始 ({len(prompt_files)}枚 / 並列{cfg['parallel']})")
        provider_start = time.time()
        sub_out = output_dir / provider

        with ThreadPoolExecutor(max_workers=cfg["parallel"]) as executor:
            future_to_name = {}
            for pf in prompt_files:
                out_path = sub_out / f"{pf.stem}.png"
                fut = executor.submit(
                    _generate_one,
                    pf, out_path, provider, cfg["api_key"],
                    args.image_size, args.max_retries, args.logo,
                )
                future_to_name[fut] = pf.name

            completed = 0
            for fut in as_completed(future_to_name):
                completed += 1
                ok, prov, elapsed, err = fut.result()
                name = future_to_name[fut]
                status = "✓" if ok else "✗"
                print(f"  {status} [{completed}/{len(prompt_files)}] {prov}:{name} ({elapsed:.1f}s)"
                      + (f" - {err}" if not ok else ""))
                results[provider].append({
                    "prompt": name,
                    "success": ok,
                    "elapsed_sec": round(elapsed, 2),
                    "error": err if not ok else None,
                })
        provider_elapsed = time.time() - provider_start
        success_count = sum(1 for r in results[provider] if r["success"])
        print(f"  → {provider}: {success_count}/{len(prompt_files)} 成功 ({provider_elapsed:.1f}s)")

    total_elapsed = time.time() - overall_start

    # 比較レポート
    report_path = output_dir / "comparison.md"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("# Provider 比較レポート\n\n")
        f.write(f"- 対象プロンプト: {len(prompt_files)}枚\n")
        f.write(f"- 解像度: {args.image_size}\n")
        f.write(f"- 総経過時間: {total_elapsed:.1f}秒\n\n")
        for provider in args.providers:
            success = sum(1 for r in results[provider] if r["success"])
            avg_time = (
                sum(r["elapsed_sec"] for r in results[provider] if r["success"]) / success
                if success else 0.0
            )
            f.write(f"## {provider}\n\n")
            f.write(f"- 成功: {success}/{len(prompt_files)}\n")
            f.write(f"- 平均生成時間: {avg_time:.1f}秒\n")
            f.write(f"- 出力先: `{output_dir / provider}`\n\n")
            f.write("| プロンプト | 成功 | 時間 | エラー |\n")
            f.write("|-----------|:---:|:---:|-------|\n")
            for r in results[provider]:
                mark = "✓" if r["success"] else "✗"
                err = (r["error"] or "").replace("|", "/")[:80]
                f.write(f"| {r['prompt']} | {mark} | {r['elapsed_sec']}s | {err} |\n")
            f.write("\n")

    # JSON も書き出し（後続集計用）
    (output_dir / "comparison.json").write_text(
        json.dumps({
            "prompts": [p.name for p in prompt_files],
            "providers": args.providers,
            "image_size": args.image_size,
            "total_elapsed_sec": round(total_elapsed, 2),
            "results": results,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print(f"📊 レポート: {report_path}")
    print(f"📦 JSON:     {output_dir / 'comparison.json'}")
    print("=" * 70)

    all_ok = all(all(r["success"] for r in results[p]) for p in args.providers)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

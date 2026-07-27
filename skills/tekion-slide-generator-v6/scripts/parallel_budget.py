#!/usr/bin/env python3
"""同時生成数の上限を、そのマシンが実際に抱えられる数から決める。

以前は定数 20 だった。この 20 は「20並列で throttle が出なかった」という実測から
来た数字で、**そこが天井だという根拠は無い**。しかも1つの数字が2種類の制約を
兼ねてしまっていた:

  - 遠い側（レート制限）… 429 を見て半減する AIMD が既に面倒を見ている。
    ここに固定の上限を置く意味はなく、置けば速くなれる分を捨てるだけ
  - 近い側（このマシンの資源）… AIMD からは見えない。ローカルの OOM は 429 として
    現れないので、減速のきっかけが無いまま落ちる

だから上限は「近い側」だけを見る。1枚の生成は `codex exec` の子プロセス1本で、
プロセス木のピークが実測 約205MB（2026-07 / macOS・画像1枚のターン）。
空きメモリをこの見積りで割った数が、そのマシンの現実的な同時実行数になる。

  256GB の Mac（空き100GB級） → 400本相当 = 実質「ページ数ぶん全部」
  25GB の Docker VM（空き17GB） → 60本程度に自動で収まる

同じコードが両方で安全に走る。明示的に外したいときは cap=0（無制限）。
"""
from __future__ import annotations

import os
import subprocess
import sys

# codex exec 1本のプロセス木のピーク実測 205MB に、画像バイトとブレの余裕を足した見積り
PER_CHILD_MB = 250
FLOOR = 4          # これ以上は下げない（数枚のデッキで直列になっても意味がない）
FALLBACK = 20      # 空きメモリが読めない環境での保守値（従来の既定値）


def available_mb() -> int | None:
    """今すぐ使える物理メモリ（MB）。読めなければ None。"""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:  # Linux
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                                 timeout=5).stdout
            page = 4096
            free = inactive = 0
            for line in out.splitlines():
                if "page size of" in line:
                    page = int(line.split("page size of")[1].split()[0])
                elif line.startswith("Pages free:"):
                    free = int(line.rsplit(":", 1)[1].strip().rstrip("."))
                elif line.startswith("Pages inactive:"):
                    # inactive は再利用可能。free だけだと macOS では極端に小さく出る
                    inactive = int(line.rsplit(":", 1)[1].strip().rstrip("."))
            if free or inactive:
                return (free + inactive) * page // (1024 * 1024)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


def ceiling(cap_option: int | None = None) -> tuple[int, str]:
    """同時実行の上限と、その根拠の一言を返す。

    cap_option: None/負 = 自動（空きメモリから算出）、0 = 無制限、正の数 = その値。
    環境変数 TEKION_PARALLEL_CAP でも同じ指定ができる（CLI が優先）。
    """
    if cap_option is None:
        env = os.environ.get("TEKION_PARALLEL_CAP")
        if env and env.strip().lstrip("-").isdigit():
            cap_option = int(env)
    if cap_option is not None and cap_option >= 0:
        if cap_option == 0:
            return sys.maxsize, "無制限（明示指定）"
        return cap_option, f"{cap_option}（明示指定）"

    free = available_mb()
    if free is None:
        return FALLBACK, f"{FALLBACK}（空きメモリを取得できないため保守値）"
    limit = max(FLOOR, free // PER_CHILD_MB)
    return limit, f"{limit}（空き {free // 1024}GB ÷ 1本{PER_CHILD_MB}MB）"

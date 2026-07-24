#!/usr/bin/env bash
set -euo pipefail

LABEL="jp.tekion.slides.hub"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SKILL_DIR="$(dirname -- "$SCRIPT_DIR")"
TEKION_HOME="${TEKION_SLIDES_HOME:-${HOME}/.tekion-slides}"
HUB_ROOT="${TEKION_HOME}/hub"
RUNTIME_DIR="${HUB_ROOT}/scripts"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_FILE="${TEKION_HOME}/hub.log"
PORT="${TEKION_DASHBOARD_PORT:-7799}"
DOMAIN="gui/${UID}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "このインストーラは macOS launchd 用です。" >&2
  echo "Linux/WSL では hub_server.py を systemd --user の ExecStart に指定してください。" >&2
  exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "${DOMAIN}/${LABEL}" >/dev/null 2>&1 || true
  if [[ -f "$PLIST" ]]; then
    rm -f -- "$PLIST"
  fi
  echo "TEKION Slides Hub の LaunchAgent を削除しました。"
  echo "ランタイムは再利用できるよう ${HUB_ROOT} に残しています。"
  exit 0
fi

PYTHON_BIN="$(command -v python3)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "python3 が見つかりません。" >&2
  exit 1
fi

mkdir -p -- "$RUNTIME_DIR" "${HUB_ROOT}/assets" "$(dirname -- "$PLIST")"

# hub_server.py は review_deck.py、export/import 系、UI アセットを実行時に使う。
# プラグイン更新で元のキャッシュパスが消えても動くよう、必要なランタイム一式をコピーする。
for source in "$SCRIPT_DIR"/*.py; do
  cp -f -- "$source" "$RUNTIME_DIR/"
done
if [[ -d "${SCRIPT_DIR}/providers" ]]; then
  mkdir -p -- "${RUNTIME_DIR}/providers"
  for source in "${SCRIPT_DIR}/providers"/*.py; do
    cp -f -- "$source" "${RUNTIME_DIR}/providers/"
  done
fi
if [[ -d "${SKILL_DIR}/assets/ui" ]]; then
  mkdir -p -- "${HUB_ROOT}/assets/ui"
  for source in "${SKILL_DIR}/assets/ui"/*; do
    [[ -f "$source" ]] && cp -f -- "$source" "${HUB_ROOT}/assets/ui/"
  done
fi
# デフォルトロゴ等のトップレベルアセットもコピーする
# （runtime の resolve_brand.py は <hub-root>/assets/logo.png を既定にするため、
#  無いと自動ワーカーの編集でロゴが再合成できず消える）
for source in "${SKILL_DIR}/assets"/*; do
  [[ -f "$source" ]] && cp -f -- "$source" "${HUB_ROOT}/assets/"
done

VERSION="$(
  python3 -c 'import json, pathlib, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", "6.0.0"))' \
    "${SKILL_DIR}/../../.codex-plugin/plugin.json" 2>/dev/null || echo "6.0.0"
)"

TMP_PLIST="$(mktemp "${TMPDIR:-/tmp}/tekion-hub.XXXXXX.plist")"
trap 'rm -f -- "$TMP_PLIST"' EXIT
sed \
  -e "s|__LABEL__|${LABEL}|g" \
  -e "s|__PYTHON__|${PYTHON_BIN}|g" \
  -e "s|__HUB_SERVER__|${RUNTIME_DIR}/hub_server.py|g" \
  -e "s|__TEKION_HOME__|${TEKION_HOME}|g" \
  -e "s|__PORT__|${PORT}|g" \
  -e "s|__VERSION__|${VERSION}|g" \
  -e "s|__LOG_FILE__|${LOG_FILE}|g" \
  "${SCRIPT_DIR}/launchd_hub.plist.in" > "$TMP_PLIST"
cp -f -- "$TMP_PLIST" "$PLIST"

launchctl bootout "${DOMAIN}/${LABEL}" >/dev/null 2>&1 || true
# bootout 直後の bootstrap は launchd 側の後始末と競合して失敗することがある（実測）→ リトライ
bootstrap_ok=0
for _attempt in 1 2 3 4 5; do
  if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then
    bootstrap_ok=1
    break
  fi
  sleep 1
done
if [[ "$bootstrap_ok" != 1 ]]; then
  echo "launchctl bootstrap に失敗しました。少し待ってから再実行してください。" >&2
  exit 1
fi
launchctl enable "${DOMAIN}/${LABEL}"
launchctl kickstart -k "${DOMAIN}/${LABEL}"

echo "TEKION Slides Hub をインストールしました: http://127.0.0.1:${PORT}/"
echo "ログ: ${LOG_FILE}"

# Linux/WSL 用 systemd user unit の雛形:
# [Unit]
# Description=TEKION Slides Hub
# [Service]
# ExecStart=/absolute/path/to/python3 /absolute/path/to/hub_server.py
# Environment=TEKION_DASHBOARD_PORT=7799
# Restart=always
# [Install]
# WantedBy=default.target

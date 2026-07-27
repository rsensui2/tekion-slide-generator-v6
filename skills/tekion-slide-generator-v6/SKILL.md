---
name: tekion-slide-generator-v6
description: "TEKION Slide Generator v6 — Markdown/テキストから日本語プレゼンスライド（16:9）を生成し PPTX/PDF まで書き出す。ユーザーが『スライドを作って』『プレゼン資料/提案書/企画書/ピッチデッキにして』『この資料をデッキ化』『登壇用の資料』のようにスライド作成を求めたら、ツール名を明示しなくても使うこと。既存の PPTX/PDF を『このパワポを直して』『既存の資料を修正して』と言われた場合もこのスキルで取り込んで改修する。起点はブラウザの『スライドダッシュボード』: 生成の実況、スライド毎の赤入れ→ワンクリック修正依頼、バージョン比較（選択=確定）、既存デッキのドラッグ&ドロップ取り込み、PPTX/PDF ダウンロードまで1画面で完結。核となる機能: (1) 枚数ぶんの一斉並列生成 + レート制限時の自動減速、(2) manifest + 検証スイープ + resume で『N枚頼んだらN枚返る』ゼロ欠損保証、(3) 差分編集・赤ペン編集・ロールバック。画像生成は Codex 内蔵 gpt-image-2（ChatGPT/Codex サブスク枠、API 従量課金なし）。"
---

# TEKION Slide Generator v6

Markdown/テキストから日本語プレゼンスライド（16:9）を生成し、PPTX/PDF まで書き出す。

| 柱 | 実装 |
|---|---|
| フルスロットル並列 | `--max-parallel auto` = 枚数ぶん一斉ファンアウト（上限は空きメモリから自動算出）。429検知で自動減速 |
| ゼロ欠損保証 | manifest（台帳）+ 生成後の機械検証 + 検証スイープ + resume |
| 直せる | 作り直し（既定）・参照つき微修正・赤ペン編集・バージョニング・ロールバック |
| 同じ顔 | 構造化デザインシステムの全プロンプト同一注入（スタイルアンカーはオプトイン） |

Codex を呼ぶコマンドの直前では `unset OPENAI_API_KEY` する（各 Phase のコマンドに含めてある。
キーが残っていると公式仕様で API 従量課金に切り替わるため）。

## 実行モード

```yaml
mode: auto  # Pre-flight〜Phase 9を承認なしで連続実行
pause_only_on: [route_ambiguous, codex_not_logged_in]
chain_commands: true  # bashは && で連結
speed_first: true  # 下記「立ち上がりを速くする」を守る
```

**立ち上がりを速くする**（ユーザーはダッシュボードに最初のスライドが出るまでの時間で体感する）:
- 読むのは SKILL.md だけでよい。references/ はプリセット・テンプレートの置き場で、
  必要になってから読む
- アクティブプリセットがあれば design_guidelines はコピーのみ（執筆しない）。無い場合も
  テンプレートの Brand Design System の値を差し替える程度に留め、ゼロから書き起こさない
- Phase 1（ダッシュボード起動）を最優先で実行してから Phase 2 以降を考える。
  ユーザーが待っている間、画面に実況が出ていることが最重要
- slides_plan.json は1パスで書き切る（下書き→推敲の往復をしない）

## 定数

```bash
SKILL_DIR="<path-to-this-skill>"
PYTHON="python3"
```

## 前提条件

- Python 3.10+（Pillow / python-pptx / requests / Jinja2）
- **Codex CLI** がインストール済み・ログイン済み（`~/.codex/auth.json` 存在）
- 画像生成は Codex 内蔵 gpt-image-2（サブスク枠）。**API キーは不要**。

初回セットアップ:
```bash
bash "${SKILL_DIR}/scripts/setup.sh"
```

## 言語ルール

```yaml
display_language: "ターゲット聴衆に合わせる（素材の言語ではない）"
target_fields: [title, subtitle, key_message, display_copy, content]
english_allowed: "固有名詞のみ (Cursor, MCP等)"
content_suffix: "※スライド上の全テキストは{lang}で表示すること。"
```

## スタイル選択

| 判断基準 | スタイル | テンプレート |
|----------|:---:|---|
| 「登壇」「ピッチ」「Keynote風」「ビジュアル重視」 | **visual** | `prompt_template_visual.j2` |
| 「営業資料」「提案書」「バランス」（デフォルト） | **balanced** | `prompt_template_balanced.j2` |

- スライド毎のオーバーライド: JSON に `"_style": "visual"` を付与
- `--style balanced` + 表紙のみ `_style: visual` が最頻パターン

---

## Pre-flight

```bash
command -v codex >/dev/null && [ -f "${CODEX_HOME:-$HOME/.codex}/auth.json" ] && echo "codex OK" || echo "STOP: codex 未導入/未ログイン"
python3 -c "import PIL, pptx, requests, jinja2; print('OK')" 2>/dev/null || bash "${SKILL_DIR}/scripts/setup.sh"
```

未ログインなら **STOP**（`codex` を一度起動してログイン）。

## Phase 1: セッション準備 + スライドダッシュボードの起動（ここが起点）

```bash
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S) && OUTPUT_DIR="[指定された出力先]" && SESSION_DIR="${OUTPUT_DIR}/slides_output/${TIMESTAMP}" && mkdir -p "${SESSION_DIR}/json" "${SESSION_DIR}/prompts" "${SESSION_DIR}/images"
```

（秒まで含めることで同時起動セッションの衝突を防ぐ）

**OUTPUT_DIR はローカルディスクに取ること**（~/Desktop や ~/Documents 直下等）。
Google Drive / Dropbox 等のクラウド同期フォルダ配下は、同期による manifest の巻き戻り・
書き込み瞬断が起きるため不可。完成した PPTX/PDF だけを最後にクラウドへコピーする。

続けてセッションを台帳へ登録し、常駐ハブの固定 URL を得る:

```bash
SID=$(${PYTHON} "${SKILL_DIR}/scripts/session_registry.py" --register "${SESSION_DIR}")
PORT="${TEKION_DASHBOARD_PORT:-7799}"
HUB_URL="http://127.0.0.1:${PORT}"
DECK_URL="${HUB_URL}/s/${SID}/"
curl -fsS "${HUB_URL}/healthz"
```

**動いているハブが、今使おうとしているスキルと同じコードかを必ず確かめる。**
ハブは scripts/ をコピーして常駐するため、スキルだけ新しくても実体は古いままになり得る:

```bash
RUNNING=$(curl -fsS "${HUB_URL}/healthz" | ${PYTHON} -c 'import json,sys;print(json.load(sys.stdin)["version"])')
EXPECTED=$(${PYTHON} "${SKILL_DIR}/scripts/hub_version.py")
[ "${RUNNING##*+}" = "${EXPECTED##*+}" ] && echo "hub OK" || echo "STALE: ${RUNNING} != ${EXPECTED}"
```

版は `<版番号>+<実体のハッシュ>`。**比較するのは `+` の後ろ**（版番号は導入場所で
変わるが、ハッシュはコードが同じなら一致する）。中身が1バイトでも違えば食い違いとして
出るので、版を上げ忘れた変更も検知できる。healthz が失敗した場合、または版が違う場合:

- **Claude Code**: `bash "${SKILL_DIR}/scripts/install_hub.sh"` を1回実行する
- **Codex**（サンドボックス内から launchd 登録はできない）: ユーザーに「ターミナルで
  `bash <skill>/scripts/install_hub.sh` を1回実行してください（初回のみ）」と案内し、
  それまでは下の「ハブ無しフォールバック」で進める

**ブラウザは会話の最初の1回だけ開く**:

```bash
curl -fsS "${DECK_URL}viewers"
```

`{"active": false}` のときだけ開く（Claude は `open "${DECK_URL}"`、Codex はアプリ内 Browser か
URL 提示）。`true` なら既にユーザーが見ている。以後このタスク中は二度と開かない —
生成・修正の反映は表示中のタブが自動で拾う。

**修正指示の処理はハブが自動で行う**ので、エージェントの待ち受けは不要 — デッキ URL を
提示したら応答を終えてよい（詳細と例外は Phase 8）。生成前・生成中に届いた指示も
未処理キューに積まれ、順に処理される。

**ハブ無しフォールバック**（従来フロー。自動処理も動かないため待ち受けが必要）:
- Claude Code: `--serve --persist` と `--await-feedback --serve-timeout 28800` をバックグラウンドで
- Codex: 生成は Phase 7 に `--with-dashboard` を付け、レビュー待ちは
  `--await-feedback --serve-timeout 300` を前面実行。exit 0 → Phase 8 の編集 → `--ack-feedback` → 待機に戻る。
  exit 2 → 1回だけ再実行（計600秒）。それでも無ければ待機を終了する
  （1ターンの待機は合計30分まで。ユーザーが明示的に延長を頼んだ時だけ延長）。
  終了時は「修正指示は保存されます。『続きを』で再開できます」と必ず案内する

既存デッキの改修はユーザーがハブに PPTX/PDF/画像をドロップするだけで始まる
（Phase 2〜7 不要 → そのまま赤入れループへ）。新規生成の進捗は「生成中 n / N」として
ハブに実況され、完成したスライドから順に画面に現れる。

## Phase 2: デザインガイドライン作成

まずダッシュボードに現在ステージを知らせる（実況表示に出る）:

```bash
echo '{"stage":"planning","detail":"デザインとスライド構成を執筆中"}' > "${SESSION_DIR}/session_status.json"
```

`${SKILL_DIR}/references/design_guidelines_template.md` をベースに `${SESSION_DIR}/design_guidelines.md` を作る。

1. 冒頭の **Brand Design System** YAML の hex 8色・字級・角丸・余白リズムを、プロジェクトに合わせた具体値で埋める（このブロックが全スライドに同一注入され、デッキの一貫性を作る）
2. 配色・写真スタイル・トーンの各セクションを埋める

**プリセット使用時**（`design-setup` スキルで設定済みの場合）はコピーで済ませる:

```bash
# プリセットはユーザーホーム優先（プラグイン更新で消えない場所）。無ければスキル同梱を使う
PRESETS_DIR="${TEKION_PRESETS_DIR:-$HOME/.tekion-slides/presets}"
[ -d "${PRESETS_DIR}" ] || PRESETS_DIR="${SKILL_DIR}/references/presets"
if [ -f "${PRESETS_DIR}/.active_preset" ]; then
  PRESET_NAME=$(cat "${PRESETS_DIR}/.active_preset")
  PRESET_PATH="${PRESETS_DIR}/${PRESET_NAME}"
  [ -f "${PRESET_PATH}" ] || PRESET_PATH="${SKILL_DIR}/references/presets/example-preset.md"
else
  PRESET_PATH="${SKILL_DIR}/references/presets/example-preset.md"
fi
cp "${PRESET_PATH}" "${SESSION_DIR}/design_guidelines.md"
echo "Using preset: $(basename "${PRESET_PATH}")"
```

## Phase 3: slides_plan.json 作成（最重要）

**Claude（このエージェント）が単一パスで直接作成**する。入力 Markdown/テキストを読み、
各スライドの内容を丁寧に書く。

### スキーマ — content 3分割

各スライドを「描く文字（display_copy）」と「描かない指示（visual_brief / layout_hint）」に分けて書く。
display_copy は見出し+3-5項目に絞り、末尾に言語強制サフィックスを入れる:

```json
{
  "slides": [
    {
      "slide_number": 0,
      "source_file": "00_cover",
      "title": "タイトル",
      "subtitle": "サブタイトル",
      "key_message": "核心メッセージ1文",
      "display_copy": "スライドに描画する文字そのもの。\n1行1要素で全行そのまま描画される。\n※スライド上の全テキストは日本語で表示すること。",
      "visual_brief": "背景写真の被写体・構図・雰囲気、図解の内容、アクセント色の配置など（描画されない指示）",
      "layout_hint": "Pattern C: 3列カードグリッド"
    }
  ],
  "total_slides": 12
}
```

- **必須**: `slide_number`(int), `source_file`, `title`, `subtitle`, さらに `display_copy` **または** `content`（全部入り `content` 1フィールドでも可）
- **オプション**: `key_message`, `visual_brief`, `layout_hint`, `_style`
- 使えるフィールドは上記のみ（`layout` や `image_prompt` 等を発明しない。バリデータが弾く）

**構成目安:** 表紙(G) → 課題提起(C/D) → ソリューション(B/C) → 市場(J) → ビジネスモデル(E) → 差別化(H/A) → ロードマップ(I) → まとめ(D) → CTA(G)

**命名規則:** 表紙 `"00_cover"` / 本編 `"01_xxx"`〜`"97_xxx"` / まとめ `"98_summary"` / CTA `"99_cta"`

### バリデーション

```bash
cat > "${SESSION_DIR}/json/slides_plan.json" << 'JSONEOF'
{作成したJSON}
JSONEOF
${PYTHON} "${SKILL_DIR}/scripts/validate_slides_json.py" --file "${SESSION_DIR}/json/slides_plan.json"
```

## Phase 4: プロンプト生成

```bash
PRESETS_DIR="${TEKION_PRESETS_DIR:-$HOME/.tekion-slides/presets}"
[ -d "${PRESETS_DIR}" ] || PRESETS_DIR="${SKILL_DIR}/references/presets"
if [ -f "${PRESETS_DIR}/.active_style" ]; then
  STYLE=$(cat "${PRESETS_DIR}/.active_style")
  [ -z "${STYLE}" ] && STYLE=balanced
else
  STYLE=balanced
fi

${PYTHON} "${SKILL_DIR}/scripts/generate_prompts_from_json.py" \
  --session-dir "${SESSION_DIR}" \
  --json-file json/slides_plan.json \
  --output-dir prompts \
  --design-guidelines "${SESSION_DIR}/design_guidelines.md" \
  --style "${STYLE}" \
  --image-size 2K
```

## Phase 5: スタイルアンカー生成（デフォルトでは作らない）

**デフォルトはスキップ。** Brand Design System の同一注入だけで一貫性は十分出る。
参照画像には実測でデメリットがある（2026-07 マスター判断）:

- アンカー生成自体が遅い（420秒タイムアウトが起きやすく、リトライで10分超になる）
- 全スライドがアンカー画像に引っ張られ、「Aa」や配色スウォッチ等のアンカー要素が
  本番スライドに混入することがある（実測: 12枚中4枚に混入）

ユーザーが明示的に求めたときだけ、デッキの「デザインの憲法」となる抽象デザインボードを1枚生成し、
Phase 7 で全スライドの参照画像として渡す。図形と配色だけで構成し、文字は「Aa」1箇所だけ描かせる:

```bash
unset OPENAI_API_KEY 2>/dev/null
cat > "${SESSION_DIR}/style_board_prompt.txt" << 'EOF'
抽象的なデザインボードを1枚生成。
[Brand Design System の配色hexを使い] 上部に primary の太い水平帯、中央に角丸カード1枚
（薄い影+極細枠）、accent の図形1つ、下部に配色スウォッチを横一列。
文字は「Aa」とだけ大きく太字で描く。他の文字は一切描かない。
実際のスライドと同じトーン。特定のトピック内容は含めない。余白多め。16:9。
EOF
${PYTHON} "${SKILL_DIR}/scripts/codex_app_server_client.py" \
  --prompt "$(cat "${SESSION_DIR}/style_board_prompt.txt")" \
  --output "${SESSION_DIR}/style_board.png" \
  --image-size 2K --aspect 16:9 --backend auto --max-retries 2
```

作った場合は `style_board.png` を Read で目視確認してから Phase 7 に渡し、生成後の各スライドに
アンカー要素（「Aa」・スウォッチ）が混入していないか必ず目視チェックする。

## Phase 6: リファレンス画像マップ（任意）

**まずアセットライブラリを確認する**。よく使うキャラクター・写真の常設置き場:

```bash
ASSETS_DIR="${TEKION_ASSETS_DIR:-$HOME/.tekion-slides/assets}"
[ -f "${ASSETS_DIR}/assets.md" ] && cat "${ASSETS_DIR}/assets.md"
```

`assets.md` には各アセットの「使いどころ」が書かれている。スライド構成とユーザー指示を照らし、
該当するスライドに割り当てる。**ユーザー指示にアセットの名前（例:「りょーこ」）があれば、
該当スライドで必ず参照画像に含め、プロンプトに「参照画像のキャラクターの顔立ち・髪型・雰囲気を
忠実に保つこと」を追記する**。アセットの登録・追加は design-setup スキル
（「デザインを設定したい」「キャラクターを登録したい」）で行える。

割り当ては reference_image_map.json に書く:

```bash
cat > "${SESSION_DIR}/reference_image_map.json" << 'JSONEOF'
{
  "00_cover": "/Users/<user>/.tekion-slides/assets/ryoko_avatar.jpeg",
  "5-1.1_オープニング_07": "/path/to/specific_image.png"
}
JSONEOF
```

キーはスライドベース名への部分一致（完全一致優先）。ユーザーがその場で画像を添付した場合は
`${SESSION_DIR}/images/` にコピーしてマップに登録する（毎回使うものなら、アセットライブラリへの
登録を提案する）。

## Phase 7: スライド画像生成

```bash
unset OPENAI_API_KEY 2>/dev/null
eval "$(${PYTHON} "${SKILL_DIR}/scripts/resolve_brand.py")"
${PYTHON} "${SKILL_DIR}/scripts/generate_slides_parallel.py" \
  --provider codex \
  --prompts-dir "${SESSION_DIR}/prompts" \
  --output-dir "${SESSION_DIR}/images" \
  --image-size 2K \
  --reference-image-map "${SESSION_DIR}/reference_image_map.json" \
  --logo "${LOGO}"
```

ハブ稼働中は `--with-dashboard` 不要（実況はハブが出す）。ハブ無しフォールバック時のみ付ける
（生成前に従来サーバが起動し、生成後はレビュー送信までブロック。内蔵ブラウザなら `--dashboard-no-open`）。

- `resolve_brand.py` がアクティブプリセットの `<slug>.config.json` から `LOGO` /
  `SLIDE_LOGO_POSITION` / `SLIDE_LOGO_SCALE`（/ `SLIDE_FOOTER_TEXT`）を解決する
  （config が無ければグローバル `assets/logo.png`・右下・0.09）。ブランド登録は `design-setup` スキル
- `--logo` は常に付与する（ユーザーが「ロゴ不要」と言ったときだけ外す）
- Phase 5 でスタイルアンカーを作った場合のみ `--style-anchor "${SESSION_DIR}/style_board.png"` を追加する
- これ1コマンドで、枚数ぶんの一斉ファンアウト（429検知で自動減速）→ 生成毎の機械検証
  → 欠損分の検証スイープ（最大2ラウンド）まで自動で走り、結果は `${SESSION_DIR}/manifest.json` に記録される
- **失敗・中断・認証エラー後は、同じコマンドをそのまま再実行する**（validated 済みはスキップされ、
  欠損分だけが生成される）。認証エラー時は先に `codex login`

| パラメータ | デフォルト | 変えるとき |
|-----------|:---------:|--------|
| `--max-parallel` | auto（枚数ぶん） | 枠残量を温存したいとき数値指定 |
| `--parallel-cap` | 自動 | 空きメモリ ÷ 250MB（codex 1本の実測）で決まる。通常変えない。`0`=無制限、正の数=固定 |
| `--sweep-rounds` / `--max-attempts` | 2 / 5 | 不安定な時間帯に増やす |
| `--force` | - | プロンプト変更なしで全再生成したいとき |
| `--per-slide-timeout` | 600 | 文字量の多いスライドで延ばす |

解像度マップ: `1K=1792x1008`, `2K=2560x1440`, `4K=3840x2160`（すべて厳密な16:9）

## Phase 8: レビュー → 差分編集

**標準の赤入れループはハブが自動処理する**（エージェントの出番なし）:
ユーザーが送信 → ハブが feedback_worker.py を起動 → 未処理キューを古い順に
edit_slide へ展開（作り直し/微修正/添付参照/全体指示） → 処理済みカーソルを進める →
開いているタブに自動反映。エージェントは生成完了を報告して応答を終えてよい。

**エージェントが Phase 8 を実行するのは次のときだけ**:
- ユーザーが「続きを」等でチャットに依頼してきた（ワーカーで扱えない依頼・失敗の引き継ぎ）
- ハブが無い環境（フォールバック。Phase 1 参照）

その場合は未処理キューから読む:

```bash
${PYTHON} "${SKILL_DIR}/scripts/review_deck.py" --session-dir "${SESSION_DIR}" --pending
# 古い順に処理し、編集・検証がすべて済んだら:
${PYTHON} "${SKILL_DIR}/scripts/review_deck.py" --session-dir "${SESSION_DIR}" --ack-feedback
```

ダッシュボード上でユーザーは修正指示のほか、スライドの並べ替え（カードの↑↓・レールのドラッグ）と
削除（🗑 = ソフトデリート、manifest の state=removed）も直接できる。並び順は manifest の
`slide_order` に保存され、表示・PPTX/PDF エクスポートに自動反映される。削除済みスライドは
再生成でも復活しない（`--force` 時のみ復活）。
各送信ペイロードの形式（`--pending` が古い順に出力する。`slide_feedback.json` は最新分のみの互換用）:

```json
{"feedback": {"02_solution_02": "指示", ...},
 "rebuild": ["05_summary_01", ...],
 "attachments": {"02_solution_02": ["/path/feedback_assets/....png"], ...},
 "global": "デッキ全体への指示（あれば）",
 "global_keep_reference": false}
```

- **デフォルトは作り直し**: 指示のあるスライドは原則 `rebuild` に入っている
  （UI のデフォルトが「前の画像を参照しない」）。`edit_slide.py --rebuild` で作り直す。
  feedback 側の同スライドの指示は互換用マーカー「【作り直し】…」の行で始まるので、
  **その行を取り除いた残り**を `--instruction` に使う。残りが無ければ引き直し
- `rebuild` に**入っていない** feedback = ユーザーが「🔗 前の画像を参照して微修正」を
  選んだもの → 通常の差分編集（指示編集）
- `attachments` = ユーザーが赤入れ欄に添付した参照画像。該当スライドの編集コマンドに
  `--reference-image <path>` を付ける（作り直し・指示編集どちらでも渡せる。複数あれば
  最も代表的な1枚を渡し、残りは指示文で内容に言及する）
- `global` = デッキ全体への一括指示。**全スライド**（個別指示があるものはその指示と連結）に
  適用する。`global_keep_reference` が false なら各スライドを `--rebuild` で、true なら
  差分編集で回す。枚数が多い場合は数枚ずつ並列に実行してよい
- ペイロードの外の依頼も2つ覚えておく: ユーザーが画像に赤で書き込んで渡してきたら
  赤ペン編集（`--annotated`）。文字構成から変えたい依頼は slides_plan.json を修正して
  Phase 4→7 を再実行（resume で該当スライドだけ生成される）

編集がすべて済んだら `--ack-feedback` でカーソルを進める（開いているタブは manifest の
変化を検知して自動更新されるので、サーバの開き直しは不要）。すべて空 = 全スライド校了。
自分でも生成画像を Read で目視し、明白な問題（文字化け・欠け）は聞かれる前に直す。
複数スライドを並列で編集してよい（manifest は flock で排他済み）。

```bash
# 指示編集: 現行スライド（raw）を参照に、指示された変更のみ適用
unset OPENAI_API_KEY 2>/dev/null
eval "$(${PYTHON} "${SKILL_DIR}/scripts/resolve_brand.py")"
${PYTHON} "${SKILL_DIR}/scripts/edit_slide.py" \
  --session-dir "${SESSION_DIR}" \
  --slide 02_solution_02 \
  --instruction "グラフの数値を 45% → 52% に修正。他は変更しない" \
  --logo "${LOGO}"

# 赤ペン編集: ユーザーが注釈を書き込んだ画像を渡す（注釈画像のみを参照にする）
${PYTHON} "${SKILL_DIR}/scripts/edit_slide.py" \
  --session-dir "${SESSION_DIR}" \
  --slide 02_solution_02 \
  --annotated /path/to/annotated.png \
  --instruction "この領域を簡素化"

# 作り直し: 前の画像を参照せず、元の生成プロンプト（+指示）から再生成
# （キャラクター等を出すなら --reference-image でアセットを渡せる）
${PYTHON} "${SKILL_DIR}/scripts/edit_slide.py" \
  --session-dir "${SESSION_DIR}" \
  --slide 05_summary_01 --rebuild \
  --instruction "写真中心のレイアウトをやめて、図解中心の構成に" \
  --logo "${LOGO}"

# ロールバック: 編集で悪化したら1つ前の版に戻す
${PYTHON} "${SKILL_DIR}/scripts/edit_slide.py" \
  --session-dir "${SESSION_DIR}" --slide 02_solution_02 --rollback
```

編集は `_v2.png`, `_v3.png` とバージョン保存され、manifest の確定版（current_image）が切り替わる。
export は常に確定版を使うため、ロールバックも次の export から反映される。

## Phase 9: PPTX/PDF生成（manifest 駆動）

```bash
${PYTHON} "${SKILL_DIR}/scripts/export_to_pptx.py" \
  --input-dir "${SESSION_DIR}/images" \
  --manifest "${SESSION_DIR}/manifest.json" \
  --output "${OUTPUT_DIR}/${OUTPUT_NAME}.pptx" && \
${PYTHON} "${SKILL_DIR}/scripts/export_to_pdf.py" \
  --input-dir "${SESSION_DIR}/images" \
  --manifest "${SESSION_DIR}/manifest.json" \
  --output "${OUTPUT_DIR}/${OUTPUT_NAME}.pdf"
```

検証済み確定版のみが書き出される。「未完成スライドあり」で止まったら Phase 7 のコマンドを
再実行して欠損分を埋めてから export し直す（意図的に部分出力するときだけ `--allow-partial`）。

---

## 過去セッションの検索と再開

全セッションは `~/.tekion-slides/sessions.db` に自動記録される（manifest 保存のたびに登録）。
ユーザーが「昨日のデッキ」「シンデレラのスライド」「作業中だったやつ」を求めたら:

```bash
${PYTHON} "${SKILL_DIR}/scripts/session_registry.py" --list --query "シンデレラ"   # 名前で検索
${PYTHON} "${SKILL_DIR}/scripts/session_registry.py" --list --days 7               # 日数で絞り込み
# 台帳の path を --register に渡すと固定ハブ URL 用 SID が得られる
${PYTHON} "${SKILL_DIR}/scripts/session_registry.py" --register "<path>"
```

スタート画面にも「RECENT SESSIONS」として直近8件が表示され、「開く」でその場で再開できる。
台帳に無い古いセッションは `--scan <ルート>` で一括取り込みできる。

## 既存デッキの改修（PPTX / PDF / 画像の取り込み）

既存デッキを取り込んで、生成デッキと同じように赤入れ → 差分編集 → export で改修できる。
入り口は2つ: **スライドダッシュボードへのドラッグ&ドロップ / 「＋ 読み込み」ボタン**（推奨）、または CLI:

```bash
${PYTHON} "${SKILL_DIR}/scripts/import_deck.py" \
  --session-dir "${SESSION_DIR}" --file /path/to/existing_deck.pptx
```

取り込みの仕組み:
- **.pptx（画像ベース）**: 各スライドから最大面積の画像を抽出
- **.pptx（図形・テキストで組まれたネイティブデッキ）**: LibreOffice(soffice) があれば
  自動で PDF 経由でページ全体を図化して取り込む（無ければ PowerPoint から PDF 書き出しを案内）
- **.pdf**: 各ページを 2K 相当で画像レンダリング（pymupdf）
- **.png / .jpg**: 1ファイル = 1スライド
- 取り込みスライドの差分編集は焼き込み済み画像を参照して行われる（raw なし → ロゴ・フッターの再合成はスキップ）

## テスト・デバッグ（mock プロバイダ）

`--provider mock` で、API・サブスク枠を一切消費せずパイプライン全体を検証できる
（プレースホルダPNGを即時生成）。スイープ・resume の動作確認には障害注入を使う:

```bash
TEKION_MOCK_FAIL_SLIDES="02_solution" TEKION_MOCK_FAIL_TIMES=1 \
${PYTHON} "${SKILL_DIR}/scripts/generate_slides_parallel.py" \
  --provider mock --prompts-dir "${SESSION_DIR}/prompts" --output-dir "${SESSION_DIR}/images"
```

## 参照ドキュメント

| ファイル | 内容 |
|----------|------|
| [references/design_guidelines_template.md](references/design_guidelines_template.md) | デザインガイドライン（Brand Design System 含む） |
| [references/presets/](references/presets/) | プリセット集 |

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `token_revoked` / 認証エラーで全体停止 | `codex login` → 同じコマンド再実行（resume が効く） |
| 一部スライドが「総試行上限で打ち切り」 | エラー内容を確認。プロンプト起因（安全性ブロック等）なら slides_plan.json を修正して再実行 |
| export が「未完成スライドあり」で止まる | Phase 7 のコマンドを再実行（欠損分のみ生成される）。急ぎなら `--allow-partial` |
| 編集結果が気に入らない | `edit_slide.py --rollback` で前の版へ |
| デッキ全体の色味がバラつく | Phase 2 の Brand Design System を具体化し、Phase 5 のスタイルアンカーを使う |
| スタイルボード生成がタイムアウト | 文字要素を「Aa」1箇所だけに減らす。図形と配色だけでアンカーの役割は果たせる |

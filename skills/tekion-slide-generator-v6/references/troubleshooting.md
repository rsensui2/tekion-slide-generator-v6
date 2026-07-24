# トラブルシューティング

| 症状 | 原因・対処 |
|------|-----------|
| APIキーエラー | `.env.local` に `GEMINI_API_KEY=...` を設定 |
| 表紙が末尾に来る | source_fileを `"00_cover"` に変更 |
| ファイル名重複 | source_fileをユニーク化。同一source_fileは自動連番 |
| Gemini APIタイムアウト | `--max-retries 3` で自動リトライ（120秒タイムアウト） |
| design_guidelines読み込みエラー | `--design-guidelines` のパスを確認 |
| パッケージ不足 | `bash ${SKILL_DIR}/scripts/setup.sh` を実行 |
| SubAgent not found | `~/.claude/agents/nanobanana-prompt-generator-subagent.md` を確認 |
| グラウンディングメタデータ | `${SESSION_DIR}/grounding/` に自動保存される |

Gemini API公式ドキュメント: https://ai.google.dev/gemini-api/docs/image-generation?hl=ja

# 受講生への配布用プロンプト

下のコードブロックの中身を丸ごとコピーして、Claude Code / Codex / Cursor などの
エージェントのチャットに貼り付けてもらう。エージェントが自分でインストールから
動作確認・使い方の案内までやる。

```text
「TEKION Slide Generator v6」というスライド生成プラグインを、この環境にセットアップしてください。

## 手順

1. まず自分がどのエージェント環境で動いているか判定してください（Claude Code / Codex / その他）。

2. シェルで自分の環境に合うインストールコマンドを実行してください:

   Claude Code の場合:
     claude plugin marketplace add rsensui2/tekion-slide-generator-v6
     claude plugin install tekion-slide-generator@tekion-slide-generator

   Codex の場合:
     codex plugin marketplace add rsensui2/tekion-slide-generator-v6
     codex plugin add tekion-slide-generator@tekion-slide-generator

   - 「already exists」系のエラーが出たら、先に `... plugin marketplace remove tekion-slide-generator` してから add をやり直す（= 最新版への更新）
   - `claude plugin` CLI が使えない古い Claude Code の場合は、ユーザーに次の2つのスラッシュコマンドを打ってもらうよう案内する:
     /plugin marketplace add rsensui2/tekion-slide-generator-v6
     /plugin install tekion-slide-generator@tekion-slide-generator

3. 常駐ダッシュボード（TEKION Slides Hub）を登録してください（macOSのみ・初回1回）:
     インストールしたプラグインのスキルフォルダにある scripts/install_hub.sh を実行する。
     例: bash ~/.codex/plugins/cache/tekion-slide-generator/*/*/skills/tekion-slide-generator-v6/scripts/install_hub.sh
     成功すると http://127.0.0.1:7799/ が常駐し、修正指示の自動処理・タブ自動更新が有効になる。
     （Linux/WSL ではスキップし、フォールバック動作になる旨を伝える）

4. Python の依存を確認してください:
     python3 -c "import PIL, pptx" が失敗する場合は pip3 install pillow python-pptx

5. インストール結果を確認して報告してください（plugin list に tekion-slide-generator が表示されること）。

6. 最後に、ユーザーへ以下を案内してください:
   - スキルを有効にするため、新しいセッション（チャット）を開き直すこと
   - ダッシュボードは http://127.0.0.1:7799/ をブックマークすれば常に開ける
   - 使い方は3つの言葉だけ:
     ・「◯◯のスライドを作って」 → 生成が始まり、ブラウザに「スライドダッシュボード」が開く。
        赤い記入欄に修正指示を書いて ⏎ するとAIが直す。右上から PPTX/PDF をダウンロード
     ・「デザインを設定したい」 → ロゴやパワポのマスターを渡すだけで自社デザインを登録。
        以後のスライドに自動適用される
     ・「キャラクターを登録したい」 → よく使うキャラクター・写真を登録すると、
        「◯◯を出して」と言うだけでスライドに登場させられる
   - 画像生成は Codex 内蔵の gpt-image-2（ChatGPT/Codex のサブスク枠）を使うため、
     別途の APIキーや従量課金は不要（Codex にログインしていることが前提）
```

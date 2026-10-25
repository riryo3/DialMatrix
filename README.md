# DialMatrix

> **書き込み安全対策を適用したソース版です。** 修正内容・手動復旧手順は [SAFETY_FIXES.md](SAFETY_FIXES.md)、検証結果は [TEST_RESULTS.md](TEST_RESULTS.md) を参照してください。旧ビルド済みアプリ・依存環境・個人バックアップは含みません。実機（Rev-O-mate）では、読み込み、ドライラン、全体バックアップ、LED の書き込みと元に戻す操作を確認しました。複数セクタの書き込み、通信が途中で切れたときの挙動、再ビルドしたアプリ・WebKit での表示は、未確認です。

Bit Trade One の「Rev-O-mate」（ダイヤル付きの11ボタンのキーパッド）を、Mac と Windows から設定するための**非公式**ツールです。
本体の中の設定（ボタンとダイヤルの動き、LED の色）を読み込み、画面で編集して、本体へ書き込めます。

> **非公式です。** Bit Trade One とは関係がなく、公式のサポートはありません。
> 「Rev-O-mate」は Bit Trade One の商標です。DialMatrix は、公式の製品ではありません。

## できること
- 本体の設定を読み込む（読むだけで、本体は変わりません）
- ボタン1〜11、ダイヤルの回転・押し下げ回転の動きを、画面で編集する（キーボード入力、マウス操作、メディアキーなど）
- 書き込む前に、変更の一覧を確認する（ドライラン）
- 書き込むときは、先に本体の全体を自動でバックアップする
- 底面 LED の色を、パレットで選ぶ（本体にもその場で映ります）
- 設定を `.bin` で保存・読み込みする
- 本体の向き（ケーブル奥／手前）の表示切り替え
- 画面の言語の切り替え（日本語／English。上の帯の言語ボックス。初期値は Mac の言語に合わせます）

## できないこと
- **マクロ（複数の動作をつなぐ設定）の作成・編集には、対応していません。** ボタンに設定できるのは、1つの動作（キー入力、マウス操作、メディアキー）です。
- 本体にマクロが入っているボタンは、画面では「本体のマクロ」と表示されます。**そのボタンを選び直して設定すると、マクロは上書きされます。** 公式アプリでマクロを作っている場合は、書き込む前の変更の一覧を、よく確認してください（バックアップは、自動で取られます）。

## インストール
1. [Releases](../../releases) から、`DialMatrix.app` の zip をダウンロードして、解凍する。
2. `DialMatrix.app` を、アプリケーションフォルダなど、好きな場所に置く。
3. **初めて開くときは、右クリック →「開く」** を選ぶ（署名・公証をしていないため、ふつうに開くと、警告が出ます）。
4. 本体を USB でつないで、「実機読込」を押す。

動作の環境: macOS（Apple Silicon）。Python などの追加のソフトは、要りません。

### Windows 版
1. [Releases](../../releases) から、`DialMatrix-Windows.zip` をダウンロードして、解凍する（OneDrive の外がおすすめです）。
2. `DialMatrix` フォルダの中の `DialMatrix.exe` を起動する。**フォルダごと使ってください**（`DialMatrix.exe` だけを別の場所に移すと動きません）。
3. **初めて起動するときは、SmartScreen の警告が出ることがあります**（署名をしていないため）。「詳細情報」→「実行」を選ぶ。
4. 本体を USB でつないで、「実機読込」を押す。

動作の環境: Windows 11（64ビット）。画面の表示に WebView2 ランタイムを使います（Windows 11 には、最初から入っています）。Python などの追加のソフトは、要りません。
Windows 版で確認したのは、**1台の PC と1つの本体**での、実機の読み込み、LED の表示、全体バックアップ付きの書き込み、`.bin` からの復元、書き込んだ設定が抜き差しのあとも残ること、Python のない環境（Windows サンドボックス）での起動です。Windows 10、複数セクタの書き込み、完了音（スピーカーのない環境で試しました）は、未確認です。
ソースから動かすときは、Python 3 に `pip install hidapi pywebview` を入れて、`python revomate_app.py --window` を実行します。`.exe` を作るには、`build_app/build_win.ps1` を使います（`pip install pyinstaller pillow` も必要です）。

## 使う前に（必ず読んでください）
- **自己責任でお使いください。** 本体への書き込みを誤ると、設定が壊れることがあります。作者は、その損害に責任を負いません。
- 書き込む前に、アプリが本体全体（2 MiB）のバックアップを `~/Documents/DialMatrix バックアップ/`（Windows では「ドキュメント」フォルダの `DialMatrix バックアップ`）に保存し、読み戻し照合します（ソース実行時はプロジェクトフォルダ）。保存・照合に失敗した場合は消去・書き込みを始めません。**書き込む前のバックアップが、元に戻す手段です。消さないでください。**
- Bit Trade One が公開している「工場出荷の設定」からも、本体を元に戻せます（[公式のリポジトリ](https://github.com/bit-trade-one/BFROM11BK_Rev-O-mate)の `presetdata/factory_preset.mbf`）。
- 書き込み途中のエラーで自動的に元へ戻る保証はありません。バックアップ名・照合済みセクタ・失敗セクタを確認し、[復旧手順](SAFETY_FIXES.md#書き込みの途中で失敗した場合)を参照してください。
- 同時に、公式のアプリや、ほかの設定ツールを動かさないでください（本体への読み書きがぶつかります）。
- このアプリは、インターネットには接続しません。画面は、自分のパソコンの中だけで動く小さなサーバー（`127.0.0.1`）で表示します。

## 公式との関係
本体との通信の方法（コマンド、設定が本体の中にどう保存されるか）は、Bit Trade One が**公開しているオープンソース**（[bit-trade-one/BFROM11BK_Rev-O-mate](https://github.com/bit-trade-one/BFROM11BK_Rev-O-mate)）を読んで、作りました。
このオープンソースは、Assembly Desk License（MIT ライセンスをもとにしたもの）で公開されていて、改変・再配布・商用利用が許されています。条件は、著作権表示とライセンス全文を載せることです。全文は `licenses/` と、アプリの中の「ライセンス表示」にあります。
公式のアプリの実行ファイルを解析（リバースエンジニアリング）したものではありません。

## 作り方
開発には、AI（Claude と Gemini）の助けを借りています。動作の確認（実機での読み込み・LED・バックアップなど）と、公開の判断は、作者が行っています。

## ライセンス
- DialMatrix のコード: [MIT License](LICENSE)
- 使っているもの（Bit Trade One の Assembly Desk License、pywebview など）: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

---

## English (short)
**DialMatrix** is an **unofficial** Mac and Windows tool for configuring Bit Trade One's *Rev-O-mate* dial keypad: read the device's settings, edit buttons / dial actions / LED colour, and write them back (with an automatic full backup first).
The screen can be switched between Japanese and English (language box in the top bar; it follows the Mac's language by default). Macros (several chained actions) cannot be created or edited; a button that holds one is shown as "device macro" and is overwritten if you set it again. Built with the help of AI assistants (Claude and Gemini); the author checks it on a real device and decides what is released. Not affiliated with Bit Trade One. "Rev-O-mate" is a trademark of Bit Trade One. Use at your own risk.
The device protocol was learned from Bit Trade One's public source ([BFROM11BK_Rev-O-mate](https://github.com/bit-trade-one/BFROM11BK_Rev-O-mate), Assembly Desk License, MIT-based); no binary was reverse-engineered.
The app is not signed or notarized yet: on first launch, right-click → Open.
**Windows:** download `DialMatrix-Windows.zip` from Releases, unzip it, and run `DialMatrix\DialMatrix.exe` (keep the whole folder together; on first launch SmartScreen may warn because the app is unsigned: "More info" → "Run anyway"). It needs no Python; the WebView2 runtime (built into Windows 11) shows the window. Verified on Windows 11 with one PC and one device only (read, LED preview, backup + write, restore from `.bin`, start without Python); Windows 10 is untested. Code: MIT (see `LICENSE`); third-party notices: `THIRD_PARTY_NOTICES.md`.

// The page's Japanese / English switch: run the i18n block from the page against a minimal fake browser.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "revomate_studio.html"), "utf8");
const block = html.match(/<script>\n(\/\* ===== i18n[\s\S]*?)<\/script>/)[1];

function load(lang) {
  const store = { dm_lang: lang };
  const sandbox = {
    localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
    navigator: { language: "ja-JP" },
    location: { reload() {} },
    MutationObserver: class { observe() {} },
    document: { documentElement: { lang: "ja", nodeType: 1, nodeName: "HTML", getAttribute: () => null, firstChild: null },
      readyState: "complete", addEventListener() {}, title: "DialMatrix", getElementById: () => null },
    alert() {}, confirm() {}, prompt() {},
  };
  sandbox.window = sandbox;
  vm.runInNewContext(block, sandbox);
  return sandbox;
}

test("Japanese mode leaves every text alone", () => {
  const w = load("ja");
  assert.strictEqual(w.tr("実機読込"), "実機読込");
  assert.strictEqual(w.tr("モード1 ボタン3: スクリプト151"), "モード1 ボタン3: スクリプト151");
});

test("English mode translates the toolbar and the write confirmation", () => {
  const w = load("en");
  assert.strictEqual(w.tr("実機読込"), "Read device");
  assert.strictEqual(w.tr("変更確認・実機へ書き込む"), "Review & write to device");
  assert.strictEqual(w.tr("プレビューした変更を実機へ書き込みますか？"), "Write the previewed changes to the device?");
  assert.strictEqual(w.tr("実機へ書き込んでいます…"), "Writing to the device…");
});

test("messages from the server are translated, with their numbers kept", () => {
  const w = load("en");
  assert.strictEqual(w.tr("モード1 待機 LED: 00 00 54 → 00 00 00"), "Mode 1 idle LED: 00 00 54 → 00 00 00");
  assert.strictEqual(w.tr("モード2 ボタン7: 本体のマクロ（No.155）を上書きします。元のマクロは全体バックアップに保存されます。"),
    "Mode 2 button 7: overwrites the device macro (No.155). The original macro is kept in the full backup.");
  assert.strictEqual(w.tr("セクタ 2 の書き込み後の照合が一致しません。"), "Verification after writing sector 2 failed.");
  assert.strictEqual(w.tr("書き込み＆ベリファイ完了。適用ボタン: 2件 / すでに本体と一致: 1件 / スキップ: 0件"),
    "Write and verify complete. Applied buttons: 2 / already matched the device: 1 / skipped: 0");
});

test("multi-line messages are translated line by line", () => {
  const w = load("en");
  const out = w.tr("書き込みに失敗しました: 不明なエラー\nバックアップ: a.bin\n失敗セクター: 1");
  assert.strictEqual(out, "Writing failed: unknown error\nBackup: a.bin\nFailed sector: 1");
});

test("the recovery wording of a failed write stays in English", () => {
  const w = load("en");
  assert.strictEqual(w.tr("自動ロールバックは行われていません。画面の編集状態は保持されています。"),
    "No automatic rollback was done. Your edits on screen are kept.");
});

test("spaces before dots are kept (Save .bin / Open .bin)", () => {
  const w = load("en");
  assert.strictEqual(w.tr(".bin保存"), "Save .bin");
  assert.strictEqual(w.tr(".binを開く"), "Open .bin");
});

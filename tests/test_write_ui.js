const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(require('node:path').join(__dirname, '..', 'revomate_studio.html'), 'utf8');
const start = html.indexOf('// --- Dry-Run & Safe Flash Write ---');
const end = html.indexOf('const STORAGE_KEY_ZOOM', start);
assert.ok(start >= 0 && end > start, 'dry-run test extraction bounds exist');
const source = html.slice(start, end);

function harness(fetchImpl) {
  const nodes = {
    dryRunModal: { classList: { hidden: true, add() { this.hidden = true; }, remove() { this.hidden = false; }, contains(x) { return x === 'hidden' ? this.hidden : false; } } },
    dryRunConfirmButton: { disabled: true }, dryRunDiffText: { textContent: '' },
  };
  const alerts = [], requests = [];
  const configState = { modes: [{ switches: [{ sw: 1, role: 'key', key: 'A', scriptDirty: true }, { sw: 2, role: 'dial', scriptDirty: true }] }] };
  const ctx = { configState, document: { getElementById: id => nodes[id] }, fetch: async (...args) => { requests.push(args); return fetchImpl(...args); },
    alert: s => alerts.push(String(s)), confirm: () => true, askDoneSoundOnce: async () => {}, showBusy() {}, hideBusy: async () => {},
    playDoneSound() {}, saveStateToStorage() {}, JSON, Number, Error, console };
  vm.createContext(ctx); vm.runInContext(source, ctx);
  return { ctx, nodes, alerts, requests, configState };
}
const response = (body, ok = true) => ({ ok, json: async () => body });
const preview = overrides => response({ ok: true, dry_run: true, plan_token: 'token', changes: ['change'], eligible_buttons: [], has_changes: true, ...overrides });

test('mixed applied/skipped success labels SW-less dial skips and clears only exact applied button state', async () => {
  const h = harness(async (_url, opts) => opts.body.includes('dry_run') ? preview({ skipped: [{ mode: 0, field: 'dial', label: 'Dial', reason: 'unsupported' }] }) : response({ ok: true, backups: ['backup.bin'], applied_buttons: [{ mode: 0, sw: 1, label: 'Key' }], skipped: [{ mode: 0, field: 'dial', label: 'Dial' }], applied: true, has_changes: true }));
  await h.ctx.openDryRunModal();
  assert.match(h.nodes.dryRunDiffText.textContent, /Dial: unsupported/);
  assert.doesNotMatch(h.nodes.dryRunDiffText.textContent, /SWundefined/);
  await h.ctx.confirmWriteToDevice();
  assert.match(h.alerts[0], /適用ボタン: 1件/);
  assert.equal(h.configState.modes[0].switches[0].scriptDirty, undefined);
  assert.equal(h.configState.modes[0].switches[1].scriptDirty, true);
});

test('preview failure disables confirmation', async () => {
  const h = harness(async () => response({ ok: false, error: 'offline' }, false));
  await h.ctx.openDryRunModal();
  assert.equal(h.nodes.dryRunConfirmButton.disabled, true);
});

test('edited config after preview does not write', async () => {
  const h = harness(async (_url, opts) => preview({}));
  await h.ctx.openDryRunModal();
  h.configState.modes[0].switches[0].key = 'B';
  await h.ctx.confirmWriteToDevice();
  assert.equal(h.requests.length, 1);
  assert.match(h.alerts[0], /変更されています/);
});

test('obsolete async preview response is ignored', async () => {
  let resolve;
  const h = harness(() => new Promise(r => { resolve = r; }));
  const pending = h.ctx.openDryRunModal();
  h.ctx.closeDryRunModal();
  resolve(preview({})); await pending;
  assert.equal(h.nodes.dryRunConfirmButton.disabled, true);
  assert.equal(h.nodes.dryRunConfirmButton.disabled, true);
});

test('no-op preview does not write or claim verify/replug', async () => {
  const h = harness(async (_url, opts) => preview({ has_changes: false, changes: [], sectors: [] }));
  await h.ctx.openDryRunModal(); await h.ctx.confirmWriteToDevice();
  assert.equal(h.requests.length, 1);
  assert.match(h.alerts[0], /書き込み・ベリファイは行いません/);
  assert.doesNotMatch(h.alerts[0], /完了|USBを抜き差し/);
});

test('failure recovery details remain visible and dirty edits stay intact', async () => {
  const h = harness(async (_url, opts) => opts.body.includes('dry_run') ? preview({}) : response({ ok: false, error: 'verify failed', backups: ['backup.bin'], completed_sectors: [1], failed_sector: 2, recovery_required: true, notes: ['inspect device'] }, false));
  await h.ctx.openDryRunModal(); await h.ctx.confirmWriteToDevice();
  const alert = h.alerts[0];
  for (const text of ['backup.bin', '1', '2', '復旧対応が必要: はい', '自動ロールバックは行われていません', 'inspect device']) assert.ok(alert.includes(text), text);
  assert.equal(h.configState.modes[0].switches[0].scriptDirty, true);
});

test('config changed while awaiting sound unlock prevents write; plan consumed on real request failure', async () => {
  let releaseSound;
  const h = harness(async (_url, opts) => opts.body.includes('dry_run') ? preview({}) : response({ ok: false, error: 'network' }, false));
  h.ctx.askDoneSoundOnce = () => new Promise(r => { releaseSound = r; });
  await h.ctx.openDryRunModal();
  const confirming = h.ctx.confirmWriteToDevice();
  h.configState.modes[0].switches[0].key = 'C'; releaseSound(); await confirming;
  assert.equal(h.requests.length, 1);
  assert.equal(h.nodes.dryRunConfirmButton.disabled, true);
});

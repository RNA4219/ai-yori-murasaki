// packaged ai_yori_murasaki/web/decision.html の decision-core を取り出して試す。
// 作成: Claude (Opus 5.5) / 2026-11-16。実行: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const html = readFileSync(join(root, '..', 'ai_yori_murasaki', 'web', 'decision.html'), 'utf8');
const m = /<script id="decision-core">([\s\S]*?)<\/script>/.exec(html);
assert.ok(m, 'decision-core の script がない');
const sandbox = { URL };
sandbox.globalThis = sandbox;
vm.runInNewContext(m[1], sandbox);
const D = sandbox.AYMDecision;
const R = D.DEFAULT_RULES;
// vm の中の配列は原型が違うので、比べる前に普通の配列にする
const plain = (x) => JSON.parse(JSON.stringify(x));

function item(over) {
  return Object.assign({ key: 'k', ref: null, title: 't', severity: 'medium', state: 'open', state_label: '未対応',
    due: null, link: null, cve_count: 0, kev: false, kev_cves: [], epss_max: null }, over);
}
function view(items, extra) {
  return Object.assign({ schema: 'aym.decision-view/1', generated_at: '2026-11-16T03:00:00Z', campaign: { id: 'c', name: 'n', created_at: null },
    previous: null, freshness: {}, rules: {}, items, removed: [] }, extra);
}

test('期限の日数を数える', () => {
  const a = D.assess(item({ due: '2026-11-06', severity: 'critical' }), '2026-11-16', R);
  assert.equal(a.overdueDays, 10);
  assert.equal(a.dueInDays, null);
  assert.equal(a.decideNow, true);
  assert.ok(a.reasons.some((r) => r.code === 'overdue' && r.tone === 'crit'));
  const b = D.assess(item({ due: '2026-11-20' }), '2026-11-16', R);
  assert.equal(b.dueInDays, 4);
  assert.ok(b.reasons.some((r) => r.code === 'due_soon'));
  assert.equal(b.decideNow, false);
});

test('終わったものは急がない', () => {
  const a = D.assess(item({ state: 'done', kev: true, due: '2026-01-01', severity: 'critical', epss_max: 0.9 }), '2026-11-16', R);
  assert.equal(a.active, false);
  assert.equal(a.decideNow, false);
  assert.equal(a.score, 0);
  assert.equal(a.reasons.length, 0);
});

test('期限切れでも中・低は急ぐものに入らない', () => {
  const a = D.assess(item({ due: '2026-11-01', severity: 'medium' }), '2026-11-16', R);
  assert.equal(a.overdueDays, 15);
  assert.equal(a.tier, null);
  assert.equal(a.decideNow, false);
});

test('段を分ける: 悪用の確認と判断待ちは「いま」、期限切れの重大・高と見込みの高いものは「早めに」', () => {
  assert.equal(D.assess(item({ kev: true, severity: 'low' }), '2026-11-16', R).tier, 'now');
  assert.equal(D.assess(item({ state: 'waiting' }), '2026-11-16', R).tier, 'now');
  assert.equal(D.assess(item({ kev: true, due: '2026-10-01', severity: 'critical' }), '2026-11-16', R).tier, 'now');
  assert.equal(D.assess(item({ due: '2026-11-01', severity: 'high' }), '2026-11-16', R).tier, 'soon');
  assert.equal(D.assess(item({ epss_max: 0.5 }), '2026-11-16', R).tier, 'soon');
  assert.equal(D.assess(item({ state: 'done', kev: true }), '2026-11-16', R).tier, null);
  const s = D.summarize(view([item({ key: 'a', kev: true }), item({ key: 'b', due: '2026-11-01', severity: 'critical' }), item({ key: 'c' }), item({ key: 'd', state: 'wont_fix' })]), '2026-11-16');
  assert.deepEqual(plain(s.now.map((r) => r.item.key)), ['a']);
  assert.deepEqual(plain(s.soon.map((r) => r.item.key)), ['b']);
  assert.equal(s.counts.now, 1);
  assert.equal(s.counts.soon, 1);
  assert.equal(s.counts.closed, 1);
  assert.equal(s.stateCounts.open, 3);
  assert.equal(s.stateCounts.wont_fix, 1);
});

test('日付を短く出す', () => {
  assert.equal(D.shortDate('2026-11-06'), '11/6');
  assert.equal(D.shortDate('2026-01-20T00:00:00Z'), '1/20');
  assert.equal(D.shortDate(null), '');
});

test('悪用の確認・判断待ち・悪用の見込みで入る', () => {
  assert.equal(D.assess(item({ kev: true, severity: 'low' }), '2026-11-16', R).decideNow, true);
  assert.equal(D.assess(item({ state: 'waiting', severity: 'info' }), '2026-11-16', R).decideNow, true);
  assert.equal(D.assess(item({ epss_max: 0.1 }), '2026-11-16', R).decideNow, true);
  assert.equal(D.assess(item({ epss_max: 0.099 }), '2026-11-16', R).decideNow, false);
});

test('並び順は 悪用の確認 → 判断待ち → 期限 → 重大度', () => {
  const v = view([
    item({ key: 'a', title: 'a', severity: 'critical', due: '2026-10-01' }),
    item({ key: 'b', title: 'b', state: 'waiting', severity: 'low' }),
    item({ key: 'c', title: 'c', kev: true, severity: 'medium' }),
    item({ key: 'd', title: 'd', severity: 'high', due: '2026-11-10' }),
  ]);
  const s = D.summarize(v, '2026-11-16');
  assert.deepEqual(plain(s.decide.map((r) => r.item.key)), ['c', 'b', 'a', 'd']);
  assert.equal(s.counts.kev, 1);
  assert.equal(s.counts.waiting, 1);
  assert.equal(s.counts.overdueSevere, 2);
});

test('前回からの変化を分ける', () => {
  const v = view([
    item({ key: 'a', state: 'done', state_label: '修正済', change: 'state_changed', prev_state: 'open', prev_state_label: '未対応' }),
    item({ key: 'b', state: 'waiting', change: 'state_changed', prev_state: 'done' }),
    item({ key: 'c', change: 'new' }),
    item({ key: 'd', change: 'unchanged' }),
  ], { previous: { id: 'p', created_at: '2026-11-13T00:00:00Z' }, removed: [{ key: 'z', title: 'z', prev_state: 'open' }] });
  const s = D.summarize(v, '2026-11-16');
  assert.deepEqual(plain(s.changes.closed.map((r) => r.item.key)), ['a']);
  assert.deepEqual(plain(s.changes.stateChanged.map((r) => r.item.key)), ['b']);
  assert.deepEqual(plain(s.changes.added.map((r) => r.item.key)), ['c']);
  assert.equal(s.changes.removed.length, 1);
  assert.equal(s.counts.changed, 4);
  assert.equal(s.hasPrevious, true);
});

test('内訳の表は状態と重大度で数える', () => {
  const s = D.summarize(view([item({ severity: 'critical' }), item({ severity: 'critical' }), item({ state: 'done', severity: 'low' }), item({ state: 'weird', severity: 'nope' })]), '2026-11-16');
  assert.equal(s.matrix.open.critical, 2);
  assert.equal(s.matrix.done.low, 1);
  assert.equal(s.matrix.other.info, 1);
});

test('形の違うデータは読まない', () => {
  assert.throws(() => D.validate({ schema: 'other', items: [] }));
  assert.throws(() => D.validate({ schema: 'aym.decision-view/1' }));
  assert.throws(() => D.validate(null));
});

test('リンクは http(s) だけ、認証情報つきは出さない', () => {
  assert.equal(D.safeLink('https://example.invalid/a'), 'https://example.invalid/a');
  assert.equal(D.safeLink('javascript:alert(1)'), null);
  assert.equal(D.safeLink('https://u:p@example.invalid/'), null);
  assert.equal(D.safeLink(null), null);
});

test('Obsidian のノート専用リンクと通常のリンクを区別する', () => {
  const uri = 'obsidian://open?vault=Demo&file=Review%2FNotes';
  assert.equal(D.obsidianNoteLink({source: 'obsidian', source_uri: uri}), uri);
  const dotNote = 'obsidian://open?vault=Demo&file=Review%2F.obsidian';
  assert.equal(D.obsidianNoteLink({source: 'obsidian', source_uri: dotNote}), dotNote);
  assert.equal(D.safeLink(uri), null);
  assert.equal(D.obsidianNoteLink({source: 'notion', source_uri: uri}), null);
  for (const candidate of [
    'https://example.invalid', 'obsidian://open?vault=Demo&file=Notes&extra=1',
    'obsidian://open?vault=Demo&file=..%2FNotes', 'obsidian://open?vault=Demo&file=%2FNotes',
    'obsidian://open?vault=Demo&file=.Obsidian%2Fnotes'
  ]) {
    assert.equal(D.obsidianNoteLink({source: 'obsidian', source_uri: candidate}), null);
  }
});

test('rules はデータの値を優先する', () => {
  const r = D.rulesOf({ rules: { top_n: 3, epss_high: 'x' } });
  assert.equal(r.top_n, 3);
  assert.equal(r.epss_high, 0.1);
});

test('見本のデータが読める', () => {
  const sample = JSON.parse(readFileSync(join(root, 'samples', 'decision-sample.json'), 'utf8'));
  const s = D.summarize(sample, '2026-11-16');
  assert.equal(s.counts.total, sample.items.length);
  assert.ok(s.decide.length > 0);
  assert.equal(s.changes.removed.length, 1);
});

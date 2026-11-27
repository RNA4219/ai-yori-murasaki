import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base = process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';
async function get(path) {
  const response = await fetch(base + path);
  assert.equal(response.status, 200);
  return response.json();
}

const {campaigns} = await get('/api/campaigns');
const campaign = campaigns[0].campaign_id;
const {runs} = await get('/api/campaigns/' + campaign + '/runs');
let historyRun, emptyRun, checkpoint;
for (const run of runs) {
  const first = await get('/api/verification/' + run.run_id + '?limit=25');
  if (first.journal_status === 'empty') emptyRun = run.run_id;
  if (first.next_before != null) {
    historyRun = run.run_id;
    checkpoint = first.verification[0];
  }
}
assert.ok(historyRun && emptyRun, '大きい履歴と空の履歴を区別できる');
assert.equal(checkpoint.method, 'checkpoint');
assert.match(checkpoint.target_last_event_hash, /^[0-9a-f]{64}$/);
assert.match(checkpoint.checkpoint_digest, /^[0-9a-f]{64}$/);

const browser = await chromium.launch({channel: process.env.AYM_BROWSER_CHANNEL || undefined});
try {
  const {sources} = await get('/api/sources');
  const doc = sources.find(source => source.title === '画面試験の参考資料');
  assert.ok(doc);
  for (const blue of [false, true]) {
    const page = await browser.newPage();
    const root = base + (blue ? '/?view=blue' : '/');
    function responseAt(path) {
      return page.waitForResponse(response => new URL(response.url()).pathname === path)
        .then(response => {
          assert.equal(response.status(), 200);
          assert.deepEqual(new URL(response.url()).searchParams.getAll('view'), blue ? ['blue'] : []);
          return response.json();
        });
    }
    const list = responseAt('/api/sources');
    await page.goto(root + '#/sources');
    const listBody = await list;
    const sourcePath = '/api/sources/' + doc.document_id;
    const detail = responseAt(sourcePath);
    const withheld = page.getByText('Blueビューでは資料は非公開です。', {exact: true});
    if (blue) {
      assert.deepEqual(Object.keys(listBody).sort(), ['reason', 'status']);
      assert.equal(listBody.status, 'withheld');
      await withheld.waitFor();
      assert.equal(await page.getByText(doc.title, {exact: true}).count(), 0);
      await page.goto(root + '#/sources/' + doc.document_id);
      assert.deepEqual(await detail, listBody);
      await withheld.waitFor();
      assert.equal(await page.locator('.source-body').count(), 0);
      const detailRoute = '**' + sourcePath + '?*';
      await page.route(detailRoute, route => route.fulfill({status: 503, json: {error: 'unavailable'}}));
      await page.locator('#refresh-btn').click();
      await page.getByText('資料を取得できません。手動更新で再取得してください。', {exact: true}).waitFor();
      assert.equal(await withheld.count(), 0);
      await page.unroute(detailRoute);
      await page.locator('#refresh-btn').click();
      await withheld.waitFor();
    } else {
      assert.ok(listBody.sources.some(source => source.document_id === doc.document_id));
      await page.getByRole('link', {name: doc.title, exact: true}).click();
      assert.equal((await detail).source.body, '合成資料の本文');
      await page.getByText('合成資料の本文', {exact: true}).waitFor();
    }
    const records = responseAt('/api/runs/' + historyRun + '/events');
    await page.goto(root + '#/run/' + historyRun);
    const event = (await records).events.find(item => item.severity === 'info');
    assert.ok(event);
    assert.equal(event.title, blue ? '[redacted]' : '合成イベントの件名');
    assert.equal(event.evidence_count, blue ? 0 : 1);
    assert.equal(event.agent.reason, blue ? undefined : '合成イベントの理由');
    if (!blue) {
      const panel = page.locator('section.panel').filter({has: page.getByRole('heading', {name: '公開イベント履歴'})});
      await panel.getByRole('table').waitFor();
      await page.route('**/runs/' + historyRun + '/events?*',
        route => route.fulfill({status: 409, json: {error: 'evidence_unverified'}}));
      await page.locator('#refresh-btn').click();
      await panel.getByText(/台帳の整合性を確認できない/).waitFor();
      assert.equal(await panel.getByRole('table').count(), 0);
    }
    await page.close();
  }
  console.log('Blueとoperatorの資料一覧・本文、Runイベントの応答と画面を確認');
  for (const width of [1440, 375]) {
    const page = await browser.newPage({viewport: {width, height: 860}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/#/campaign/' + campaign);
    await page.getByRole('button', {name: /^実行履歴/}).click();
    await page.locator('a[href="#/run/' + historyRun + '"]').first().click();
    const history = page.locator('section.panel').filter({has: page.getByRole('heading', {name: '検証履歴', exact: true})});
    await history.getByText('成功', {exact: false}).first().waitFor();
    await history.locator('summary').first().click();
    await history.getByText(checkpoint.target_last_event_hash, {exact: true}).first().waitFor();
    await history.getByText(checkpoint.checkpoint_digest, {exact: true}).first().waitFor();
    const bounds = await history.locator('details[open]').first().evaluate(el => ({
      right: el.getBoundingClientRect().right, scroll: el.scrollWidth, width: el.clientWidth
    }));
    assert.ok(bounds.right <= width + 1 && bounds.scroll <= bounds.width + 1, 'ハッシュを表示しても詳細は画面幅に収まる');
    const earlier = history.getByRole('button', {name: '以前の記録'});
    await earlier.focus();
    await page.keyboard.press('Enter');
    const previousFailure = history.getByRole('listitem').filter({hasText: 'checkpoint_mismatch'});
    await previousFailure.getByText('失敗', {exact: false}).waitFor();
    await previousFailure.locator('summary').click();
    await previousFailure.getByText('検証方法・対象は未記録', {exact: true}).waitFor();
    await history.getByRole('button', {name: '新しい記録'}).click();
    await history.getByText('1 ページ目', {exact: true}).waitFor();
    await previousFailure.waitFor({state: 'detached'});
    await page.goto(base + '/#/run/' + emptyRun);
    await history.getByText('検証記録は0件です。', {exact: true}).waitFor();
    assert.deepEqual(errors, []);
    await page.close();
    console.log(width + 'px: 検証時点の版、過去の失敗、新旧ページの移動、空の履歴を確認');
  }
} finally {
  await browser.close();
}

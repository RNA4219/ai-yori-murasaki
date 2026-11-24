import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';
const browser = await chromium.launch({channel: process.env.AYM_BROWSER_CHANNEL || undefined});
try {
  for (const width of [1440, 390, 320]) {
    const page = await browser.newPage({viewport: {width, height: 900}, reducedMotion: 'reduce'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const blue of [false, true]) {
      await page.goto(base + (blue ? '/?view=blue' : '/'));
      for (const [label, path] of [
        ['システム構成図', '/architecture'], ['案件受付台帳', '/intake'],
        ['判断の画面', '/decision'], ['モニター', '/'],
      ]) {
        const nav = page.getByRole('navigation', {name: '画面を切り替える'});
        const link = nav.getByRole('link', {name: label, exact: true});
        await link.focus();
        await page.keyboard.press('Enter');
        await nav.locator('[aria-current="page"]').waitFor();
        const current = new URL(page.url());
        assert.equal(current.pathname, path);
        assert.equal(current.searchParams.get('view'), blue ? 'blue' : null);
        assert.equal(await nav.getByRole('link').count(), 3);
        const {right, bottom} = await nav.boundingBox().then(({x,y,width,height}) => ({right:x+width,bottom:y+height}));
        assert.ok(right <= width && bottom <= 64, '製品ナビゲーションは一行に収まる');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width);
        if (blue && path === '/intake') {
          await page.getByText('Blueビューでは受付記録は非公開です。').waitFor();
        }
      }
    }
    assert.deepEqual(errors, []);
    await page.close();
    console.log(width + 'px: 4画面のキーボード移動、現在地、Blueビューを確認');
  }
  const recovery = await browser.newPage();
  await recovery.route('**/api/campaigns/*/decision-view*', route =>
    route.fulfill({json: {decision_view: {status: 'withheld'}}}));
  for (const blue of [false, true]) {
    const expected = blue ? 'blue' : null;
    await recovery.goto(base + '/decision?campaign=qa-case' + (blue ? '&view=blue' : ''));
    const back = recovery.getByRole('link', {name: '案件一覧へ戻る'});
    await back.waitFor();
    const list = new URL(await back.getAttribute('href'), base);
    assert.equal(list.pathname, '/decision');
    assert.equal(list.searchParams.get('view'), expected);
    await recovery.goto(base + '/architecture' + (blue ? '?view=blue' : ''));
    await recovery.locator('.guide summary').click();
    const product = recovery.getByRole('link', {name: /改修後の構成図を開く/});
    const url = new URL(await product.getAttribute('href'), base);
    assert.equal(url.searchParams.get('system'), 'aym');
    assert.equal(url.searchParams.get('view'), expected);
    assert.equal(url.searchParams.getAll('view').length, blue ? 1 : 0);
  }
  await recovery.close();

  const monitor = await browser.newPage();
  let status = 503;
  await monitor.route('**/api/campaigns?*', route => route.fulfill({
    status, json: status === 200 ? {campaigns: [], total: 0, page: 1, pages: 1} : {},
  }));
  const warning = monitor.getByText('案件一覧は前回取得に成功した内容を表示しています。');
  const timestamp = monitor.locator('#header-fetched');
  await monitor.clock.setFixedTime(new Date('2026-11-24T03:00:00Z'));
  await monitor.goto(base);
  await monitor.locator('.ledger').getByText(/取得に失敗しました/).waitFor();
  await monitor.waitForFunction(() => !document.querySelector('#refresh-btn').hasAttribute('aria-busy'));
  assert.equal(await timestamp.innerText(), '未取得');
  assert.equal(await warning.count(), 0);
  status = 200;
  await monitor.locator('#refresh-btn').click();
  await monitor.waitForFunction(() => document.querySelector('#header-fetched').textContent !== '未取得'
    && !document.querySelector('#refresh-btn').hasAttribute('aria-busy'));
  const successfulTime = await timestamp.innerText();
  status = 503;
  await monitor.clock.setFixedTime(new Date('2026-11-24T03:10:00Z'));
  await monitor.locator('#refresh-btn').click();
  await warning.waitFor();
  assert.equal(await timestamp.innerText(), successfulTime);
  await monitor.waitForFunction(() => !document.querySelector('#refresh-btn').hasAttribute('aria-busy'));
  status = 409;
  await monitor.locator('#refresh-btn').click();
  await monitor.locator('.ledger').getByText(/管理者が/).waitFor();
  assert.equal(await warning.count(), 0);
  await monitor.close();
  console.log('補助リンクの表示モード、初回失敗・復旧・再取得失敗の表示を確認');
} finally {
  await browser.close();
}

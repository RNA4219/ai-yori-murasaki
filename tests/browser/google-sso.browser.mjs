import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {chromium} from 'playwright';

async function exercise(browser, {monitor, provider}, width) {
  const page = await browser.newPage({viewport:{width, height:850}, reducedMotion:'reduce'});
  const errors = [], unexpected = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const origin = new URL(route.request().url()).origin;
    if (origin === monitor || origin === provider) return route.continue();
    unexpected.push(origin);
    return route.abort();
  });
  const deepLink = monitor + '/architecture?view=blue&system=aym';
  const login = page.getByRole('link', {name:'Google でログイン', exact:true});
  const logout = page.getByRole('button', {name:'ログアウト', exact:true});
  const heading = name => page.getByRole('heading', {level:1, name, exact:true}).waitFor();
  const health = () => page.request.get(monitor + '/api/health');
  async function choose(name) {
    await login.focus();
    await page.keyboard.press('Enter');
    await heading('テスト用アカウント選択');
    assert.equal(new URL(page.url()).origin, provider);
    await page.getByRole('link', {name, exact:true}).click();
  }
  try {
    await page.goto(deepLink);
    await heading('モニターにログイン');
    assert.equal(await page.getByRole('navigation').count(), 0);
    assert.equal((await health()).status(), 401);
    for (const [choice, message] of [
      ['キャンセル', 'ログインをキャンセルしました'],
      ['未許可の利用者', 'このアカウントでは利用できません'],
      ['一時エラー', '現在ログインできません'],
    ]) {
      await choose(choice);
      await heading(message);
      assert.equal(new URL(page.url()).origin, monitor);
      assert.equal((await health()).status(), 401);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
    await choose('許可した利用者');
    await page.waitForURL(deepLink);
    assert.equal((await health()).status(), 200);
    const nav = page.getByRole('navigation', {name:'画面を切り替える'});
    for (const label of ['モニター','案件受付台帳','判断の画面','システム構成図']) {
      await nav.getByRole('link', {name:label, exact:true}).click();
      await nav.locator('[aria-current="page"]').waitFor();
      assert.equal(new URL(page.url()).searchParams.get('view'), 'blue');
      const box = await logout.boundingBox(), menu = await nav.boundingBox();
      assert.ok(box && box.width >= 44 && box.height >= 44 && box.x + box.width <= width + 1);
      assert.ok(menu.y + menu.height <= 64, label+'のナビ下端 '+(menu.y+menu.height)+'px ('+width+'px)');
      assert.equal(await nav.getByRole('link').count(), 3);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const protectedPage = page.url();
      await logout.focus();
      const loggingOut = page.waitForResponse(response => new URL(response.url()).pathname === '/auth/logout');
      await page.keyboard.press('Enter');
      const response = await loggingOut;
      assert.equal(response.status(), 303, label+'からのログアウト');
      assert.equal(await response.request().headerValue('origin'), monitor);
      await heading('モニターにログイン');
      assert.equal((await health()).status(), 401);
      await page.goto(protectedPage);
      await heading('モニターにログイン');
      await choose('許可した利用者');
      await page.waitForURL(protectedPage);
      assert.equal((await health()).status(), 200);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log(width+'px: 合成Googleの選択・取消・未許可・再試行・Blueの4画面・ログアウトと再ログイン');
  } finally {
    await page.close();
  }
}

const fixture = spawn(process.env.PYTHON || 'python',
  ['-u','-m','tests.fixtures.google_sso_browser_server','--stop-on-eof'], {stdio:['pipe','pipe','inherit']});
const lines = createInterface({input:fixture.stdout});
const exited = once(fixture, 'exit');
let browser;
try {
  const [line] = await Promise.race([
    once(lines, 'line', {signal:AbortSignal.timeout(30000)}),
    exited.then(([code]) => { throw Error('SSO検収サーバーが起動前に終了しました（'+code+'）'); }),
  ]);
  const urls = JSON.parse(line);
  assert.equal(new URL(urls.monitor).hostname, '127.0.0.1');
  assert.equal(new URL(urls.provider).hostname, 'localhost');
  browser = await chromium.launch({channel:process.env.AYM_BROWSER_CHANNEL || undefined});
  for (const width of [1440, 375, 320]) await exercise(browser, urls, width);
} finally {
  try {
    await browser?.close();
  } finally {
    lines.close();
    fixture.stdin.end();
    await exited;
  }
}

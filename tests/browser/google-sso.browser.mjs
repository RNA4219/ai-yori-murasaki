import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {chromium} from 'playwright';

async function exercise(browser, {monitor, provider}, width, policy) {
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
    const authorize = new URL(page.url());
    assert.equal(authorize.origin, provider);
    const scope = authorize.searchParams.get('scope').split(/\s+/).sort();
    assert.deepEqual(scope, policy === 'subject' ? ['openid','profile'] : ['email','openid']);
    await page.getByRole('link', {name, exact:true}).click();
  }
  try {
    await page.goto(deepLink);
    await heading('モニターにログイン');
    assert.equal(await page.getByRole('navigation').count(), 0);
    assert.equal((await health()).status(), 401);
    const forbidden = 'このアカウントでは利用できません';
    const retries = [
      ['キャンセル', 'ログインをキャンセルしました'],
      ['未許可の利用者', forbidden],
      ['一時エラー', '現在ログインできません'],
    ];
    if (policy !== 'subject') retries.push(['利用者IDのみ一致', forbidden], ['未確認のメール', forbidden]);
    if (policy === 'both') retries.push(['ドメインのみ一致', forbidden]);
    for (const [choice, message] of retries) {
      await choose(choice);
      await heading(message);
      assert.equal(new URL(page.url()).origin, monitor);
      assert.equal((await health()).status(), 401);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
    const allowed = policy === 'domain' ? 'ドメインのみ一致' : '許可した利用者';
    await choose(allowed);
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
      await choose(allowed);
      await page.waitForURL(protectedPage);
      assert.equal((await health()).status(), 200);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log(width+'px '+policy+' 合成Googleの選択・取消・未許可・再試行・Blueの4画面・ログアウトと再ログイン');
  } finally {
    await page.close();
  }
}

async function checkPolicy(browser, policy, widths) {
  const fixture = spawn(process.env.PYTHON || 'python',
    ['-u','-m','tests.fixtures.google_sso_browser_server','--policy',policy,'--stop-on-eof'],
    {stdio:['pipe','pipe','inherit']});
  const lines = createInterface({input:fixture.stdout});
  const exited = once(fixture, 'exit');
  try {
    const [line] = await Promise.race([
      once(lines, 'line', {signal:AbortSignal.timeout(30000)}),
      exited.then(([code]) => { throw Error('SSO検収サーバーが起動前に終了しました（'+code+'）'); }),
    ]);
    const urls = JSON.parse(line);
    assert.equal(new URL(urls.monitor).hostname, '127.0.0.1');
    assert.equal(new URL(urls.provider).hostname, 'localhost');
    for (const width of widths) await exercise(browser, urls, width, policy);
  } finally {
    lines.close();
    fixture.stdin.end();
    await exited;
  }
}

const browser = await chromium.launch({channel:process.env.AYM_BROWSER_CHANNEL || undefined});
try {
  await checkPolicy(browser, 'subject', [1440, 375, 320]);
  await checkPolicy(browser, 'domain', [1440, 375]);
  await checkPolicy(browser, 'both', [1440, 375]);
} finally {
  await browser.close();
}

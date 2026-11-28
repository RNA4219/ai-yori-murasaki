import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {chromium} from 'playwright';

async function exercise(browser, {monitor, provider}, width, policy) {
  const page = await browser.newPage({
    viewport:{width, height:850}, reducedMotion:'reduce',
    colorScheme:policy === 'domain' ? 'dark' : 'light',
    forcedColors:policy === 'both' ? 'active' : 'none',
  });
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
  const health = () => page.evaluate(async () => (await fetch('/api/health')).status);
  async function checkLoginScreen(message) {
    const main = page.getByRole('main');
    const title = main.getByRole('heading', {level:1, name:message, exact:true});
    await title.waitFor();
    const blocks = (await main.locator('p, h1, a').allTextContents()).map(text => text.trim());
    assert.equal(blocks.length, 4, 'ブランド、見出し、案内、ログイン操作の順に並ぶ');
    assert.ok(blocks[0].endsWith('AI YORI MURASAKI'));
    assert.equal(blocks[1], message);
    assert.ok(blocks[2].length > 0);
    assert.equal(blocks[3], 'Google でログイン');
    const card = await main.boundingBox();
    const viewport = page.viewportSize();
    assert.ok(card.width <= 441 && card.x >= 12, 'カード幅と左右の余白');
    assert.ok(Math.abs(card.x + card.width/2 - viewport.width/2) <= 1, 'カードの水平中央配置');
    assert.ok(Math.abs(card.y + card.height/2 - viewport.height/2) <= 1, 'カードの垂直中央配置');
    const textBlocks = main.locator('h1, p');
    const readable = await textBlocks.evaluateAll(elements => elements.every(element => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return [...range.getClientRects()].every(rect => rect.left >= 0 && rect.right <= innerWidth + 1)
        && element.scrollHeight <= element.clientHeight + 1;
    }));
    assert.ok(readable, message+'の折り返し ('+width+'px)');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const box = await login.boundingBox();
    const titleBox = await title.boundingBox();
    assert.ok(box && box.height >= 48 && Math.abs(box.x - titleBox.x) <= 1
      && Math.abs(box.width - titleBox.width) <= 1, 'ログイン操作が本文幅で高さ48px以上');
    assert.ok(box.y >= 0 && box.y + box.height <= viewport.height, 'ログイン操作が初期表示内にある');
    await page.keyboard.press('Tab');
    const focused = await login.evaluate(element => {
      const style = getComputedStyle(element);
      return element.matches(':focus-visible')
        && ((style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0) || style.boxShadow !== 'none');
    });
    assert.ok(focused, 'Tab でログイン操作に移りフォーカスが見える ('+width+'px)');
  }
  async function secureCookie(prefix, path) {
    const name = prefix + new URL(monitor).port;
    const cookie = (await page.context().cookies(monitor + path)).find(entry => entry.name === name);
    assert.ok(cookie && cookie.secure && cookie.httpOnly && cookie.sameSite === 'Lax', name);
  }
  async function choose(name) {
    await login.focus();
    await page.keyboard.press('Enter');
    await heading('テスト用アカウント選択');
    const authorize = new URL(page.url());
    assert.equal(authorize.origin, provider);
    assert.equal(authorize.searchParams.get('redirect_uri'), monitor + '/auth/google/callback');
    await secureCookie('aym_login_', '/auth/google/callback');
    const scope = authorize.searchParams.get('scope').split(/\s+/).sort();
    assert.deepEqual(scope, policy === 'subject' ? ['openid','profile'] : ['email','openid']);
    await page.getByRole('link', {name, exact:true}).click();
  }
  try {
    const entry = await page.goto(deepLink);
    assert.match((await entry.securityDetails()).protocol, /^TLS 1\.[23]$/);
    await checkLoginScreen('モニターにログイン');
    assert.equal(await page.getByRole('navigation').count(), 0);
    assert.equal(await health(), 401);
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
      await checkLoginScreen(message);
      assert.equal(new URL(page.url()).origin, monitor);
      assert.equal(await health(), 401);
    }
    const allowed = policy === 'domain' ? 'ドメインのみ一致' : '許可した利用者';
    await choose(allowed);
    await page.waitForURL(deepLink);
    assert.equal(await health(), 200);
    await secureCookie('aym_session_', '/');
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
      assert.equal(await health(), 401);
      await page.goto(protectedPage);
      await heading('モニターにログイン');
      await choose(allowed);
      await page.waitForURL(protectedPage);
      assert.equal(await health(), 200);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    console.log(width+'px '+policy+' 合成Googleの選択・取消・未許可・再試行・Blueの4画面・ログアウトと再ログイン');
  } finally {
    await page.close();
  }
}

async function checkPolicy(policy, widths) {
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
    assert.equal(new URL(urls.monitor).protocol, 'https:');
    assert.equal(new URL(urls.provider).protocol, 'https:');
    assert.match(urls.spkiPin, /^[A-Za-z0-9+/]{43}=$/);
    const channel = process.env.AYM_BROWSER_CHANNEL || undefined;
    if (policy === 'subject') {
      const untrusted = await chromium.launch({channel});
      try {
        const page = await untrusted.newPage();
        await assert.rejects(page.goto(urls.monitor), /ERR_CERT_AUTHORITY_INVALID/);
      } finally {
        await untrusted.close();
      }
    }
    const browser = await chromium.launch({channel,
      args:['--ignore-certificate-errors-spki-list=' + urls.spkiPin]});
    try {
      for (const width of widths) await exercise(browser, urls, width, policy);
    } finally {
      await browser.close();
    }
  } finally {
    lines.close();
    fixture.stdin.end();
    await exited;
  }
}

await checkPolicy('subject', [1440, 375, 320]);
await checkPolicy('domain', [1440, 375]);
await checkPolicy('both', [1440, 375]);

import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {chromium} from 'playwright';

const base = process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';
const browser = await chromium.launch({headless:true, channel:process.env.AYM_BROWSER_CHANNEL || undefined});
const target = {id:'desk-final', kind:'external', name:'終点の受付窓口', type:'受付台帳',
  description:'VeryLongUnbrokenReference'.repeat(16), revision:7};
const fixture = {schema:'aym.architecture/v1', name:'架空の資料受付', nodes:[
  ...Array.from({length:32}, (_, i) => ({id:'station-'+i, kind:'service', name:'架空の窓口 '+i})), target
], connections:[], findings:[]};
const custom = {id:'handoff', label:'引き継ぎ場所'};

async function openFile(page, buffer) {
  await page.getByLabel('構成ファイルを開く').setInputFiles({name:'desks.json', mimeType:'application/json', buffer});
  await page.getByRole('heading', {name:fixture.name, exact:true}).waitFor();
}

async function check(page, width) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(new URL('/', base).href);
  await page.getByRole('link', {name:'システム構成図', exact:true}).click();
  await page.getByRole('heading', {name:'注文管理サービス', exact:true}).waitFor();
  await openFile(page, Buffer.from(JSON.stringify(fixture)));
  const [panel, detail] = await Promise.all(
    ['.map-panel','#detail'].map(selector => page.locator(selector).boundingBox()));
  const workspaceWidth = await page.locator('#workspace').evaluate(el => el.clientWidth);
  assert.ok(Math.abs(panel.width - workspaceWidth) <= 1 && Math.abs(detail.width - workspaceWidth) <= 1,
    '図と詳細が作業領域の全幅を使う');
  assert.ok(detail.y >= panel.y + panel.height - 1, '詳細が図の下に並ぶ');
  assert.equal(await page.locator('#zoom-value').textContent(), '100%');
  assert.ok(await page.locator('#diagram').evaluate(svg => Math.abs(svg.getScreenCTM().a - 1) < 0.02));

  const search = page.getByRole('searchbox', {name:'要素を検索'});
  const choices = page.getByRole('combobox', {name:'構成要素を選択'});
  const open = page.getByRole('button', {name:'詳細を開く'});
  await search.fill('一致しない語');
  assert.equal(await choices.locator('option:enabled').count(), 0);
  assert.ok(await open.isDisabled());
  await search.fill('終点');
  assert.deepEqual(await choices.locator('option:enabled').evaluateAll(options => options.map(o => o.value)), [target.id]);
  await open.click();
  await page.getByRole('heading', {name:target.name, exact:true}).waitFor();
  assert.ok(await page.locator('#detail').evaluate(el => el === document.activeElement));
  assert.ok(await page.locator('#description').evaluate(el =>
    el.scrollWidth <= el.clientWidth + 1 && el.clientHeight > parseFloat(getComputedStyle(el).lineHeight)));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByRole('button', {name:'図に戻る'}).click();
  const card = page.locator('.node[data-id="desk-final"]');
  assert.ok(await card.evaluate(el => {
    const box = el.getBoundingClientRect(), frame = el.ownerSVGElement.getBoundingClientRect();
    return document.activeElement === el && box.left >= frame.left - 1 && box.right <= frame.right + 1
      && box.top >= frame.top - 1 && box.bottom <= frame.bottom + 1;
  }), '選んだ要素が図に入り、キーボード操作を再開できる');

  await page.keyboard.press('Enter');
  await page.getByText('要素の種類を変更', {exact:true}).click();
  await page.getByLabel('種類ID', {exact:true}).fill(custom.id);
  await page.getByLabel('表示名', {exact:true}).fill(custom.label);
  await page.getByRole('button', {name:'種類を追加して割り当てる'}).click();
  const legend = page.getByRole('list', {name:'要素の種類'});
  await legend.getByRole('listitem').filter({hasText:custom.label}).waitFor();
  assert.equal(await page.getByLabel('この要素の種類').inputValue(), custom.id);
  assert.equal(await card.getAttribute('data-kind'), custom.id);
  const reset = page.getByRole('button', {name:'見本に戻す'});
  await reset.click();
  const dialog = page.getByRole('dialog', {name:'未保存の変更があります'});
  await dialog.waitFor();
  await page.keyboard.press('Escape');
  await dialog.waitFor({state:'hidden'});
  assert.ok(await page.locator('#unsaved').isVisible());
  assert.equal(await card.getAttribute('data-kind'), custom.id);

  const pending = page.waitForEvent('download');
  await page.getByRole('button', {name:'構成図を保存', exact:true}).click();
  const saved = await readFile(await (await pending).path());
  const parsed = JSON.parse(saved);
  assert.deepEqual(parsed.nodeKinds, [custom]);
  assert.deepEqual(parsed.nodes.find(node => node.id === target.id), {...target, kind:custom.id});
  await page.getByRole('button', {name:'JSONの保存を確認した'}).click();
  await reset.click();
  await page.getByRole('heading', {name:'注文管理サービス', exact:true}).waitFor();
  await openFile(page, saved);
  assert.equal(await card.getAttribute('data-kind'), custom.id);
  await legend.getByRole('listitem').filter({hasText:custom.label}).waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS 構成図の可読性・検索・復帰・独自種類の保存と再読込 '+width+'px');
}

try {
  for (const width of [1440, 375]) {
    const page = await browser.newPage({viewport:{width, height:900}, reducedMotion:'reduce'});
    try { await check(page, width); } finally { await page.close(); }
  }
} finally {
  await browser.close();
}

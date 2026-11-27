import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp, mkdir, readFile, readdir, rm, stat, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {chromium} from 'playwright';

// node tests/browser/architecture-switch.browser.mjs（インストール済みの製品依存と Chromium を使用）
const root = await mkdtemp(join(tmpdir(), 'aym-map-switch-'));
const workspace = join(root, 'workspace');
const directory = join(workspace, 'architecture');
const sample = '注文管理サービス';
const product = 'ai-yori-murasaki の構成図';
const longName = '図'.repeat(200);
const originalName = longName + '（保管用の原本）';
const annotations = {revision: 3, owner: '架空の編集担当'};

function mapFile(nodeId, name, extra = {}) {
  return JSON.stringify({schema: 'aym.architecture/v1', name, ...extra,
    nodes: [{id: nodeId, kind: 'service', name: nodeId}], connections: [], findings: []});
}

async function snapshot() {
  const files = [];
  for (const entry of (await readdir(directory)).sort()) {
    const path = join(directory, entry);
    files.push([entry, await readFile(path, 'utf8'), (await stat(path)).mtimeMs]);
  }
  return files;
}

function heading(page, title) {
  return page.getByRole('heading', {name: title, exact: true}).waitFor();
}

async function downloadMap(page) {
  const pending = page.waitForEvent('download');
  await page.getByRole('button', {name: '構成図を保存', exact: true}).click();
  const stream = await (await pending).createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const bytes = Buffer.concat(chunks);
  const doc = JSON.parse(bytes);
  assert.equal(doc.name, originalName, '保存時に原本の名称を保つ');
  assert.deepEqual(doc.annotations, annotations, '製品で扱わない原本の項目も保持する');
  return bytes;
}

async function enter(page, url) {
  await page.goto(url);
  await page.getByRole('link', {name: 'システム構成図', exact: true}).click();
  await heading(page, sample);
}

async function switchMaps(browser, base, width) {
  const page = await browser.newPage({viewport: {width, height: 900}, reducedMotion: 'reduce'});
  try {
    const errors = [];
    const methods = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/architecture/maps')) methods.push(request.method());
    });
    await enter(page, base);
    const picker = page.getByRole('combobox', {name: '図', exact: true});
    await picker.waitFor();
    const labels = picker.locator('option:enabled');
    assert.deepEqual(await labels.allTextContents(), ['見本', '製品自身の図', 'alpha', longName]);
    await picker.selectOption({label: longName});
    await heading(page, longName);
    await page.locator('.node[data-id="second"]').waitFor();
    assert.equal(new URL(page.url()).search, '?map=zeta');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.reload();
    await heading(page, longName);
    assert.equal(await picker.locator('option:checked').textContent(), longName);

    const saved = await downloadMap(page);
    await picker.selectOption({label: '見本'});
    await heading(page, sample);
    await page.getByLabel('構成ファイルを開く').setInputFiles({
      name: '保存した構成図.json', mimeType: 'application/json', buffer: saved});
    await heading(page, longName);
    await page.locator('.node[data-id="second"]').waitFor();
    assert.equal(new URL(page.url()).search, '');
    await downloadMap(page);

    await picker.selectOption({label: 'alpha'});
    await heading(page, 'alpha');
    assert.equal(new URL(page.url()).search, '?map=alpha');
    await page.getByRole('button', {name: '範囲を指定'}).click();
    await page.locator('.node[data-id="first"]').focus();
    await page.keyboard.press('Enter');
    await page.locator('#unsaved').waitFor();
    await picker.selectOption({label: longName});
    const dialog = page.getByRole('dialog', {name: '未保存の変更があります'});
    await dialog.waitFor();
    await page.keyboard.press('Escape');
    await dialog.waitFor({state: 'hidden'});
    await heading(page, 'alpha');
    assert.equal(await picker.locator('option:checked').textContent(), 'alpha');
    assert.equal(new URL(page.url()).search, '?map=alpha');
    assert.ok(await page.locator('#unsaved').isVisible());

    await picker.selectOption({label: '製品自身の図'});
    await dialog.getByRole('button', {name: '保存せず切り替える'}).click();
    await heading(page, product);
    assert.equal(new URL(page.url()).search, '?system=aym');
    await page.reload();
    await heading(page, product);
    await picker.selectOption({label: '見本'});
    await heading(page, sample);
    assert.equal(new URL(page.url()).search, '');

    await picker.selectOption({label: 'alpha'});
    await heading(page, 'alpha');
    await page.getByLabel('構成ファイルを開く').setInputFiles({
      name: 'local.json', mimeType: 'application/json', buffer: Buffer.from(mapFile('file-node', '手元の図'))});
    await heading(page, '手元の図');
    assert.equal(new URL(page.url()).search, '');
    await page.reload();
    await heading(page, sample);

    await page.goto(base + '/architecture?map=unlisted');
    await heading(page, sample);
    await page.locator('#error').waitFor();
    assert.ok((await page.locator('#error').innerText()).trim());
    assert.equal(new URL(page.url()).search, '');
    assert.equal(await picker.locator('option:checked').textContent(), '見本');

    await enter(page, base + '/?view=blue');
    assert.equal(new URL(page.url()).searchParams.get('view'), 'blue');
    assert.equal(await picker.isVisible(), false);
    for (const path of ['/api/architecture/maps', '/api/architecture/maps/alpha']) {
      const response = await page.request.get(base + path + '?view=blue');
      assert.equal(response.status(), 200);
      assert.deepEqual(await response.json(), {status: 'withheld'});
    }
    assert.ok(methods.length > 0 && methods.every(method => method === 'GET'));
    assert.deepEqual(errors, []);
    console.log('PASS 構成図の切替・再読込・復帰・未保存取消・不明 ID・Blue ' + width + 'px');
  } finally {
    await page.close();
  }
}

let server, browser;
try {
  // CLI と同じ入口を使い、検証用の場所と空きポートは実行ごとに分ける。
  server = spawn(process.env.PYTHON || 'python', ['-u', '-m', 'ai_yori_murasaki',
    '--workspace', workspace, '--trusted-root', join(root, 'trusted'),
    'monitor', 'serve', '--allow-http-loopback', '--bind', '127.0.0.1:0'], {stdio: ['ignore', 'pipe', 'inherit']});
  const output = createInterface({input: server.stdout});
  const [line] = await Promise.race([
    once(output, 'line', {signal: AbortSignal.timeout(30000)}),
    once(server, 'exit').then(([code]) => {throw Error('モニター起動前に終了 ' + code);}),
  ]);
  output.close();
  const base = JSON.parse(line).data.url;
  assert.equal(new URL(base).hostname, '127.0.0.1');
  browser = await chromium.launch({headless: true, channel: process.env.AYM_BROWSER_CHANNEL || undefined});
  const empty = await browser.newPage();
  try {
    const list = empty.waitForResponse(response => new URL(response.url()).pathname === '/api/architecture/maps');
    await enter(empty, base);
    assert.deepEqual(await (await list).json(), {maps: []});
    assert.equal(await empty.getByRole('combobox', {name: '図', exact: true}).isVisible(), false);
    await assert.rejects(stat(directory), {code: 'ENOENT'});
  } finally {
    await empty.close();
  }

  await mkdir(directory);
  await writeFile(join(directory, 'zeta.json'), mapFile('second', originalName, {annotations}));
  await writeFile(join(directory, 'alpha.json'), mapFile('first'));
  const before = await snapshot();
  for (const width of [1440, 375]) await switchMaps(browser, base, width);
  assert.deepEqual(await snapshot(), before, '構成図の一覧・原本・更新日時を維持する');
} finally {
  try {
    await browser?.close();
  } finally {
    if (server?.pid && server.exitCode === null && server.signalCode === null) {
      const stopped = once(server, 'exit');
      server.kill();
      await stopped;
    }
    await rm(root, {recursive: true, force: true});
  }
}

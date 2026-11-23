import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base=process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';
const browser=await chromium.launch({channel:process.env.AYM_BROWSER_CHANNEL || undefined});
const [from,to]=['views','api'];

async function wirePoint(wire) {
  const point=await wire.evaluate(g=>{
    const path=g.querySelector('.wire-hit'), length=path.getTotalLength();
    for(let i=25;i<74;i++) {
      const p=path.getPointAtLength(length*(i%50)/50).matrixTransform(path.getScreenCTM());
      const hits=document.elementsFromPoint(p.x,p.y);
      if(hits[0]?.closest('.wire') && hits.some(el=>el.closest('.wire')===g)) return {x:p.x,y:p.y};
    }
    return null;
  });
  assert.ok(point,'画面上で接続線を直接選べる');
  return point;
}

try {
  const layout=await browser.newPage({viewport:{width:1108,height:900}});
  await layout.goto(base+'/architecture?system=aym');
  await layout.getByRole('heading',{name:'ai-yori-murasaki の構成図',exact:true}).waitFor();
  assert.equal(await layout.locator('#note').isVisible(),false);
  await layout.locator('#choose').selectOption('summary');
  assert.equal(await layout.locator('#description').isVisible(),false);
  for(const width of [1108,390,320]) {
    await layout.setViewportSize({width,height:900});
    for(const mode of ['all','repos']) {
      await layout.locator('#'+mode).click();
      const row=await layout.locator('.panel-head').evaluate(bar=>{
        const controls=[...bar.querySelectorAll('button,output')].map(el=>el.getBoundingClientRect());
        const centers=controls.map(rect=>rect.top+rect.height/2);
        return {count:controls.length,spread:Math.max(...centers)-Math.min(...centers),
          scroll:bar.scrollWidth-bar.clientWidth,pageWidth:document.documentElement.scrollWidth};
      });
      assert.equal(row.count,7);
      assert.ok(row.spread<=1,'表示切替・倍率・配置操作が一列に並ぶ');
      assert.equal(row.pageWidth,width);
      if(width>=390) assert.equal(row.scroll,0,'全操作が横スクロールなしで収まる');
    }
    const last=layout.getByRole('button',{name:'配置を戻す',exact:true});
    await last.scrollIntoViewIfNeeded();
    assert.equal(await last.evaluate(el=>{
      const button=el.getBoundingClientRect(), bar=el.closest('.panel-head').getBoundingClientRect();
      return button.left>=bar.left && button.right<=bar.right;
    }),true);
  }
  await layout.close();
  for(const mobile of [false,true]) {
    const page=await browser.newPage({viewport:{width:mobile?390:1440,height:900},hasTouch:mobile,reducedMotion:'reduce'});
    const errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(base+'/architecture?system=aym');
    const frame=page.locator('#frame'), node=page.locator('[data-id="views"]');
    await node.waitFor();
    await page.locator('#fit-map').click();
    await frame.scrollIntoViewIfNeeded();
    const wire=page.locator('.wire').filter({has:page.locator('.edge[data-from="views"][data-to="api"]')});
    const coords=()=>node.getAttribute('transform');
    const path=()=>wire.locator('.edge').getAttribute('d');
    const viewport=()=>page.locator('#diagram').getAttribute('viewBox');
    if(!mobile) {
      const diagram=page.locator('#diagram'), box=await diagram.boundingBox();
      const pointer={x:Math.round(box.x+box.width*.3),y:Math.round(box.y+box.height*.4)};
      const worldPoint=()=>diagram.evaluate((svg,p)=>{
        const point=new DOMPoint(p.x,p.y).matrixTransform(svg.getScreenCTM().inverse());
        return {x:point.x,y:point.y};
      },pointer);
      const start=await worldPoint(), zoom=await page.locator('#zoom-value').textContent();
      const scroll=await page.evaluate(()=>window.scrollY);
      await page.mouse.move(pointer.x,pointer.y);
      await page.mouse.wheel(0,-100);
      await page.waitForFunction(z=>parseInt(document.querySelector('#zoom-value').textContent)>parseInt(z),zoom);
      const after=await worldPoint();
      assert.ok(Math.hypot(after.x-start.x,after.y-start.y)<.01,'ポインター下の図の座標を保持する');
      await page.mouse.wheel(0,100);
      await page.waitForFunction(z=>document.querySelector('#zoom-value').textContent===z,zoom);
      assert.equal(await page.evaluate(()=>window.scrollY),scroll);
      await page.locator('#name').hover();
      const outsideScroll=await page.evaluate(()=>window.scrollY);
      await page.mouse.wheel(0,100);
      await page.waitForFunction(y=>window.scrollY>y,outsideScroll);
      assert.equal(await page.locator('#zoom-value').textContent(),zoom);
      await frame.scrollIntoViewIfNeeded();
    }
    const original=await coords(), originalPath=await path();
    const touch=mobile?await page.context().newCDPSession(page):null;
    async function drag(x,y,dx,dy) {
      if(touch) {
        await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
        await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+dx,y:y+dy}]});
        await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
      } else {
        await page.mouse.move(x,y);await page.mouse.down();
        await page.mouse.move(x+dx,y+dy,{steps:6});await page.mouse.up();
      }
    }
    const box=await node.boundingBox();
    await drag(box.x+box.width/2,box.y+box.height/2,22,28);
    assert.notEqual(await coords(),original);
    assert.notEqual(await path(),originalPath);
    assert.equal(await page.locator('#reset-layout').isEnabled(),true);
    const beforeZoom=parseInt(await page.locator('#zoom-value').textContent());
    await page.locator('#zoom-in').click();
    assert.ok(parseInt(await page.locator('#zoom-value').textContent())>beforeZoom);
    const stationary=await coords(), beforePan=await viewport();
    await frame.scrollIntoViewIfNeeded();
    const empty=await frame.evaluate(el=>{
      const rect=el.getBoundingClientRect();
      for(let y=rect.top+12;y<rect.bottom-32;y+=12) for(let x=rect.left+12;x<rect.right-40;x+=18) {
        const target=document.elementFromPoint(x,y);
        if(target?.closest('#diagram') && !target.closest('.wire,.node')) return {x,y};
      }
    });
    assert.ok(empty);
    await drag(empty.x,empty.y,28,24);
    assert.notEqual(await viewport(),beforePan);
    assert.equal(await coords(),stationary);
    if(touch) {
      const r=await frame.boundingBox(),x=r.x+r.width/2,y=r.y+r.height/2;
      const pins=gap=>[{id:0,x:x-gap,y},{id:1,x:x+gap,y}];
      const before=parseInt(await page.locator('#zoom-value').textContent());
      await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:pins(28)});
      await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:pins(55)});
      await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
      assert.ok(parseInt(await page.locator('#zoom-value').textContent())>before);
    }
    await page.locator('#reset-layout').click();
    assert.equal(await coords(),original);
    assert.equal(await path(),originalPath);
    await frame.scrollIntoViewIfNeeded();
    const p=await wirePoint(wire);
    if(mobile) await page.touchscreen.tap(p.x,p.y);else await page.mouse.click(p.x,p.y);
    await page.locator('#detail-kind').getByText('選択中の接続').waitFor();
    if(mobile && await wire.getAttribute('aria-pressed')!=='true') {
      await page.getByLabel('この位置の接続').selectOption(await wire.getAttribute('data-wire'));
    }
    const product=await (await page.request.get(base+'/architecture/product.json')).json();
    const record=product.connections.find(c=>c.from===from && c.to===to);
    assert.ok((await page.locator('#findings').innerText()).includes(record.label));
    assert.ok((await page.locator('#findings').innerText()).includes(record.evidence));
    assert.equal(await wire.getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    if(process.env.AYM_CANVAS_CAPTURE && !mobile) await page.locator('#workspace').screenshot({path:process.env.AYM_CANVAS_CAPTURE});
    await node.focus();await page.keyboard.press('ArrowRight');
    assert.notEqual(await coords(),original);
    await page.keyboard.press('Enter');
    assert.match(await page.locator('#detail-name').innerText(),/モニター/);
    await wire.focus();await page.keyboard.press('Enter');
    assert.equal(await page.locator('#detail-kind').innerText(),'選択中の接続');
    {
      const nodes=[{id:'a',name:'受付',kind:'service'},{id:'b',name:'保管',kind:'store'}];
      const connections=[{from:'a',to:'b',label:'記録する',evidence:'仕様書A'},{from:'a',to:'b',label:'状態を照合する'}];
      const file={schema:'aym.architecture/v1',name:'重複する接続の確認',nodes,connections};
      await page.locator('#file').setInputFiles({name:'local-map.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(file))});
      await page.getByRole('heading',{name:file.name,exact:true}).waitFor();
      assert.equal(await page.locator('#reset-layout').isDisabled(),true);
      await page.locator('.wire').focus();await page.keyboard.press('Enter');
      const reasons=await page.locator('#findings').innerText();
      for(const value of ['記録する','仕様書A','状態を照合する','根拠の記録はありません']) assert.ok(reasons.includes(value));
      const fields=name=>page.getByRole('group',{name,exact:true});
      const first=fields('記録する'), second=fields('状態を照合する');
      await second.getByLabel('利用者の入力経路').selectOption('ui');
      await first.getByLabel('利用者の入力経路').selectOption('ui');
      assert.equal(await page.locator('.user-peg').count(),1,'同じ送信元の複数の入力記録にコマは1つ');
      await second.getByLabel('利用者の入力経路').selectOption('internal');
      assert.equal(await page.locator('[data-id="a"] .user-peg').count(),1);
      await first.getByRole('textbox').fill('受付フォームの件名と本文');
      assert.equal(await page.locator('.wire').getAttribute('data-input'),'ui');
      assert.equal(await page.locator('#unsaved').isVisible(),true);
      const pending=page.waitForEvent('download');
      await page.getByRole('button',{name:mobile?'変更をJSONに保存':'構成図を保存',exact:true}).click();
      const download=await pending, chunks=[];
      for await(const chunk of await download.createReadStream()) chunks.push(chunk);
      const buffer=Buffer.concat(chunks);
      const saved=JSON.parse(buffer);
      assert.deepEqual(saved.nodes,nodes);
      assert.deepEqual(saved.connections,[{...connections[0],userInput:{mode:'ui',scope:'受付フォームの件名と本文'}},
        {...connections[1],userInput:{mode:'internal'}}]);
      await page.locator('#file').setInputFiles({name:download.suggestedFilename(),mimeType:'application/json',buffer});
      await page.getByText('読み込み元　'+download.suggestedFilename(),{exact:true}).waitFor();
      assert.equal(await page.locator('[data-id="a"] .user-peg').count(),1);
      await page.getByRole('button',{name:'入力経路を編集　記録する',exact:true}).click();
      assert.equal(await first.getByRole('textbox').inputValue(),'受付フォームの件名と本文');
      assert.equal(await second.getByRole('combobox').inputValue(),'internal');
      assert.equal(await second.getByRole('textbox').count(),0);
      await first.getByRole('combobox').selectOption('internal');
      assert.equal(await page.locator('.user-peg').count(),0);
      assert.equal(await page.locator('.wire').getAttribute('data-input'),'internal');
      await first.getByRole('combobox').selectOption('');
      await second.getByRole('combobox').selectOption('');
      assert.equal(await page.locator('#input-legend').isVisible(),false);
    }
    assert.deepEqual(errors,[]);await page.close();
    console.log('PASS 構成と接続　'+(mobile?'タッチ・ピンチ':'マウス')+'・キーボード');
  }
} finally { await browser.close(); }

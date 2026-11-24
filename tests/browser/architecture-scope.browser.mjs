import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const browser=await chromium.launch({channel:process.env.AYM_BROWSER_CHANNEL || undefined});
const base=process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';

// SVGの塗りの内外を調べ、設定値に加えて画面上の囲み方も確認する。
async function enclosed(page){
  return page.locator('#diagram').evaluate(svg=>{
    const fill=svg.querySelector('.scope-fill');
    if(!fill) return [];
    return [...svg.querySelectorAll('.node')].filter(n=>{
      const box=n.querySelector('.box').getBBox();
      const center=new DOMPoint(box.width/2,box.height/2).matrixTransform(n.transform.baseVal.consolidate().matrix);
      return fill.isPointInFill(center);
    }).map(n=>n.dataset.id);
  });
}
try{
  for(const width of [1440,390]){
    const mobile=width===390;
    const page=await browser.newPage({viewport:{width,height:900},hasTouch:mobile});
    await page.goto(base+'/architecture');
    await page.locator('.scope-caption').waitFor();
    assert.deepEqual(await enclosed(page),['web','gateway']);
    const nodes=['a','b','c'].map(id=>({id,name:id,kind:'service'}));
    const original={schema:'aym.architecture/v1',name:'範囲未指定',nodes,connections:[]};
    const upload=async(name,buffer)=>page.locator('#file').setInputFiles({name,mimeType:'application/json',buffer});
    await upload('input.json',Buffer.from(JSON.stringify(original)));
    await page.getByRole('heading',{name:original.name,exact:true}).waitFor();
    assert.deepEqual(await enclosed(page),[]);
    await page.locator('#scope-toggle').click();
    await page.locator('#fit-map').click();
    await page.locator('#frame').scrollIntoViewIfNeeded();
    const box=id=>page.locator('.node[data-id="'+id+'"] .box');
    const a=await box('a').boundingBox(), b=await box('b').boundingBox();
    const start={x:Math.min(a.x,b.x)-3,y:Math.min(a.y,b.y)-3};
    const end={x:Math.max(a.x+a.width,b.x+b.width)+3,y:Math.max(a.y+a.height,b.y+b.height)+3};
    if(mobile){
      const cdp=await page.context().newCDPSession(page);
      await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[start]});
      await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[end]});
      await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    }else{
      await page.mouse.move(start.x,start.y);await page.mouse.down();
      await page.mouse.move(end.x,end.y,{steps:5});await page.mouse.up();
    }
    assert.deepEqual(await enclosed(page),['a','b']);
    if(mobile) await box('b').tap(); else await box('b').click();
    const c=page.locator('.node[data-id="c"]');
    await c.focus();await page.keyboard.press('Enter');
    assert.deepEqual(await enclosed(page),['a','c']);
    await page.locator('#scope-toggle').click();
    const outline=page.locator('.scope-border'), before=await outline.getAttribute('d');
    await c.focus();await page.keyboard.press('ArrowRight');
    assert.notEqual(await outline.getAttribute('d'),before);
    assert.deepEqual(await enclosed(page),['a','c']);
    const scopeExport=page.waitForEvent('download');
    await page.locator('#scope-save').click();
    const scopeFile=await scopeExport, scopeBytes=[];
    for await(const chunk of await scopeFile.createReadStream()) scopeBytes.push(chunk);
    assert.deepEqual(JSON.parse(Buffer.concat(scopeBytes)).userScope,['a','c']);
    await page.locator('#scope-toggle').click();
    await c.focus();await page.keyboard.press('Enter');
    await page.locator('#scope-toggle').click();
    assert.deepEqual(await enclosed(page),['a']);
    await upload('不正.json',Buffer.from('{'));
    await page.locator('#error').waitFor();
    assert.equal(await page.locator('#replace-dialog').isVisible(),false);
    assert.deepEqual(await enclosed(page),['a']);
    const next=Buffer.from(JSON.stringify({...original,name:'次の構成'}));
    await upload('次の構成.json',next);
    const prompt=page.getByRole('dialog',{name:'未保存の変更があります'});
    await prompt.getByRole('button',{name:'キャンセル'}).click();
    await prompt.waitFor({state:'hidden'});
    assert.deepEqual(await enclosed(page),['a']);
    assert.equal(await page.locator('#unsaved').isVisible(),true);
    await upload('次の構成.json',next);
    const pending=page.waitForEvent('download');
    await prompt.getByRole('button',{name:'JSONを保存',exact:true}).click();
    const download=await pending,chunks=[];
    for await(const chunk of await download.createReadStream()) chunks.push(chunk);
    const saved=Buffer.concat(chunks);
    assert.deepEqual(JSON.parse(saved),{...original,userScope:['a']});
    assert.equal(await prompt.isVisible(),true);
    assert.equal(await page.locator('#unsaved').isVisible(),true);
    assert.equal(await page.locator('#name').innerText(),original.name);
    await prompt.getByRole('button',{name:'切り替える',exact:true}).click();
    await page.getByRole('heading',{name:'次の構成',exact:true}).waitFor();
    assert.equal(await page.locator('#unsaved').isVisible(),false);
    await upload(download.suggestedFilename(),saved);
    await page.getByText('読み込み元　'+download.suggestedFilename(),{exact:true}).waitFor();
    assert.deepEqual(await enclosed(page),['a']);
    await page.close();console.log('PASS 利用者の関与範囲の囲み・編集・保存・復元 '+width+'px');
  }
  const page=await browser.newPage({acceptDownloads:false});
  await page.goto(base+'/architecture');
  const web=page.locator('.node[data-id="web"]');
  await web.waitFor();
  await page.locator('#scope-toggle').click();
  await web.focus();await page.keyboard.press('Enter');
  const count=await page.locator('#scope-count').innerText();
  const blocked=page.waitForEvent('download');
  await page.locator('#save-map').click();
  assert.ok(await (await blocked).failure());
  assert.equal(await page.locator('#unsaved').isVisible(),true);
  const next={schema:'aym.architecture/v1',name:'別図',nodes:[{id:'next',name:'次',kind:'service'}],connections:[]};
  await page.locator('#file').setInputFiles({name:'next.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(next))});
  const guard=page.getByRole('dialog',{name:'未保存の変更があります'});
  await guard.getByRole('button',{name:'キャンセル'}).click();
  await guard.waitFor({state:'hidden'});
  assert.equal(await page.locator('#scope-count').innerText(),count);
  assert.equal(await page.locator('#name').innerText(),'注文管理サービス');
  await web.focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('#confirm-saved').isVisible(),false);
  assert.equal(await page.locator('#unsaved').isVisible(),true);
  await page.close();console.log('PASS 通常保存のダウンロード拒否後も編集を保持');
}finally{await browser.close();}

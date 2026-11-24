import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base=process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';
const browser=await chromium.launch({headless:true,channel:process.env.AYM_BROWSER_CHANNEL || undefined});
const upload=(name,text)=>({name,mimeType:'text/plain',buffer:Buffer.from(text)});
try{
  for(const width of [1440,390]){
    const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce',deviceScaleFactor:process.env.AYM_TRIAGE_CAPTURE?2:1});
    const errors=[],requests=[];
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('request',r=>requests.push(r.url()));
    await page.goto(base+'/');
    await page.getByRole('link',{name:'システム構成図',exact:true}).click();
    const selected=page.locator('.node.selected');
    await selected.waitFor();
    assert.equal(await page.locator('.node').count(),6);
    const peg=page.locator('.user-peg');
    assert.equal(await peg.count(),1);
    assert.equal(await page.locator('[data-id="web"] .user-peg text').textContent(),'利用者');
    assert.match(await page.locator('[data-id="web"]').getAttribute('aria-label'),/利用者が操作/);
    assert.equal(await page.locator('#detail-name').innerText(),'注文控えの保管庫');
    assert.match(await page.locator('#findings').innerText(),/REC-04/);
    await page.locator('#nearby').check();
    assert.equal(await page.locator('.node').count(),2);
    assert.equal(await peg.count(),0);
    await page.locator('#nearby').uncheck();
    await page.locator('#repos').click();
    assert.equal(await page.locator('.node').count(),3);
    assert.equal(await page.locator('[data-id="web"] .user-peg').count(),1);
    await page.locator('[data-id="web"]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#detail-name').innerText(),'orders-web');
    assert.match(await page.locator('#links').innerText(),/型定義に依存/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    const cloud='Resources:\n  Receipts:\n    Type: AWS::S3::Bucket\n    Properties:\n      VersioningConfiguration: {Status: Suspended}\n  Worker:\n    Type: AWS::Lambda::Function\n    Properties:\n      Bucket: !Ref Receipts';
    const countBefore=requests.length;
    const own={schema:'aym.architecture/v1',name:'請求書管理',nodes:[
      {id:'billing',kind:'repository',name:'billing-api'},{id:'audit',kind:'repository',name:'audit-log'},
      {id:'portal',kind:'service',name:'利用者ポータル'}],connections:[],
      findings:[['billing','low','担当者の更新'],['billing','high','認証設定の確認'],
        ['billing','medium','監査ログの保存期間'],['billing','critical','請求書の閲覧権限'],
        ['billing','high','共有権限の確認'],['audit','info','構成の記録']]
        .map(([node,severity,title])=>({node,severity,title,evidence:'引き継ぎ記録 12',action:'権限台帳を確認する'}))};
    await page.locator('#file').setInputFiles(upload('own-map.json',JSON.stringify(own)));
    await page.getByRole('heading',{name:'請求書管理',exact:true}).waitFor();
    assert.match(await page.locator('#findings').innerText(),/引き継ぎ記録 12/);
    const billing=page.locator('.node[data-id="billing"]'), badges=billing.locator('.severity-badge');
    const triage=['Critical 1','High 2','Medium 1','Low 1'];
    assert.deepEqual(await badges.locator('text').allTextContents(),triage);
    assert.deepEqual(await page.locator('.finding .level').allTextContents(),['Critical','High','High','Medium','Low']);
    assert.equal(await billing.getAttribute('data-severity'),'critical');
    const colors={'critical':'rgb(32, 33, 36)','high':'rgb(180, 35, 54)','medium':'rgb(245, 205, 66)','low':'rgb(38, 115, 71)'};
    for(const [severity,color] of Object.entries(colors)){
      assert.equal(await billing.locator('.severity-badge[data-severity="'+severity+'"] rect')
        .evaluate(el=>getComputedStyle(el).fill),color);
      assert.equal(await page.locator('.finding[data-severity="'+severity+'"] .level').first()
        .evaluate(el=>getComputedStyle(el).backgroundColor),color);
    }
    const within=await billing.evaluate(node=>{
      const box=node.querySelector('.box').getBoundingClientRect();
      return [...node.querySelectorAll('.severity-badge')].every(badge=>{
        const r=badge.getBoundingClientRect();
        return r.left>=box.left && r.right<=box.right && r.top>=box.top && r.bottom<=box.bottom;
      });
    });
    assert.ok(within,'すべての重大度と件数がカードに収まる');
    for(const label of triage) assert.ok((await billing.getAttribute('aria-label')).includes(label));
    assert.equal(await page.locator('[data-id="portal"] .severity-badge').count(),0);
    assert.deepEqual(await page.locator('[data-id="audit"] .severity-badge text').allTextContents(),['情報 1']);
    if(width===1440 && process.env.AYM_TRIAGE_CAPTURE){
      await page.locator('#fit-map').click();
      await page.locator('#frame').scrollIntoViewIfNeeded();
      const clip=await billing.evaluate(el=>{
        const r=el.getBoundingClientRect();
        return {x:r.x-8,y:r.y-8,width:r.width+16,height:r.height+16};
      });
      await page.screenshot({path:process.env.AYM_TRIAGE_CAPTURE,clip});
    }
    await page.locator('#file').setInputFiles(upload('own.yaml',cloud));
    await page.getByRole('heading',{name:'own.yaml',exact:true}).waitFor();
    assert.equal(await page.locator('.node').count(),2);
    assert.match(await page.locator('#findings').innerText(),/Resources.Receipts.Properties.VersioningConfiguration/);
    await page.locator('#file').setInputFiles(upload('broken.json','{bad'));
    await page.locator('#error').waitFor();
    assert.equal(await page.locator('#name').innerText(),'own.yaml');
    for(const pkg of [{name:'web',repository:'team/web',dependencies:{api:'^1'}},{name:'api',repository:'team/api'}]){
      await page.locator('#file').setInputFiles(upload('package.json',JSON.stringify(pkg)));
      await page.getByText('読み込み元　package.json '+(pkg.name==='web'?1:2)+' 件',{exact:true}).waitFor();
    }
    assert.equal(await page.locator('.node').count(),2);
    assert.equal(await page.locator('.edge').count(),1);
    await page.locator('.wire').focus();await page.keyboard.press('Enter');
    await page.getByLabel('利用者の入力経路').selectOption('internal');
    await page.locator('#file').setInputFiles(upload('package.json',JSON.stringify({name:'shared',repository:'team/shared'})));
    await page.getByText('読み込み元　package.json 3 件',{exact:true}).waitFor();
    assert.equal(await page.locator('.wire').getAttribute('data-input'),'internal');
    assert.equal(requests.length,countBefore,'ファイルの読み込みによるHTTPリクエストがない');
    await page.locator('#reset').click();
    const prompt=page.getByRole('dialog',{name:'未保存の変更があります'});
    await prompt.waitFor();
    await page.keyboard.press('Escape');
    await prompt.waitFor({state:'hidden'});
    assert.equal(await page.locator('.wire').getAttribute('data-input'),'internal');
    assert.equal(await page.locator('#unsaved').isVisible(),true);
    await page.locator('#reset').click();
    await prompt.getByRole('button',{name:'保存せず切り替える'}).click();
    await page.getByRole('heading',{name:'注文管理サービス'}).waitFor();
    assert.equal(await page.locator('#unsaved').isVisible(),false);
    assert.equal(await selected.getAttribute('data-id'),'archive');
    assert.deepEqual(errors,[]);
    await page.close();
    console.log('PASS 構成図、リポジトリ、指摘、ファイル入力、キーボード '+width+'px');
  }
}finally{await browser.close();}

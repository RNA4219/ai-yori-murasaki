import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base=process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';
const browser=await chromium.launch({headless:true,channel:process.env.AYM_BROWSER_CHANNEL || undefined});
const upload=(name,text)=>({name,mimeType:'text/plain',buffer:Buffer.from(text)});
try{
  for(const width of [1440,390]){
    const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});
    const errors=[],requests=[];
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('request',r=>requests.push(r.url()));
    await page.goto(base+'/');
    await page.getByRole('link',{name:'システム構成図',exact:true}).click();
    const selected=page.locator('.node.selected');
    await selected.waitFor();
    assert.equal(await page.locator('.node').count(),6);
    assert.equal(await page.locator('#detail-name').innerText(),'注文控えの保管庫');
    assert.match(await page.locator('#findings').innerText(),/REC-04/);
    await page.locator('#nearby').check();
    assert.equal(await page.locator('.node').count(),2);
    await page.locator('#nearby').uncheck();
    await page.locator('#repos').click();
    assert.equal(await page.locator('.node').count(),3);
    await page.locator('[data-id="web"]').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#detail-name').innerText(),'orders-web');
    assert.match(await page.locator('#links').innerText(),/型定義に依存/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    const cloud='Resources:\n  Receipts:\n    Type: AWS::S3::Bucket\n    Properties:\n      VersioningConfiguration: {Status: Suspended}\n  Worker:\n    Type: AWS::Lambda::Function\n    Properties:\n      Bucket: !Ref Receipts';
    const countBefore=requests.length;
    const own={schema:'aym.architecture/v1',name:'請求書管理',nodes:[{id:'billing',kind:'repository',name:'billing-api'}],
      connections:[],findings:[{node:'billing',severity:'low',title:'担当者の更新',evidence:'引き継ぎ記録 12',action:'担当者を登録する'}]};
    await page.locator('#file').setInputFiles(upload('own-map.json',JSON.stringify(own)));
    await page.getByRole('heading',{name:'請求書管理',exact:true}).waitFor();
    assert.match(await page.locator('#findings').innerText(),/引き継ぎ記録 12/);
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
    assert.equal(requests.length,countBefore,'ファイルの読み込みによるHTTPリクエストがない');
    await page.locator('#reset').click();
    await page.getByRole('heading',{name:'注文管理サービス'}).waitFor();
    assert.equal(await selected.getAttribute('data-id'),'archive');
    assert.deepEqual(errors,[]);
    await page.close();
    console.log('PASS 構成図、リポジトリ、指摘、ファイル入力、キーボード '+width+'px');
  }
}finally{await browser.close();}

import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.AYM_BROWSER_URL || 'http://127.0.0.1:8765';
const response = await fetch(base + '/api/campaigns');
assert.equal(response.status, 200);
const campaigns = (await response.json()).campaigns;
assert.ok(campaigns.length >= 1);
const cid = campaigns[0].campaign_id;
assert.match(cid, /^[a-z0-9][a-z0-9-]{0,63}$/);
const runResponse = await fetch(base + '/api/campaigns/' + cid + '/runs');
assert.equal(runResponse.status, 200);
const runs = (await runResponse.json()).runs;
assert.ok(runs.length > 0, 'campaign has an initialization run');
const rid = runs[0].run_id;

const browser = await chromium.launch({headless:true,channel:process.env.AYM_BROWSER_CHANNEL || undefined});
try {
  for(const width of [1440,768,390]) {
    const context = await browser.newContext({viewport:{width,height:900}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    try {
      await page.goto(base + '/decision');
      await page.getByRole('heading',{name:'確認する案件を選ぶ'}).waitFor({timeout:15000});
      const candidate=page.locator('#campaign-list a[href="/decision?campaign='+cid+'"]');
      await candidate.waitFor({timeout:15000});
      assert.equal(await page.locator('#view-switch [aria-current="page"]').count(),1);
      assert.equal(await page.locator('#to-monitor').getAttribute('href'),'/#/');
      await candidate.click();
      await page.waitForURL('**/decision?campaign='+cid);
      await page.locator('#h-sum').waitFor({state:'visible'});
      assert.match(await page.locator('#h-sum').innerText(),/確かめていないことがあります/);
      assert.match(await page.locator('#sum-lead').innerText(),/評価の記録がありません/);
      assert.equal(await page.locator('#to-monitor').getAttribute('href'),' /#/campaign/'.trim()+cid);
      assert.equal(await page.locator('#progress-text').count(),0);
      assert.equal(await page.locator('#bar').count(),0);
      await page.locator('#related-cases').click();
      await page.waitForURL('**/#/relations/'+cid);
      await page.locator('#view-root .relation-case').first().waitFor();
      assert.equal(await page.locator('#switch-to-decision').getAttribute('href'),'/decision?campaign='+cid);
      await page.goBack();
      await page.locator('#h-sum').waitFor();
      await page.locator('#to-monitor').click();
      await page.waitForURL('**/#/campaign/'+cid);
      assert.equal(await page.locator('#switch-to-decision').getAttribute('href'),'/decision?campaign='+cid);
      await page.locator('#switch-to-decision').click();
      await page.waitForURL('**/decision?campaign='+cid);
      await page.goto(base + '/#/run/' + rid);
      await page.waitForFunction(expected =>
        document.getElementById('switch-to-decision')?.getAttribute('href') === expected,
        '/decision?campaign='+cid,{timeout:15000});

      // Mock only the list payload to test the assessment display and fold.
      const mock = {
        schema:'aym.decision-view/1',generated_at:'2026-11-16T00:00:00Z',
        campaign:{id:cid,name:'INT-DUAL-BROWSER',status:'review_pending',created_at:'2026-11-16T00:00:00Z'},
        previous:null,removed:[],rules:{},freshness:{},assessment:{
          present:true,historical:false,coverage:{executed:3,not_executed:2,failed:1}},
        items:[{key:'internal-finding-abc',ref:'hidden-9001',title:'確認が必要な課題',
          state:'done',state_label:'修正済',assessment_status:'confirmed',
          severity:'high',due:null,link:null,kev:false,kev_cves:[],epss_max:null,cve_count:0}]
      };
      await page.route('**/api/campaigns/'+cid+'/decision-view',r=>r.fulfill({
        status:200,contentType:'application/json',body:JSON.stringify({decision_view:mock})}));
      const observedAt = '2026-11-20T00:00:00Z';
      const aligned = {
        finding_id:'reviewed-item', title:'通知ルールを照合', source_status:'完了', classification:'aligned',
        rules_used:['audit-setting'], rules_unfiltered:false, excluded_resource_count:1,
        collected_at:[observedAt], matches:[{id:'node-a', type:'node', method:'id', in_inventory:true,
          detections:[{id:'notice-a', rule:'audit-setting', status:'resolved', updated_at:observedAt}]}]
      };
      const uncertain = {
        ...aligned, finding_id:'unverified-item', title:'範囲外の種類を照合', source_status:'未対応',
        classification:'uncomparable', reason:'no_detections', rules_used:[], rules_unfiltered:true,
        matches:[{id:'disk-a', type:'disk', method:'id', in_inventory:null, detections:[]}]
      };
      const legacy = {
        finding_id:'legacy-item', title:'以前の照合結果', source_status:'未対応', classification:'open_absent',
        collected_at:[observedAt], matches:[{id:'archive-a', type:null, method:'id', in_inventory:false, detections:[]}]
      };
      let driftReport = {
        campaign_id:cid, observations:[{kind:'inventory',collected_at:observedAt}],
        counts:{open_absent:1, open_resolved:0, closed_active:0, aligned:1, uncomparable:1},
        reasons:{no_detections:1}, items:[aligned, uncertain, legacy]
      };
      await page.route('**/api/campaigns/'+cid+'/drift', route => route.fulfill(
        driftReport ? {json:driftReport} : {status:503, body:'unavailable'}
      ));
      await page.goto(base+'/decision?campaign='+cid);
      await page.locator('#h-sum').waitFor({state:'visible'});
      assert.match(await page.locator('#h-sum').innerText(),/終わっていない課題は全部で 1 件です/);
      assert.match(await page.locator('#sum-lead').innerText(),/検査していない範囲が 3 類型あります。安全とは限りません/);
      assert.equal((await page.locator('main').innerText()).includes('internal-finding-abc'),false);
      assert.match(await page.locator('#now .item-action').first().innerText(),/修正案を確認してください/);
      await page.locator('#now .item > summary').first().click();
      await page.locator('#now .item .item-more').first().waitFor();
      assert.equal(await page.getByText('internal-finding-abc').first().isVisible(),false);
      await page.locator('#now .item details').first().evaluate(e=>{e.open=true;});
      assert.equal(await page.getByText('internal-finding-abc').first().isVisible(),true);
      assert.equal(await page.locator('#now .item a').first().getAttribute('href'),'/#/campaign/'+cid);
      // 修正後に失敗した課題は両画面で同じ次の行動を案内する。
      mock.items[0].assessment_status = 'retest_failed';
      mock.previous = {id:'previous-case',created_at:'2026-11-13T00:00:00Z'};
      mock.items[0].change = 'new';
      mock.items.push({...mock.items[0],key:'B',title:'継続した課題B',assessment_status:'retested',change:'unchanged'});
      mock.removed = [{key:'A',title:'前回だけの課題A',ref:null,prev_state:'open',prev_state_label:'未対応'}];
      await page.locator('#refresh-view').click();
      await page.locator('#now .item-action').getByText(/失敗した検査を確認し、修正内容を見直して/).waitFor();
      assert.match(await page.locator('#h-sum').innerText(), /終わっていない課題は全部で 1 件/);
      const changes = page.locator('#changes');
      const disappeared = changes.locator('details').filter({hasText:'今回の記録にない課題'});
      await disappeared.locator('summary').click();
      await disappeared.getByText('前回だけの課題A（前は 未対応）',{exact:true}).waitFor();
      assert.match(await changes.locator('summary').filter({hasText:'片付いた'}).innerText(), /なし/);
      assert.match(await changes.locator('summary').filter({hasText:'新しく入った'}).innerText(), /1 件/);
      await page.route('**/api/campaigns/'+cid+'/assessment', route => route.fulfill({json:{
        report:{status:'ready',coverage:{categories:[]},findings:[{finding_id:'failed-retest',
          title:mock.items[0].title,remediation:{proposal_ref:'a'},validation:{result:'failed',
            checks:{benign_continued:false}}}]}
      }}));
      await page.locator('#to-monitor').click();
      await page.getByRole('button',{name:'評価',exact:true}).click();
      const finding = page.locator('[data-key="assess-finding-failed-retest"]');
      await finding.locator(':scope > summary .badge').filter({hasText:'再検証失敗'}).waitFor();
      await finding.locator(':scope > summary').click();
      await finding.getByText('失敗した検査を確認し、修正内容を見直してください。',{exact:true}).waitFor();
      await finding.getByText('技術詳細',{exact:true}).click();
      assert.match(await finding.getByRole('row').filter({hasText:'正常動作継続'}).innerText(), /条件未達/);
      await page.locator('#switch-to-decision').click();
      await page.locator('#now .item-action').getByText(/失敗した検査を確認し、修正内容を見直して/).waitFor();
      const size=await page.locator('#to-monitor').evaluate(e=>e.getBoundingClientRect().height);
      assert.ok(size>=44,'switch target >= 44px, got '+size);
      await page.keyboard.press('Tab');
      assert.ok(await page.evaluate(()=>document.activeElement?.tagName),'keyboard focus');
      const dims=await page.evaluate(()=>({
        width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
      assert.ok(dims.scroll<=dims.width+2,'overflow at '+width+'px '+JSON.stringify(dims));
      await page.locator('#q').fill('存在しない課題');
      await page.locator('#jump a[href="#sec-all"]').click();
      assert.equal(await page.locator('#q').inputValue(),'');
      assert.equal(await page.locator('#f-any').getAttribute('aria-pressed'),'true');
      assert.equal(await page.locator('#all .item').count(),mock.items.length);
      await page.locator('#drift-list > summary').click();
      await page.locator('#drift-candidates .item > summary').click();
      await page.locator('#drift-candidates').getByText('一覧に無し',{exact:true}).waitFor();
      await page.locator('#drift-others-list > summary').click();
      const reviewedRow = page.locator('#drift-others .item').filter({hasText:aligned.title});
      await reviewedRow.locator('summary').focus();
      await page.keyboard.press('Enter');
      await reviewedRow.getByText('指定したルールのみ',{exact:true}).waitFor();
      await reviewedRow.getByText('audit-setting',{exact:true}).waitFor();
      await reviewedRow.getByText('1 件',{exact:true}).waitFor();
      const uncertainRow = page.locator('#drift-others .item').filter({hasText:uncertain.title});
      await uncertainRow.locator('summary').click();
      for (const label of ['絞り込みなし','該当する検知なし','disk','未確認']) {
        await uncertainRow.getByText(label,{exact:true}).waitFor();
      }

      const scope = page.locator('#product-verification');
      await scope.locator('summary').focus();
      await page.keyboard.press('Enter');
      await scope.getByRole('table',{name:'製品検収で確認した範囲（2026年11月8日時点）'}).waitFor();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2));
      const rows = scope.getByRole('table').locator('tbody tr');
      assert.deepEqual(await rows.evaluateAll(entries => entries.map(row =>
        Array.from(row.cells, cell => cell.textContent))), [
          ['認可不備','確認済み','確認済み'],
          ['SQLインジェクション','確認済み','未検証'],
          ['XSS','確認済み','未検証'],
          ['パストラバーサル','確認済み','未検証'],
          ['既知依存脆弱性','確認済み','未検証']
        ]);
      const sources = await scope.getByRole('link').evaluateAll(links => links.map(link => link.href));
      assert.ok(sources.some(link => link.endsWith('/docs/acceptance/AC-20261107-03.md')));
      assert.ok(sources.some(link => link.endsWith('/docs/acceptance/AC-20261108-01.md')));

      // 取込前と、観測を取り込んでずれが0件の状態を、同じ案件で読み分ける。
      const recorded = {...driftReport, items:[aligned], counts:{
        open_absent:0, open_resolved:0, closed_active:0, aligned:1, uncomparable:0}, reasons:{}};
      driftReport = {...recorded, observations:[], items:[uncertain],
        counts:{...recorded.counts, aligned:0, uncomparable:1}, reasons:{out_of_scope:1}};
      await page.locator('#refresh-view').click();
      await page.locator('#sec-drift').getByText('観測未取込・照合待ち 1 件',{exact:true}).waitFor();
      assert.match(await page.locator('#sec-drift').innerText(), /資源一覧または検知一覧を取り込んで/);
      assert.equal(await page.locator('#drift-others-list').isVisible(),false);
      driftReport = recorded;
      await page.locator('#refresh-view').click();
      await page.locator('#sec-drift').getByText('ずれの候補 0 件',{exact:true}).waitFor();
      assert.doesNotMatch(await page.locator('#sec-drift').innerText(), /観測未取込/);
      driftReport = null;
      await page.locator('#refresh-view').click();
      await page.getByText(/課題と観測のずれを取得できません/).waitFor();
      assert.equal(await page.locator('#sec-drift').isVisible(),false);

      // 公開済みの画面を読み直しても、非公開応答の後に以前の件数を残さない。
      await page.route('**/api/campaigns/'+cid+'/decision-view',
        route=>route.fulfill({json:{decision_view:{status:'withheld'}}}));
      await page.locator('#refresh-view').click();
      await page.getByText('評価情報はBlueに非公開です。').waitFor();
      assert.equal(await page.locator('#view').isVisible(),false);
      await page.locator('#notes a').click();
      await candidate.waitFor();

      await page.goto(base+'/');
      const evidence=page.locator('#case-sheet .action-main');
      await evidence.waitFor();
      assert.match(await page.locator('.sheet-meta').innerText(),/最終実行/);
      const count=await page.locator('.sheet-number').boundingBox();
      assert.ok(count && count.x>=0 && count.x+count.width<=width);
      const environment=page.locator('details.environment');
      await environment.locator('summary').click();
      await Promise.all([
        page.waitForResponse(r=>r.url()===base+'/api/campaigns?page=1'),
        page.locator('#refresh-btn').click()
      ]);
      await page.waitForFunction(()=>!document.querySelector('#refresh-btn').hasAttribute('data-manual-busy'));
      assert.equal(await environment.evaluate(node=>node.open),true);

      // 案件一覧の先頭が空でも、記録のある案件から読み始められる。
      const otherId='case-with-findings';
      const fullCase={...campaigns[0],campaign_id:otherId,intent_id:'権限の棚卸し',finding_count:2};
      await page.route('**/api/campaigns?page=1',r=>r.fulfill({json:{campaigns:[{...campaigns[0],finding_count:0},fullCase]}}));
      await page.route('**/api/campaigns/'+cid,r=>r.fulfill({json:{campaign:{campaign_id:cid},findings:[]}}));
      const example={campaign:fullCase,findings:[{title:'管理者の登録を確認する',severity:'high'},{title:'担当者の異動日を確認する',severity:'low'}]};
      await page.route('**/api/campaigns/'+otherId,r=>r.fulfill({json:example}));
      await page.reload();
      const visibleFinding=page.getByRole('heading',{name:example.findings[0].title});
      await visibleFinding.waitFor();
      assert.match(await page.locator('.excerpt-list').innerText(),/担当者の異動日/);
      assert.equal(await evidence.getAttribute('href'),'#/campaign/'+otherId);
      async function selectCase(id) {
        if (width < 768) await page.locator('#case-choice').selectOption(id);
        else await page.locator('button[data-select-case="'+id+'"]').click();
      }
      await selectCase(cid);
      await page.getByText('この案件に登録された検知項目はまだありません。').waitFor();
      assert.equal(await visibleFinding.count(),0);
      assert.equal(await page.locator('.action-secondary').getAttribute('href'),'/decision?campaign='+cid);
      // 選択中に届いた以前の案件の応答で、空の案件の表示が戻らない。
      let deliverRequest;
      const delayedResponse = new Promise(resolve => { deliverRequest=resolve; });
      await page.route('**/api/campaigns/'+otherId, route=>deliverRequest(route), {times:1});
      await selectCase(otherId);
      const oldRequest = await delayedResponse;
      await selectCase(cid);
      await page.getByText('この案件に登録された検知項目はまだありません。').waitFor();
      await oldRequest.fulfill({json:example});
      assert.equal(await visibleFinding.count(),0);
      await selectCase(otherId);
      await visibleFinding.waitFor();
      await page.route('**/api/campaigns/'+otherId,r=>r.fulfill({status:503,json:{}}),{times:1});
      await page.locator('#refresh-btn').click();
      await page.getByText('更新できませんでした。前回取得した検知記録を表示しています。').waitFor();
      assert.equal(await visibleFinding.isVisible(),true);
      await page.waitForFunction(()=>!document.querySelector('#refresh-btn').hasAttribute('data-manual-busy'));
      await page.route('**/api/campaigns/'+otherId,r=>r.fulfill({status:403,json:{}}));
      await page.locator('#refresh-btn').click();
      await page.getByText('この案件の検知記録を表示する権限がありません。').waitFor();
      assert.equal(await visibleFinding.count(),0);
      const series = [
        {campaign_id:'round-three',intent_id:'権限点検-20261222',created_at:'2026-12-22',previous_id:'round-two'},
        {campaign_id:cid,intent_id:'権限点検',created_at:'2026-10-01',previous_id:null},
        {campaign_id:'round-two',intent_id:'権限点検-20261122',created_at:'2026-11-22',previous_id:cid},
        {campaign_id:'standalone',intent_id:'ログ保管',created_at:'2026-11-20',previous_id:null}
      ];
      await page.route('**/api/campaign-relations',r=>r.fulfill({json:{campaigns:series}}));
      await page.locator('#desk-relations').click();
      const history=page.locator('.history-sheet');
      const steps=history.getByRole('link');
      await history.waitFor();
      assert.deepEqual(await steps.evaluateAll(links=>links.map(a=>a.getAttribute('href'))),
        [cid,'round-two','round-three'].map(id=>'#/campaign/'+id));
      assert.deepEqual(await history.locator('.history-phase').allTextContents(),['初回','2回目','3回目']);
      const markers=await history.locator('.history-step').evaluateAll(nodes=>nodes.map(node=>{
        const dot=node.querySelector('.history-dot').getBoundingClientRect();
        const line=getComputedStyle(node,'::before');
        return {x:dot.x,y:dot.y,horizontal:line.borderTopWidth,vertical:line.borderLeftWidth};
      }));
      for (let i=1;i<markers.length;i++) {
        const prev=markers[i-1], next=markers[i];
        if (width>=800) assert.ok(prev.x<next.x && prev.y===next.y && parseFloat(prev.horizontal)>0);
        else assert.ok(prev.y<next.y && prev.x===next.x && parseFloat(prev.vertical)>0);
      }
      assert.match(await page.locator('.relation-single').innerText(),/ログ保管/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await steps.first().click();
      await page.locator('.detail-head').waitFor();
      await page.goBack();
      await history.waitFor();
      await page.goto(base+'/#/relations/round-two');
      await history.locator('[aria-current="true"]').waitFor();
      assert.equal(await steps.count(),3);
      assert.equal(await history.locator('[aria-current="true"]').getAttribute('href'),'#/campaign/round-two');
      assert.equal(await page.locator('.relation-single').count(),0);
      const branch={campaign_id:'other-round',intent_id:'権限点検-20261122B',created_at:'2026-11-22',previous_id:cid};
      await page.route('**/api/campaign-relations',r=>r.fulfill({json:{campaigns:[branch,...series]}}));
      await page.locator('#refresh-btn').click();
      const branchLink=history.locator('.history-branch .relation-case');
      await branchLink.waitFor();
      assert.equal(await steps.count(),4);
      assert.equal(await branchLink.getAttribute('href'),'#/campaign/other-round');
      assert.equal(await branchLink.locator('xpath=ancestor::ol[1]/parent::li').getAttribute('data-key'),'step-'+cid);
      await page.waitForFunction(()=>!document.querySelector('#refresh-btn').hasAttribute('data-manual-busy'));
      await page.route('**/api/campaign-relations',r=>r.fulfill({json:{status:'withheld'}}));
      await page.locator('#refresh-btn').click();
      await page.getByText('案件のつながりはこの表示では非公開です。').waitFor();
      assert.equal(await history.count(),0);
      await page.route('**/api/campaigns?page=*', route => {
        const second = new URL(route.request().url()).searchParams.get('page') === '2';
        const first = second ? 50 : 0;
        const rows = Array.from({length:second ? 1 : 50}, (_, i) => ({
          ...campaigns[0], campaign_id:'page-case-'+(first+i), finding_count:0, finding_excerpt:[]
        }));
        return route.fulfill({json:{campaigns:rows,page:second ? 2 : 1,pages:2,total:51,checked_at:'2026-11-23T00:00:00Z'}});
      });
      await page.goto(base+'/');
      await page.getByRole('button',{name:'次へ',exact:true}).click();
      await page.getByText('2 / 2 ページ',{exact:true}).waitFor();
      assert.equal(await page.locator('[data-select-case]').count(),1);
      await page.getByRole('button',{name:'前へ',exact:true}).click();
      await page.getByText('1 / 2 ページ',{exact:true}).waitFor();
      assert.equal(await page.locator('[data-select-case]').count(),50);
      assert.deepEqual(errors,[],'no JavaScript errors');
      console.log('PASS real Chromium '+width+'px: routes, accessibility, state and layout');
    } finally {
      await context.close();
    }
  }
} finally {
  await browser.close();
}

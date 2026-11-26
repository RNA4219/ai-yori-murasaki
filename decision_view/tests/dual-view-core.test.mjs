import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const html=readFileSync('ai_yori_murasaki/web/decision.html','utf8');
const match=html.match(/<script id="decision-core">([\s\S]*?)<\/script>/);
assert.ok(match,'decision-core script');
const box={URL};box.globalThis=box;vm.runInNewContext(match[1],box);
const D=box.AYMDecision;
function item(o={}) { return {key:'x',title:'sample',state:'other',severity:'high',kev:false,kev_cves:[],due:null,epss_max:null,...o};}
function view(items,extra={}){return {schema:'aym.decision-view/1',campaign:{id:'abc',name:'example'},items,rules:{},previous:null,removed:[],freshness:{},...extra};}

test('unknown is unfinished and no false all-clear',()=>{
  const a=D.summarize(view([item()]),'2026-11-16');
  assert.equal(a.counts.active,1);
});
test('assessment supersedes imported completion',()=>{
  const a=D.assess(item({state:'done',assessment_status:'confirmed'}),'2026-11-16',D.DEFAULT_RULES);
  assert.equal(a.active,true); assert.equal(a.tier,'now');
});
test('retested and refuted closed; historical never closed',()=>{
  for (const st of ['retested','refuted']) assert.equal(D.assess(item({assessment_status:st}),'2026-11-16',D.DEFAULT_RULES).active,false);
  assert.equal(D.assess(item({state:'done',assessment_status:'historical'}),'2026-11-16',D.DEFAULT_RULES).active,true);
});
test('assessment labels and action semantics',()=>{
  const states=['confirmed','candidate','refuted','retested','retest_failed','proposed','undetermined','historical','unknown'];
  for(const st of states) {assert.ok(D.stateLabel(item({assessment_status:st})).length);assert.ok(D.nextActionFor(st).endsWith('。'));}
  assert.equal(D.stateLabel(item({state:'waiting',assessment_status:null})),'未評価');
});
test('T53: failed validation needs a decision even if the source was closed',()=>{
  const failure=item({state:'done',assessment_status:'retest_failed'});
  assert.equal(D.stateLabel(failure),'再検証失敗');
  assert.match(D.nextActionFor(failure.assessment_status),/失敗した検査.*修正内容/);
  const {counts}=D.summarize(view([failure]),'2026-11-16');
  assert.deepEqual([counts.active,counts.closed,counts.now],[1,0,1]);
});
test('urgent by assessment status, not severity alone',()=>{
  assert.equal(D.assess(item({assessment_status:'proposed',severity:'low'}),'2026-11-16',D.DEFAULT_RULES).tier,'now');
  assert.equal(D.assess(item({assessment_status:'candidate',severity:'critical'}),'2026-11-16',D.DEFAULT_RULES).tier,null);
});
test('no progress bars, percentages, unsafe raw ID in card metadata',()=>{
  assert.doesNotMatch(html,/id="progress-text"|id="bar"|class="progress"/);
  assert.match(html,/技術の詳細/);
  assert.match(html,/id="view-switch"/);
  assert.match(html,/id="campaign-picker"/);
  assert.match(html,/読み直す/);
});
test('monitor switcher source supports campaign and run routes',()=>{
  const monitor=readFileSync('ai_yori_murasaki/web/index.html','utf8');
  assert.match(monitor,/updateDecisionSwitch/);
  assert.match(monitor,/state\.detail\.data\.run\.campaign_id/);
  assert.match(monitor,/switch-to-decision/);
});

test('T38: decision HTML does not reproduce operator-only APIs or controls',()=>{
  for(const forbidden of ['/api/runs/','/api/approvals','/api/verification/','/rsi','承認する','実行する']){
    assert.equal(html.includes(forbidden),false,'unexpected operator API/control '+forbidden);
  }
  for(const required of ['/api/health','/api/campaigns','/decision-view']){
    assert.equal(html.includes(required),true,'missing read API '+required);
  }
});

test('T41: Python assessment status derives exactly as the monitor JS on shared inputs', async()=>{
  const monitor = readFileSync('ai_yori_murasaki/web/index.html','utf8');
  const extract=(name)=>{
    const start=monitor.indexOf('function '+name+'(');
    assert.ok(start>=0,name+' missing');
    const begin=monitor.indexOf('{',start), end=monitor.indexOf('\n  }',begin)+4;
    assert.ok(end>begin);
    return monitor.slice(start,end);
  };
  const ctx = {};
  vm.runInNewContext(extract('deriveFindingStatus') + '\n' + extract('nextActionFor') +
    '\nthis.deriveFindingStatus=deriveFindingStatus;this.nextActionFor=nextActionFor;',ctx);
  const cases=[
    [{claims:[{status:'confirmed'}]},false],
    [{claims:[{status:'hypothesis'}]},false],
    [{claims:[{status:'refuted'}]},false],
    [{validation:{result:'success'}},false],
    [{validation:{result:'failed'},remediation:{proposal_ref:'x'}},false],
    [{validation:{result:'failed'},remediation:{proposal_ref:'x'}},true],
    [{remediation:{proposal_ref:'x'}},false],
    [{validation:{result:'undetermined'}},false],
    [{},true],
    [{},false],
    [{claims:[{status:'confirmed'}],remediation:{proposal_ref:'x'}},false],
    [{classification:{cve:{status:'candidate'}}},false],
    [{validation:{result:'success'}},true]
  ];
  const monitorResults=cases.map(([finding,historical])=>ctx.deriveFindingStatus(finding,historical));
  const {spawnSync}=await import('node:child_process');
  const pyCode='import json,sys\nfrom ai_yori_murasaki.decision_view import derive_assessment_status\ncases=json.load(sys.stdin)\nprint(json.dumps([derive_assessment_status(f,h) for f,h in cases]))';
  const py=spawnSync('python',['-c',pyCode],{input:JSON.stringify(cases),encoding:'utf8'});
  assert.equal(py.status,0,py.stderr);
  assert.deepEqual(JSON.parse(py.stdout),monitorResults);
  for(const status of ['confirmed','candidate','refuted','retested','retest_failed','proposed','undetermined','historical','unknown']){
    assert.equal(D.nextActionFor(status),ctx.nextActionFor(status));
  }
});

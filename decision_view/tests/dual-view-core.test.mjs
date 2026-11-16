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
test('eight status labels and action semantics',()=>{
  const states=['confirmed','candidate','refuted','retested','proposed','undetermined','historical','unknown'];
  for(const st of states) {assert.ok(D.stateLabel(item({assessment_status:st})).length);assert.ok(D.nextActionFor(st).endsWith('。'));}
  assert.equal(D.stateLabel(item({state:'waiting',assessment_status:null})),'未評価');
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

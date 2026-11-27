import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseFile,validateMap,importDocuments,serializeMap,nodeKindOptions,registerNodeKind,setNodeKind} from '../ai_yori_murasaki/web/architecture/model.mjs';

const example=JSON.parse(await readFile(new URL('../ai_yori_murasaki/web/architecture/sample.json',import.meta.url)));
const loadOne=(value,name='template.json')=>importDocuments([{name,value}]);

test('既存の種類と未登録の種類IDを表示し、宣言のない既存JSONをそのまま保存する',()=>{
  const original=structuredClone(example);
  original.nodes[0].kind='手続き / 受付';
  original.nodes[1].kind='external';
  const {map}=loadOne(original);
  assert.deepEqual([...nodeKindOptions(map)],[
    ['repository','リポジトリ'],['service','サービス'],['store','データストア'],
    ['external','外部システム'],['手続き / 受付','手続き / 受付']
  ]);
  assert.deepEqual(JSON.parse(serializeMap(map,original)),original);
  assert.equal(Object.hasOwn(JSON.parse(serializeMap(map)), 'nodeKinds'),false);
});

test('独自種類をIDで登録・割り当てし、要素の並び・type・拡張項目を保って再読込できる',()=>{
  const original=structuredClone(example);
  original.nodeKinds=[{id:'office',label:'窓口',legend:{color:'orange'}}];
  original.nodes[0].kind='日本語 / 受付';
  original.nodes[0].type='受付票';
  original.nodes[0].owner='例示部署';
  original.nodes[1].id=' '+original.nodes[1].id+' ';
  original.extension={version:2};
  original.connections[0].userInput={mode:'ui',scope:'件名',extension:true};
  const {map}=loadOne(original);
  const changed=map.nodes[1];
  assert.equal(registerNodeKind(map,'日本語 / 受付','窓口'),'日本語 / 受付');
  const kind=registerNodeKind(map,'constructor','保管 <一時>');
  setNodeKind(map,changed.id,kind);
  map.nodes.reverse();
  map.connections[0].userInput={mode:'internal'};
  const saved=JSON.parse(serializeMap(map,original));
  const expected=structuredClone(original);
  expected.nodeKinds.push({id:'日本語 / 受付',label:'窓口'},{id:'constructor',label:'保管 <一時>'});
  expected.nodes[1].kind='constructor';
  expected.connections[0].userInput={mode:'internal',extension:true};
  assert.deepEqual(saved,expected);
  const restored=loadOne(saved).map;
  assert.equal(nodeKindOptions(restored).get(restored.nodes[1].kind),'保管 <一時>');
  assert.equal(nodeKindOptions(restored).get('日本語 / 受付'),'窓口');
  assert.equal(restored.nodes[0].type,'受付票');
  assert.deepEqual(JSON.parse(serializeMap(restored,saved)),saved);
  assert.equal(original.nodeKinds.length,1);
  assert.notEqual(original.nodes[1].kind,'constructor');
});

test('種類の重複や不正な入力は読み込み・登録・割り当て時に拒否し、編集中の図を保つ',()=>{
  const entry={id:'desk',label:'窓口'};
  for (const nodeKinds of [null,{},[null],[entry,{id:' desk ',label:'二番'}],
    [{...entry,id:'service'}],[{...entry,id:' '}],[{...entry,label:20}],[{id:5,label:'名'}],
    [{id:'長'.repeat(161),label:'名'}],[{...entry,label:'長'.repeat(161)}],Array(101).fill(entry)]) {
    assert.throws(()=>loadOne({...example,nodeKinds}));
  }
  for (const kind of ['',null,{},'名'.repeat(161)]) {
    const invalid=structuredClone(example);
    invalid.nodes[0].kind=kind;
    assert.throws(()=>loadOne(invalid),/kind/);
  }
  const {map}=loadOne({...example,nodeKinds:[entry]});
  const snapshot=structuredClone(map);
  assert.throws(()=>registerNodeKind(map,'desk','複製'),/重複/);
  assert.throws(()=>registerNodeKind(map,'external','別名'),/重複/);
  assert.throws(()=>registerNodeKind(map,'queue',' '),/表示名/);
  assert.throws(()=>setNodeKind(map,map.nodes[0].id,'窓口'),/種類/);
  assert.throws(()=>setNodeKind(map,'missing','desk'),/ID/);
  assert.deepEqual(map,snapshot);
});

test('種類の長さは前後の空白を除いたUnicodeコードポイントで数える',()=>{
  const original=structuredClone(example);
  const id='🧩'.repeat(160), label='📦'.repeat(160);
  original.nodes[0].kind=' '+id+' ';
  const {map}=loadOne(original);
  assert.equal(map.nodes[0].kind,id);
  assert.equal(registerNodeKind(map,' '+id+' ',' '+label+' '),id);
  const saved=JSON.parse(serializeMap(map,original));
  assert.deepEqual(saved.nodeKinds,[{id,label}]);
  assert.equal(nodeKindOptions(loadOne(saved).map).get(id),label);
  assert.throws(()=>registerNodeKind(map,'x',label+'📦'),/表示名/);
  assert.throws(()=>registerNodeKind(map,id+'🧩','名称'),/ID/);
});

test('利用者の関与範囲は指定された部品IDだけを保存する',()=>{
  const original=structuredClone(example);
  delete original.userScope;
  const map=validateMap(original);
  assert.deepEqual(map.userScope,[]);
  map.userScope=['worker'];
  assert.deepEqual(validateMap(JSON.parse(serializeMap(map,original))).userScope,['worker']);
  map.userScope=[];
  assert.deepEqual(JSON.parse(serializeMap(map,example)).userScope,[]);
  for(const userScope of [null,'web',['missing'],['web','web']]) assert.throws(()=>validateMap({...original,userScope}),/userScope/);
});

test('接続の入力区分と任意の範囲を保存し、未指定と内部処理を区別する',()=>{
  const original=structuredClone(example);
  original.connections[0].owner='運用チーム';
  const {map}=loadOne(original);
  assert.equal(map.connections[0].userInput,undefined);
  map.connections[0].userInput={mode:'ui',scope:'申請件名'};
  delete map.connections[2].userInput;
  const doc=JSON.parse(serializeMap(map,original));
  assert.equal(doc.connections[0].owner,'運用チーム');
  assert.equal(original.connections[0].userInput,undefined);
  assert.deepEqual(validateMap(doc),map);
  assert.deepEqual(validateMap(JSON.parse(serializeMap(map))),map);
  for(const input of [null,{mode:'edit'},{mode:'internal',scope:'件名'},{mode:'ui',scope:'名'.repeat(601)}]){
    doc.connections[0].userInput=input;assert.throws(()=>validateMap(doc));
  }
});

test('見本と利用者の構成は同じ形式で、指摘は存在する要素と根拠を必要とする',()=>{
  const map=validateMap(example);
  assert.deepEqual([map.nodes.length,map.connections.length,map.findings.length],[6,6,2]);
  for(const mutate of [
    d=>d.nodes.push(d.nodes[0]), d=>d.connections[0].to='absent',
    d=>d.findings[0].node='absent', d=>d.findings[0].evidence='', d=>d.findings[0].severity='mystery'
  ]){
    const data=structuredClone(example);mutate(data);assert.throws(()=>validateMap(data));
  }
});
test('CloudFormationの明示依存・設定参照・条件付き設定の根拠を保持する',()=>{
  const yaml=[
    'Resources:', '  Archive:', '    Type: AWS::S3::Bucket','    Condition: StoreReceipts',
    '    Properties:', '      VersioningConfiguration: {Status: Suspended}',
    '  Index:', '    Type: AWS::DynamoDB::Table','    Properties:',
    '      PointInTimeRecoverySpecification: {PointInTimeRecoveryEnabled: false}',
    '  Function:', '    Type: AWS::Lambda::Function','    DependsOn: [Index]','    Properties:',
    '      Environment:', '        Variables:', '          FIRST: !Ref Archive','          SECOND: !GetAtt Index.Arn'
  ].join('\n');
  const {map}=loadOne(parseFile(yaml,'template.yml'));
  assert.equal(map.nodes.length,3);
  assert.deepEqual(map.connections.map(c=>[c.from,c.to,c.label]),[
    ['Function','Index','作成時に依存'],['Function','Archive','設定で参照'],['Function','Index','設定で参照']
  ]);
  assert.deepEqual(map.findings.map(f=>f.node),['Archive','Index']);
  assert.match(map.findings[0].evidence,/条件付き.*Archive.*Suspended/);
});
test('Fn::Subの変数上書きやパラメータを別のリソースへの参照にしない',()=>{
  const doc={Resources:{A:{Type:'AWS::S3::Bucket'},B:{Type:'AWS::S3::Bucket'},
    Fn:{Type:'AWS::Lambda::Function',Properties:{a:{'Fn::Sub':['\u0024{A} / \u0024{B.Arn} / \u0024{Param}',{A:'constant'}]}}}}};
  const {map}=loadOne(doc);
  assert.deepEqual(map.connections.map(c=>[c.from,c.to]),[['Fn','B']]);
  assert.equal(map.findings.length,0);
});
test('未設定・計算式を弱点と断定せず、存在しないDependsOnを拒否する',()=>{
  const doc={Resources:{Table:{Type:'AWS::DynamoDB::Table',Properties:{PointInTimeRecoverySpecification:{
    PointInTimeRecoveryEnabled:{Ref:'Parameter'}}}}}};
  assert.equal(loadOne(doc).map.findings.length,0);
  doc.Resources.Table.DependsOn='Missing';
  assert.throws(()=>loadOne(doc),/DependsOn/);
});
test('EFSは保存時暗号化の明示的な無効設定にだけ指摘を付ける',()=>{
  const efs=props=>({Type:'AWS::EFS::FileSystem',Properties:props});
  const map=loadOne({Resources:{Private:efs({Encrypted:true}),Unknown:efs({}),Plain:efs({Encrypted:false})}}).map;
  assert.equal(map.findings.length,1);
  assert.equal(map.findings[0].node,'Plain');
  assert.match(map.findings[0].evidence,/Resources.Plain.Properties.Encrypted = false/);
});
test('別々に選んだpackage.jsonを結び、共通リポジトリ・エイリアス・未読の依存を扱う',()=>{
  const pkg=(name,repository,extra={})=>({name:'package.json',value:{name,repository,...extra}});
  const one=importDocuments([pkg('site','github:team/site',{dependencies:{models:'npm:@team/contracts@^1',external:'^2'}})]);
  const two=importDocuments([pkg('@team/contracts',{url:'git+https://github.com/Team/Common.git'}),
    pkg('sibling','git@github.com:team/common.git',{devDependencies:{'@team/contracts':'*'}})],one.packages);
  assert.equal(two.map.nodes.length,2);
  assert.equal(two.map.connections.length,1);
  assert.match(two.map.connections[0].evidence,/site.*dependencies.*models/);
  assert.throws(()=>importDocuments([pkg('site','github:other/site')],two.packages),/重複/);
});

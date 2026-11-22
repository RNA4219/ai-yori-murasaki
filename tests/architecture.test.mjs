import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseFile,validateMap,importDocuments} from '../ai_yori_murasaki/web/architecture/model.mjs';

const example=JSON.parse(await readFile(new URL('../ai_yori_murasaki/web/architecture/sample.json',import.meta.url)));
const loadOne=(value,name='template.json')=>importDocuments([{name,value}]);

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

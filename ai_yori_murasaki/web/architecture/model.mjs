import { CORE_SCHEMA, Type, load } from './js-yaml.mjs';

export const SCHEMA = 'aym.architecture/v1';
export const MAX_BYTES = 1024 * 1024;
export const inputModes = new Map([['','未指定'],['ui','利用者入力'],['internal','内部処理']]);
export const severityNames = new Map([
  ['critical','重大'], ['high','高'], ['medium','中'], ['low','低'], ['info','情報']
]);
export const kindNames = new Map([
  ['repository','リポジトリ'], ['service','サービス'], ['store','データストア'], ['external','外部システム']
]);
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const tags = ['Ref','GetAtt','Sub','Join','Select','Split','If','FindInMap','ImportValue',
  'GetAZs','Base64','Equals','And','Or','Not','Condition','Cidr','Transform','Length','ToJsonString'];
const yamlSchema = CORE_SCHEMA.extend(tags.flatMap(tag => ['scalar','sequence','mapping'].map(kind =>
  new Type('!' + tag, {kind, construct:value => ({[tag === 'Ref' || tag === 'Condition' ? tag : 'Fn::' + tag]:value})}))));

export function parseFile(text, filename) {
  if (/\.json$/i.test(filename)) {
    try { return JSON.parse(text.replace(/^\uFEFF/,'')); }
    catch { throw Error(filename + ' をJSONとして読み取れません。記号や引用符を確認してください。'); }
  }
  try { return load(text, {schema:yamlSchema,maxDepth:64}); }
  catch (e) {
    const line = e.mark ? '（' + (e.mark.line + 1) + ' 行目）' : '';
    throw Error(filename + ' をYAMLとして読み取れません' + line + '。');
  }
}

function string(value, label, limit = 600) {
  if (typeof value !== 'string' || !value.trim() || value.length > limit) {
    throw Error(label + ' に1〜' + limit + '文字の文字列を指定してください。');
  }
  return value.trim();
}
function rows(value,label,limit) {
  if (!Array.isArray(value) || value.length > limit || value.some(v=>!plain(v))) {
    throw Error(label + ' は最大' + limit + '件のオブジェクトの配列で指定してください。');
  }
  return value;
}
export function validateMap(raw) {
  if (!plain(raw) || raw.schema !== SCHEMA) throw Error('構成図の形式を確認してください。');
  // 表示名だけを200文字に収める。書き出しには元の文書を使う。
  const title = typeof raw.name==='string' ? raw.name.trim() : '';
  if (!title) throw Error('構成名を指定してください。');
  const name = title.match(/^.{1,200}/su)[0];
  const ids = new Set();
  const nodes = rows(raw.nodes,'nodes',100).map(n=>{
    const id=string(n.id,'構成要素のID',180);
    if (ids.has(id)) throw Error('構成要素のIDが重複しています。' + id);
    ids.add(id);
    if (!kindNames.has(n.kind)) throw Error(id + ' の種類（kind）を確認してください。');
    return {id,kind:n.kind,name:string(n.name,'表示名',160),type:n.type ? string(n.type,'種別',160) : '',
      description:n.description ? string(n.description,'説明',1000) : ''};
  });
  if (!nodes.length) throw Error('少なくとも1件の構成要素が必要です。');
  const connections=rows(raw.connections,'connections',300).map(c=>{
    if (!ids.has(c.from) || !ids.has(c.to)) throw Error('接続先または接続元のIDが nodes に存在しません。');
    if (c.from===c.to) throw Error('接続元と接続先には別の構成要素を指定してください。');
    const connection={from:c.from,to:c.to,label:string(c.label,'関係の名前',80),
      evidence:c.evidence ? string(c.evidence,'関係の根拠',1600) : ''};
    if ('userInput' in c) {
      const input=c.userInput;
      if (!plain(input) || !input.mode || !inputModes.has(input.mode)) throw Error('接続の userInput.mode は ui または internal を指定してください。');
      connection.userInput={mode:input.mode};
      if ('scope' in input) {
        if (input.mode!=='ui') throw Error('入力範囲は利用者入力の接続に指定してください。');
        connection.userInput.scope=string(input.scope,'画面から入力できる範囲',600);
      }
    }
    return connection;
  });
  const findings=rows(raw.findings ?? [],'findings',300).map(f=>{
    if (!ids.has(f.node)) throw Error('指摘の対象IDが nodes に存在しません。');
    if (!severityNames.has(f.severity)) throw Error('指摘の重要度（severity）を確認してください。');
    return {node:f.node,severity:f.severity,title:string(f.title,'指摘名',220),
      evidence:string(f.evidence,'指摘の根拠',1600),action:string(f.action,'確認・対応',1000)};
  });
  const userScope=raw.userScope===undefined ? [] : raw.userScope;
  if(!Array.isArray(userScope) || userScope.length>ids.size || new Set(userScope).size!==userScope.length || userScope.some(id=>!ids.has(id))){
    throw Error('userScope には関与範囲に含める構成要素のIDを重複なく指定してください。');
  }
  return {name,nodes,connections,findings,userScope:[...userScope]};
}

export function serializeMap(map,original) {
  const document=original ? {...original,connections:original.connections.map((item,i)=>{
    const connection={...item};
    const input=map.connections[i].userInput;
    if(input) connection.userInput=input;
    else delete connection.userInput;
    return connection;
  })} : {schema:SCHEMA,...map};
  if(map.userScope.length || original?.userScope) document.userScope=map.userScope;
  const tooLarge='保存する構成図が1 MiBを超えています。';
  // YAML の別名が繰り返し使われた場合も、JSONへの展開量を制限する。
  let budget=MAX_BYTES;
  const compact=JSON.stringify(document,function(key,value){
    budget-=1+(Array.isArray(this)?0:key.length)+(typeof value==='string'?value.length:0);
    if(budget<0) throw Error(tooLarge);
    return value;
  });
  if(new Blob([compact]).size>MAX_BYTES) throw Error(tooLarge);
  const pretty=JSON.stringify(document,null,2)+'\n';
  return new Blob([pretty]).size<=MAX_BYTES ? pretty : compact;
}

function cloudMap(document, filename) {
  const resources = document.Resources;
  const nodes=[], connections=[], findings=[], seen=new Set();
  const ids=Object.keys(resources);
  if (ids.length>100) throw Error('一度に表示できるリソースは100件までです。');
  const known=new Set(ids);
  for (const [id,resource] of Object.entries(resources)) {
    if (!plain(resource) || typeof resource.Type!=='string') throw Error(id + ' の Type が見つかりません。');
    const type=resource.Type;
    const props=resource.Properties || {};
    const conditional=resource.Condition ? '条件付きリソース。' : '';
    const kind=/^AWS::(S3::Bucket|DynamoDB::(Table|GlobalTable)|RDS::DB(Instance|Cluster)|EFS::FileSystem)$/.test(type) ? 'store' : 'service';
    nodes.push({id,name:id,kind,type,description:conditional + 'CloudFormation テンプレートに宣言されたリソース。'});
    function edge(to,label,path) {
      if (!known.has(to) || to===id) return;
      const key=JSON.stringify([id,to,label]);
      if (seen.has(key)) return;
      seen.add(key);
      connections.push({from:id,to,label,evidence:'Resources.' + id + '.' + path});
    }
    const dependencies=resource.DependsOn === undefined ? [] : [].concat(resource.DependsOn);
    for (const ref of dependencies) {
      if (typeof ref!=='string' || !known.has(ref)) throw Error(id + ' の DependsOn に未定義のリソースがあります。');
      edge(ref,'作成時に依存','DependsOn');
    }
    // テンプレートは実行せず、既知の論理IDへの参照だけを線にする。
    const visited=new WeakSet();
    let visits=0;
    function walk(value,path,depth) {
      if (!value || typeof value!=='object' || visited.has(value)) return;
      if (depth>64 || ++visits>15000) throw Error(id + ' の設定が読み込み上限を超えています。');
      visited.add(value);
      if (typeof value.Ref==='string') edge(value.Ref,'設定で参照',path+'.Ref');
      const getAtt=value['Fn::GetAtt'];
      if (typeof getAtt==='string' || Array.isArray(getAtt)) {
        edge(typeof getAtt==='string' ? getAtt.split('.')[0] : getAtt[0],'設定で参照',path+'.Fn::GetAtt');
      }
      const sub=value['Fn::Sub'];
      if (sub !== undefined) {
        const template=Array.isArray(sub) ? sub[0] : sub;
        const overrides=Array.isArray(sub) && plain(sub[1]) ? sub[1] : {};
        if (typeof template==='string') {
          for (const match of template.matchAll(/\$\{([A-Za-z0-9]+)(?:\.([^}]+))?\}/g)) {
            const variable=match[2] ? match[1]+'.'+match[2] : match[1];
            if (!Object.hasOwn(overrides,variable)) edge(match[1],'設定で参照',path+'.Fn::Sub');
          }
        }
      }
      for (const [key,child] of Object.entries(value)) walk(child,path+'.'+key,depth+1);
    }
    walk(props,'Properties',0);
    if (type==='AWS::S3::Bucket' && props.VersioningConfiguration?.Status==='Suspended') {
      findings.push({node:id,severity:'medium',title:'オブジェクトのバージョニングが一時停止',
        evidence:conditional+'Resources.'+id+'.Properties.VersioningConfiguration.Status = Suspended',
        action:'このバケットの復元要件を確認し、必要に応じてバージョニングを有効にしてください。'});
    }
    if (type==='AWS::DynamoDB::Table' && props.PointInTimeRecoverySpecification?.PointInTimeRecoveryEnabled===false) {
      findings.push({node:id,severity:'medium',title:'ポイントインタイムリカバリが無効',
        evidence:conditional+'Resources.'+id+'.Properties.PointInTimeRecoverySpecification.PointInTimeRecoveryEnabled = false',
        action:'障害時にどの時点まで戻す必要があるか確認し、復元要件に合わせて設定を見直してください。'});
    }
    if (type==='AWS::EFS::FileSystem' && props.Encrypted===false) {
      findings.push({node:id,severity:'medium',title:'ファイルの保存時暗号化が無効',
        evidence:conditional+'Resources.'+id+'.Properties.Encrypted = false',
        action:'保存するデータの機密性を確認し、暗号化が必要なら暗号化済みファイルシステムへの移行計画を検討してください。'});
    }
  }
  return validateMap({schema:SCHEMA,name:filename,nodes,connections,findings});
}

function repoKey(manifest) {
  const source=typeof manifest.repository==='string' ? manifest.repository : manifest.repository?.url;
  if (typeof source!=='string') throw Error(manifest.name+' の package.json に repository のURLが必要です。');
  let value=source.replace(/^git\+/,'').replace(/\.git\/?$/i,'').replace(/\/$/,'');
  if (/^(?:github:)?[\w.-]+\/[\w.-]+$/.test(value)) value='https://github.com/'+value.replace(/^github:/,'');
  value=value.replace(/^git@([^:]+):/,'ssh://git@$1/');
  try {
    const url=new URL(value);
    if (!['https:','http:','ssh:','git:'].includes(url.protocol) || !url.hostname || url.pathname==='/') throw Error();
    const path=url.pathname.replace(/\/$/,'').replace(/\.git$/i,'');
    return url.host + (url.hostname==='github.com' ? path.toLowerCase() : path);
  } catch { throw Error(manifest.name+' の repository URLを確認してください。'); }
}
function packageMap(manifests) {
  const repos=new Map(), owners=new Map(), connections=[];
  for (const m of manifests.values()) {
    const key=repoKey(m);
    if (!repos.has(key)) repos.set(key,{id:'r'+(repos.size+1),kind:'repository',name:key.split('/').at(-1),type:'Git',description:key});
    owners.set(m.name,repos.get(key).id);
  }
  for (const m of manifests.values()) {
    for (const [field,label] of [['dependencies','依存'],['peerDependencies','ピア依存'],['devDependencies','開発時に依存'],['optionalDependencies','任意の依存']]) {
      for (const [name,version] of Object.entries(m[field] || {})) {
        const alias=typeof version==='string' && /^npm:((?:@[^/]+\/)?[^@]+)(?:@.*)?$/.exec(version);
        const to=owners.get(alias ? alias[1] : name);
        if (to && to!==owners.get(m.name)) connections.push({from:owners.get(m.name),to,label,evidence:m.name+' / package.json → '+field+' → '+name});
      }
    }
  }
  return validateMap({schema:SCHEMA,name:'リポジトリの依存関係',nodes:[...repos.values()],connections,findings:[]});
}

export function importDocuments(files, priorPackages=new Map()) {
  if (!files.length) throw Error('読み込むファイルを選んでください。');
  if (files.every(({value})=>plain(value) && typeof value.name==='string' && !('schema' in value) && !('Resources' in value))) {
    const packages=new Map(priorPackages);
    for (const {value} of files) {
      const name=string(value.name,'パッケージ名',214);
      if (packages.has(name) && repoKey(packages.get(name))!==repoKey(value)) throw Error(name+' は異なるリポジトリに重複しています。');
      packages.set(name,value);
    }
    if (packages.size>100) throw Error('package.json は100件まで読み込めます。');
    return {map:packageMap(packages),packages,source:'package.json '+packages.size+' 件',
      note:'読み込んだパッケージ間の宣言上の依存関係を表示しています。'};
  }
  if (files.length!==1) throw Error('複数ファイルを同時に選べるのは package.json です。');
  const {name,value}=files[0];
  if (plain(value) && value.schema===SCHEMA) return {map:validateMap(value),original:value,source:name,note:'ファイルに記載された接続と指摘を表示しています。'};
  if (plain(value) && plain(value.Resources)) return {map:cloudMap(value,name),source:name,
    note:'テンプレート上の依存関係を表示しています。S3の版管理、DynamoDBの復元、EFSの保存時暗号化の明示設定を確認します。'};
  throw Error('構成図、CloudFormation、または repository を含む package.json を選んでください。');
}

import { parseFile, importDocuments, validateMap, serializeMap, MAX_BYTES, kindNames, severityNames, inputModes } from './model.mjs';

const $=id=>document.getElementById(id);
const ns='http://www.w3.org/2000/svg';
const priorities=[...severityNames.keys()];
const state={map:null,byId:new Map(),findingsByNode:new Map(),selected:null,mode:'all',packages:new Map(),load:0,note:'',choice:'sample'};
const params=new URLSearchParams(location.search);
const blueView=params.get('view')==='blue';
const mapIdPattern=/^[a-z0-9][a-z0-9-]{0,63}$/;
const mapChoice=$('map-choice');
const diagram=$('diagram'), frame=$('frame');
let camera={x:0,y:0,zoom:1}, chart=null, selectedWire=null, wireChoices=[];
let editingScope=false;
const scopeMembers=new Set();
const offsets={all:new Map(),repos:new Map()};
const nodesDrawn=new Map(), wiresDrawn=new Map();
function html(tag,text,cls) {
  const e=document.createElement(tag);
  if (cls) e.className=cls;
  if (text!==undefined) e.textContent=text;
  return e;
}
function svg(tag,attrs={},text) {
  const e=document.createElementNS(ns,tag);
  for (const [k,v] of Object.entries(attrs)) e.setAttribute(k,String(v));
  if (text!==undefined) e.textContent=text;
  return e;
}
function issues(id) { return state.findingsByNode.get(id); }
function levelName(severity) {
  return severity==='info' ? severityNames.get(severity) : severity[0].toUpperCase()+severity.slice(1);
}
function eligible() { return state.map.nodes.filter(n=>state.mode==='all' || n.kind==='repository'); }
function incident(id) { return state.map.connections.filter(c=>c.from===id || c.to===id); }
function initial(nodes) {
  const wanted=new Set(nodes.map(n=>n.id));
  return state.map.findings.find(f=>wanted.has(f.node))?.node ?? nodes[0].id;
}

const replaceDialog=$('replace-dialog');
let pendingMap=null;

function requestMap(result) {
  // 既存の package.json を書き換えずに追加する場合は、編集中の範囲と経路をそのまま統合できる。
  const appending=result.packages && state.packages.size>0 &&
    [...state.packages].every(([name,manifest])=>result.packages.get(name)===manifest);
  if($('unsaved').hidden || appending) {
    apply(result);
    return;
  }
  pendingMap={result,version:state.load};
  $('replace-note').textContent='切り替えると現在の編集内容が失われます。必要なら先にJSONを保存してください。';
  $('replace-discard').textContent='保存せず切り替える';
  replaceDialog.returnValue='cancel';
  if(!replaceDialog.open) replaceDialog.showModal();
}
$('replace-cancel').addEventListener('click',()=>replaceDialog.close('cancel'));
$('replace-discard').addEventListener('click',()=>replaceDialog.close('replace'));
$('replace-save').addEventListener('click',()=>{
  if(!downloadMap()) {
    replaceDialog.close('cancel');
    return;
  }
  // ブラウザーからはダウンロードの完了や取り消しを判定できない。
  $('replace-note').textContent='JSONの保存を確認してから、図を切り替えてください。';
  $('replace-discard').textContent='切り替える';
});
replaceDialog.addEventListener('close',()=>{
  const pending=pendingMap;
  pendingMap=null;
  if(replaceDialog.returnValue==='replace' && pending?.version===state.load) apply(pending.result);
  else syncChoice();
});

function apply(result) {
  const sample=result.choice==='sample';
  let keptEdits=false;
  if(result.packages && state.packages.size){
    result.map.userScope=result.map.nodes.filter(n=>scopeMembers.has(n.id)).map(n=>n.id);
    keptEdits=!$('unsaved').hidden || result.map.userScope.length>0;
    const key=c=>JSON.stringify([c.from,c.to,c.label,c.evidence]);
    const inputs=new Map(state.map.connections.filter(c=>c.userInput).map(c=>[key(c),c.userInput]));
    for(const c of result.map.connections){
      const input=inputs.get(key(c));
      if(input) { c.userInput=input;keptEdits=true; }
    }
  }
  state.map=result.map;
  state.choice=result.choice || 'file';
  syncChoice();
  const url=new URL(location.href);
  url.searchParams.delete('map');
  url.searchParams.delete('system');
  if (state.choice==='aym') url.searchParams.set('system','aym');
  if (state.choice.startsWith('map:')) url.searchParams.set('map',state.choice.slice(4));
  if (url.href!==location.href) history.replaceState(history.state,'',url);
  editingScope=false;scopeMembers.clear();
  for(const id of state.map.userScope) scopeMembers.add(id);
  state.original=result.original;
  setEdited(keptEdits);
  $('save-map').disabled=false;
  state.map.findings.sort((a,b)=>priorities.indexOf(a.severity)-priorities.indexOf(b.severity));
  state.byId=new Map(state.map.nodes.map(n=>[n.id,n]));
  state.findingsByNode=new Map(state.map.nodes.map(n=>[n.id,[]]));
  for(const finding of state.map.findings) state.findingsByNode.get(finding.node).push(finding);
  state.mode='all';
  offsets.all.clear();offsets.repos.clear();selectedWire=null;
  resetGestures();
  chart=null;
  state.packages=result.packages || new Map();
  state.selected=initial(state.map.nodes);
  state.note=result.note || '';
  $('nearby').checked=false;
  $('workspace').hidden=false;
  $('error').hidden=true;
  if (result.notice) error(result.notice);
  $('reset').hidden=sample;
  $('name').textContent=state.map.name;
  $('source').textContent=sample ? '見本データ' : '読み込み元　'+result.source;
  $('counts').textContent=state.map.nodes.length+' 要素　／　'+state.map.connections.length+' 接続　／　'+state.map.findings.length+' 件の指摘';
  render();
  $('status').textContent='構成図を読み込みました。'+$('source').textContent;
}
function error(message) { $('error').textContent=message; $('error').hidden=false; }
function warnBeforeLeaving(event) {
  event.preventDefault();
  event.returnValue=true;
}
function setEdited(edited){
  if(edited) window.addEventListener('beforeunload',warnBeforeLeaving);
  else window.removeEventListener('beforeunload',warnBeforeLeaving);
  $('unsaved').hidden=!edited;
  $('edited-label').hidden=false;
  $('confirm-saved').hidden=true;
  $('save-changes').hidden=!edited || !selectedWire;
  if(!edited) $('scope-save').hidden=true;
}
function syncChoice() {
  const temporary=mapChoice.querySelector('[data-current]');
  if (temporary) temporary.remove();
  mapChoice.value=state.choice;
  if (mapChoice.selectedIndex<0 && state.map) {
    // ローカルファイル、または一覧上限外の図をアドレスから直接開いた場合。
    const label=state.choice==='file' ? 'ファイルから開いた図' : state.map.name;
    const option=new Option(label,state.choice);
    option.disabled=true;
    option.dataset.current='';
    mapChoice.add(option);
    mapChoice.value=state.choice;
  }
}

async function loadChoices() {
  if (blueView) return;
  try {
    const res=await fetch('/api/architecture/maps',{credentials:'same-origin'});
    if (!res.ok) return;
    const {maps}=await res.json();
    if (!Array.isArray(maps) || !maps.length) return;
    const options=document.createDocumentFragment();
    for (const entry of maps) {
      if (!entry || typeof entry.id!=='string' || !mapIdPattern.test(entry.id) || typeof entry.name!=='string') return;
      options.append(new Option(entry.name,'map:'+entry.id));
    }
    mapChoice.append(options);
    syncChoice();
    $('map-choice-wrap').hidden=false;
  } catch {
    // 一覧が使えなくても、見本・製品の図とローカルファイルは開ける。
  }
}

async function loadChoice(value,{fallback=false,notice=''}={}) {
  const version=++state.load;
  const workspace=value.startsWith('map:');
  const id=workspace ? value.slice(4) : '';
  const product=value==='aym';
  try {
    let path=product ? '/architecture/product.json' : '/architecture/sample.json';
    if (workspace) {
      if (blueView || !mapIdPattern.test(id)) throw Error('指定した構成図の ID を確認してください。');
      path='/api/architecture/maps/'+id;
    }
    const response=await fetch(path,{credentials:'same-origin'});
    if (workspace && response.status===404) throw Error('指定した構成図が作業場所にありません。');
    if (!response.ok) throw Error('構成図を読み込めませんでした。');
    let original=await response.json();
    if (version!==state.load) return;
    if (workspace && original && (typeof original.name!=='string' || !original.name.trim())) {
      original={...original,name:id};
    }
    const map=validateMap(original);
    const source=workspace ? '作業場所の '+id+'.json' : 'ai-yori-murasaki の実装';
    const note=value==='sample' ? '架空の構成とレビュー記録を使用しています。' : '';
    requestMap({map,original,source,note,choice:value,notice});
  } catch(e) {
    if(version!==state.load) return;
    syncChoice();
    if (fallback) return loadChoice('sample',{notice:e.message+' 見本を開きました。'});
    error(e.message);
  }
}
async function openFiles(files) {
  const version=++state.load;
  syncChoice();
  try {
    if (files.length>100 || files.some(file=>file.size>MAX_BYTES) || files.reduce((n,f)=>n+f.size,0)>4*MAX_BYTES) {
      throw Error('1ファイル1 MiB以下、一度に100ファイル・合計4 MiB以下で選んでください。');
    }
    const docs=[];
    for (const file of files) {
      const contents=await file.text();
      if (version!==state.load) return;
      docs.push({name:file.name,value:parseFile(contents,file.name)});
    }
    const result=importDocuments(docs,state.packages);
    if(version===state.load) requestMap(result);
  } catch(e) { if (version===state.load) error(e.message); }
}

// リポジトリだけの図は参照元からの距離で並べる。循環や独立した要素にも位置を割り当てる。
function repoBands(nodes,edges) {
  const outgoing=new Map(nodes.map(n=>[n.id,[]]));
  const incoming=new Set();
  for (const e of edges) { outgoing.get(e.from).push(e.to); incoming.add(e.to); }
  const levels=new Map();
  function visit(seeds) {
    const queue=seeds.map(n=>n.id);
    queue.forEach(id=>levels.set(id,0));
    for(let i=0;i<queue.length;i++){
      const id=queue[i];
      for(const other of outgoing.get(id)) if(!levels.has(other)) {
        levels.set(other,levels.get(id)+1);queue.push(other);
      }
    }
  }
  visit(nodes.filter(n=>!incoming.has(n.id)));
  for(const n of nodes) if(!levels.has(n.id)) visit([n]);
  const bands=[];
  for(const n of nodes) (bands[levels.get(n.id)] ??= {label:'',nodes:[]}).nodes.push(n);
  return bands;
}
const actorHeight=46;
function cardSize(id,width,inputSources) {
  const counts=new Map();
  for(const {severity} of issues(id)) counts.set(severity,(counts.get(severity) || 0)+1);
  const badges=[];
  let x=10,y=54;
  measure.font='bold 12px Arial,sans-serif';
  for(const [severity,count] of counts) {
    const label=levelName(severity)+' '+count;
    const w=Math.ceil(measure.measureText(label).width)+14;
    if(x>10 && x+w>width-10) { x=10;y+=23; }
    badges.push({severity,label,x,y,w});
    x+=w+4;
  }
  return {w:width,h:badges.length ? y+46 : 80,badges,actorSpace:inputSources.has(id)?actorHeight:0};
}
function layout(nodes,edges,width) {
  const inputSources=new Set(state.map.connections.filter(c=>c.userInput?.mode==='ui').map(c=>c.from));
  const repoOnly=nodes.every(n=>n.kind==='repository');
  const bands=repoOnly ? repoBands(nodes,edges) : [
    {label:'リポジトリ',nodes:nodes.filter(n=>n.kind==='repository')},
    {label:'サービス',nodes:nodes.filter(n=>n.kind==='service' || n.kind==='external')},
    {label:'データストア',nodes:nodes.filter(n=>n.kind==='store')}
  ].filter(b=>b.nodes.length);
  const vertical=width<580 || bands.length>4;
  const positions=new Map(), zones=[];
  const gap=36, margin=12;
  let bottom=0;
  if(vertical) {
    let top=14;
    for(const band of bands){
      const columns=width>=460 ? 3 : width>=290 ? 2 : 1;
      const cell=(width-margin*2)/columns;
      const bw=Math.min(186,cell-18);
      const start=band.label ? 64 : 12;
      let zoneHeight=start;
      for(let i=0;i<band.nodes.length;i+=columns){
        const row=band.nodes.slice(i,i+columns).map((n,col)=>{
          const card=cardSize(n.id,bw,inputSources);
          Object.assign(card,{x:margin+(col+.5)*cell-bw/2,y:top+zoneHeight+card.actorSpace,bandTop:top});
          positions.set(n.id,card);
          return card.actorSpace+card.h;
        });
        zoneHeight+=Math.max(...row)+20;
      }
      zones.push({x:margin,y:top,w:width-margin*2,h:zoneHeight,label:band.label});
      for(const n of band.nodes) positions.get(n.id).bandBottom=top+zoneHeight;
      top+=zoneHeight+22;
    }
    bottom=top;
  } else {
    const widthPer=(width-2*margin)/bands.length;
    const bw=Math.min(205,Math.max(106,widthPer-46));
    const heights=bands.map(band=>band.nodes.reduce((total,n)=>{
      const card=cardSize(n.id,bw,inputSources);
      positions.set(n.id,card);
      return total+card.actorSpace+card.h+gap;
    },0));
    bottom=Math.max(300,Math.max(...heights)+96);
    bands.forEach((band,col)=>{
      const left=margin+col*widthPer;
      zones.push({x:left+2,y:16,w:widthPer-4,h:bottom-32,label:band.label});
      let y=(bottom-heights[col]+gap)/2+10;
      for(const n of band.nodes){
        const card=positions.get(n.id);
        card.x=left+(widthPer-bw)/2;card.y=y+card.actorSpace;
        y=card.y+card.h+gap;
      }
    });
  }
  return {positions,zones,height:bottom};
}
function edgePath(a,b) {
  const ax=a.x+a.w/2, ay=a.y+a.h/2, bx=b.x+b.w/2, by=b.y+b.h/2;
  if(a.bandTop!==undefined && b.bandTop!==undefined && a.bandTop!==b.bandTop) {
    const corridor=by>ay ? a.bandBottom+10 : a.bandTop-10;
    return 'M '+(a.x+a.w)+' '+ay+' h 5 V '+corridor+' H '+(b.x-7)+' V '+by+' H '+(b.x-2);
  }
  if(Math.abs(ax-bx) < 5) {
    const side=Math.abs(ay-by)>a.h*2 ? -1 : 1;
    const x1=ax+side*a.w/2, x2=bx+side*b.w/2, lane=x1+side*14;
    return 'M '+x1+' '+ay+' C '+(lane+side*4)+' '+ay+' '+lane+' '+ay+' '+lane+' '+((ay+by)/2)+' S '+(lane+side*4)+' '+by+' '+(x2+side*2)+' '+by;
  }
  if(Math.abs(by-ay)>a.h*1.5 && Math.abs(bx-ax)<a.w*1.4) {
    const s=by>ay?1:-1, y1=ay+s*a.h/2, y2=by-s*(b.h/2+2), mid=(y1+y2)/2;
    return 'M '+ax+' '+y1+' C '+ax+' '+mid+' '+bx+' '+mid+' '+bx+' '+y2;
  }
  const s=bx>ax?1:-1, x1=ax+s*a.w/2, x2=bx-s*(b.w/2+2), mid=(x1+x2)/2;
  return 'M '+x1+' '+ay+' C '+mid+' '+ay+' '+mid+' '+by+' '+x2+' '+by;
}
const measure=document.createElement('canvas').getContext('2d');
function fit(text,width,size,bold=false) {
  measure.font=(bold?'700 ':'')+size+'px "Yu Gothic UI",Meiryo,sans-serif';
  if(measure.measureText(text).width<=width) return text;
  let s='';
  for(const char of text){if(measure.measureText(s+char+'…').width>width) break; s+=char;}
  return s+'…';
}
const glyphs={
  repository:'M3 4h13v13H5a2 2 0 0 1-2-2z M6 4v9 M3 14h13',
  service:'M6 3h7l4 6-4 6H6L2 9z M7 9h5',
  store:'M3 5c0-4 13-4 13 0s-13 4-13 0 M3 5v9c0 4 13 4 13 0V5 M3 10c0 4 13 4 13 0',
  external:'M3 5h13v10H3z M6 9h7 M9 7l3 2-3 2'
};
function userPeg(){
  const peg=svg('g',{class:'user-peg',transform:'translate(10 '+(-actorHeight)+')','aria-hidden':true});
  peg.append(svg('circle',{cx:10,cy:7,r:5.6}),
    svg('path',{d:'M7 14Q10 12.8 13 14L16.3 25Q16.8 28 10 28Q3.2 28 3.7 25Z'}),
    svg('text',{x:25,y:21},'利用者'),
    svg('path',{class:'peg-arrow',d:'M10 31v12m-4-4 4 4 4-4'}));
  return peg;
}
function updateCamera() {
  const {x,y,zoom}=camera, width=frame.clientWidth/zoom, height=frame.clientHeight/zoom;
  diagram.setAttribute('viewBox',[x,y,width,height].join(' '));
  const background=$('grid-background');
  if(background) for(const [key,v] of Object.entries({x,y,width,height})) background.setAttribute(key,v);
  $('zoom-value').textContent=Math.round(zoom*100)+'%';
  $('zoom-out').disabled=zoom<=.05;$('zoom-in').disabled=zoom>=4;
  $('reset-layout').disabled=!offsets[state.mode].size;
}
function fitMap() {
  if(!chart || !chart.positions.size) return;
  const boxes=[...chart.positions.values()];
  const auto=!offsets[state.mode].size;
  const x=Math.min(...boxes.map(p=>p.x))-24, y=Math.min(...boxes.map(p=>p.y-p.actorSpace))-(auto?44:24);
  const w=Math.max(...boxes.map(p=>p.x+p.w))+24-x,h=Math.max(...boxes.map(p=>p.y+p.h))+24-y;
  const zoom=Math.min(1,frame.clientWidth/w,frame.clientHeight/h);
  camera={zoom,x:x+(w-frame.clientWidth/zoom)/2,y:y+(h-frame.clientHeight/zoom)/2};
  updateCamera();
}
function viewportPoint(clientX,clientY) {
  const rect=diagram.getBoundingClientRect();
  return {x:clientX-rect.left,y:clientY-rect.top};
}
function zoomAt(value,point={x:frame.clientWidth/2,y:frame.clientHeight/2}) {
  const next=Math.max(.05,Math.min(4,value));
  camera.x+=point.x*(1/camera.zoom-1/next);
  camera.y+=point.y*(1/camera.zoom-1/next);
  camera.zoom=next;updateCamera();
}
function reveal(target) {
  if(!target) return;
  const box=target.getBoundingClientRect(), view=diagram.getBoundingClientRect();
  const x=(box.left+box.right)/2, y=(box.top+box.bottom)/2;
  if(x<view.left+8 || x>view.right-8 || y<view.top+8 || y>view.bottom-8) {
    camera.x+=(x-view.left-view.width/2)/camera.zoom;
    camera.y+=(y-view.top-view.height/2)/camera.zoom;
    updateCamera();
  }
}
function drawScope(){
  const layer=$('scope-frame');
  if(!layer || !chart) return;
  layer.replaceChildren();
  const members=[], others=[];
  for(const [id,p] of chart.positions){
    const inside=scopeMembers.has(id), pad=inside?8:4;
    (inside?members:others).push({l:p.x-pad,r:p.x+p.w+pad,t:p.y-p.actorSpace-pad,b:p.y+p.h+pad});
  }
  if(!members.length) return;
  const left=Math.min(...members.map(p=>p.l)), right=Math.max(...members.map(p=>p.r));
  const top=Math.min(...members.map(p=>p.t)), bottom=Math.max(...members.map(p=>p.b));
  const blockers=others.filter(p=>p.l<right && p.r>left && p.t<bottom && p.b>top);
  // 他の部品が間にある場合だけ、水平帯ごとに枠を折り曲げて除外する。
  const levels=new Set([top,bottom]);
  if(blockers.length) for(const p of [...members,...blockers]){
    levels.add(Math.max(top,p.t));levels.add(Math.min(bottom,p.b));
  }
  const ys=[...levels].sort((a,b)=>a-b);
  let fill='',border='',previous=[];
  for(let i=0;i<ys.length;i++){
    const y=ys[i], end=ys[i+1];
    let spans=[];
    if(end!==undefined){
      const active=members.filter(p=>p.t<end && p.b>y);
      if(active.length){
        spans=[[Math.min(...active.map(p=>p.l)),Math.max(...active.map(p=>p.r))]];
      } else {
        // 上下の部品が同じ列にあるときは、その間も枠でつなぐ。
        const above=Math.max(...members.filter(p=>p.b<=y).map(p=>p.b));
        const below=Math.min(...members.filter(p=>p.t>=end).map(p=>p.t));
        const upper=members.filter(p=>p.b===above), lower=members.filter(p=>p.t===below);
        const l=Math.max(Math.min(...upper.map(p=>p.l)),Math.min(...lower.map(p=>p.l)));
        const r=Math.min(Math.max(...upper.map(p=>p.r)),Math.max(...lower.map(p=>p.r)));
        if(l<r) spans=[[l,r]];
      }
      for(const p of blockers.filter(p=>p.t<end && p.b>y)){
        spans=spans.flatMap(([l,r])=>[[l,Math.min(p.l,r)],[Math.max(p.r,l),r]].filter(([a,b])=>a<b));
      }
      for(const [l,r] of spans){
        fill+='M'+l+' '+y+'H'+r+'V'+end+'H'+l+'Z';
        border+='M'+l+' '+y+'V'+end+'M'+r+' '+y+'V'+end;
      }
    }
    // 隣り合う帯の共有辺を消し、外周だけを描く。
    const ends=[...previous.flat(),...spans.flat()].sort((a,b)=>a-b);
    for(let j=0;j<ends.length;j+=2) if(ends[j]<ends[j+1]) border+='M'+ends[j]+' '+y+'H'+ends[j+1];
    previous=spans;
  }
  const anchor=members.find(p=>p.t===top);
  layer.append(svg('path',{class:'scope-fill',d:fill}),svg('path',{class:'scope-border',d:border}),
    svg('text',{class:'scope-caption',x:anchor.l+5,y:top-3},'利用者の関与範囲'));
}
function editScope(ids,toggle=false){
  for(const id of ids){
    if(toggle && scopeMembers.has(id)) scopeMembers.delete(id);
    else scopeMembers.add(id);
  }
  state.map.userScope=[...scopeMembers];
  $('scope-save').hidden=false;
  setEdited(true);draw();
}
function setScopeMode(on){
  editingScope=on;resetGestures();draw();
}
function draw(reset=false){
  if(!state.map) return;
  const visible=eligible(), near=new Set([state.selected]);
  for(const c of incident(state.selected)){near.add(c.from);near.add(c.to);}
  const nodes=$('nearby').checked ? visible.filter(n=>near.has(n.id)) : visible;
  const ids=new Set(nodes.map(n=>n.id));
  const edges=state.map.connections.filter(c=>ids.has(c.from)&&ids.has(c.to));
  const width=Math.max(235,frame.clientWidth), initial=!chart || reset;
  chart=layout(nodes,edges,width);
  for(const [id,offset] of offsets[state.mode]){
    const p=chart.positions.get(id);
    if(p){p.x+=offset.x;p.y+=offset.y;delete p.bandTop;}
  }
  const active=document.activeElement?.closest?.('.node,.wire')?.dataset;
  nodesDrawn.clear();wiresDrawn.clear();
  const fragment=document.createDocumentFragment(),defs=svg('defs');
  for(const [id,color] of [['arrow','#8c8192'],['active-arrow','#674779'],['input-arrow','#006d95']]){
    const marker=svg('marker',{id,viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:6,markerHeight:6,orient:'auto'});
    marker.append(svg('path',{d:'M0 1 L9 5 L0 9 z',fill:color}));defs.append(marker);
  }
  const grid=svg('pattern',{id:'grid',width:16,height:16,patternUnits:'userSpaceOnUse'});
  grid.append(svg('circle',{cx:1,cy:1,r:.9,fill:'#cfc5d5'}));defs.append(grid);
  fragment.append(defs,svg('rect',{id:'grid-background',fill:'url(#grid)','pointer-events':'none'}));
  const lanes=svg('g',{id:'lanes'});
  for(const z of chart.zones) if(z.label){
    lanes.append(svg('rect',{class:'lane',x:z.x,y:z.y,width:z.w,height:z.h,rx:9}));
    lanes.append(svg('line',{class:'lane-divider',x1:z.x+1,y1:z.y+30,x2:z.x+z.w-1,y2:z.y+30}));
    lanes.append(svg('text',{class:'lane-title',x:z.x+10,y:z.y+22},z.label));
  }
  fragment.append(lanes);
  fragment.append(svg('g',{id:'scope-frame',class:'scope-frame','aria-hidden':true}));
  let focusTarget;
  for(const c of edges) {
    const key=JSON.stringify([c.from,c.to]);
    if(wiresDrawn.has(key)){wiresDrawn.get(key).records.push(c);continue;}
    const chosen=key===selectedWire;
    const activeEdge=chosen || (!selectedWire && (c.from===state.selected || c.to===state.selected));
    const label=state.byId.get(c.from).name+' → '+state.byId.get(c.to).name;
    const g=svg('g',{class:'wire'+(chosen?' selected':''),role:'button',tabindex:0,'data-wire':key,
      'aria-label':'接続　'+label,'aria-pressed':chosen});
    const path=svg('path',{class:'edge'+(activeEdge?' near':''),d:edgePath(chart.positions.get(c.from),chart.positions.get(c.to)),'data-from':c.from,'data-to':c.to,
      'marker-end':'url(#'+(activeEdge?'active-arrow':'arrow')+')'});
    const hit=svg('path',{class:'wire-hit',d:path.getAttribute('d')});
    g.append(svg('title',{},label+'　'+c.label),path,hit);fragment.append(g);
    wiresDrawn.set(key,{group:g,path,hit,records:[c]});
    if(active?.wire===key) focusTarget=g;
  }
  if(selectedWire && !wiresDrawn.has(selectedWire)) selectedWire=null;
  updateInputLines();
  const emphasized=selectedWire ? new Set(JSON.parse(selectedWire)) : near;
  for(const n of nodes){
    const p=chart.positions.get(n.id), findings=issues(n.id), selected=!selectedWire && n.id===state.selected;
    const g=svg('g',{class:'node'+(selected?' selected':'')+(findings.length?' flagged':'')+(emphasized.has(n.id)?' near':''),
      transform:'translate('+p.x+' '+p.y+')',role:'button',tabindex:0,'aria-pressed':selected,'data-id':n.id});
    g.append(svg('rect',{class:'box',width:p.w,height:p.h,rx:8}));
    if(selected) g.append(svg('rect',{class:'selection-frame',x:-4,y:-4,width:p.w+8,height:p.h+8,rx:10}));
    const color=n.kind==='repository'?'#68487a':n.kind==='store'?'#29676c':'#915031';
    g.append(svg('path',{class:'glyph',d:glyphs[n.kind],transform:'translate(9 8)',color}));
    g.append(svg('text',{class:'node-type',x:32,y:23},fit(n.type || kindNames.get(n.kind),p.w-42,12)));
    g.append(svg('text',{class:'node-title',x:11,y:48},fit(n.name,p.w-21,15,true)));
    if(findings.length){
      g.dataset.severity=findings[0].severity;
      for(const {severity,label,x,y,w} of p.badges){
        const badge=svg('g',{class:'severity-badge','data-severity':severity,transform:'translate('+x+' '+y+')'});
        badge.append(svg('rect',{width:w,height:19,rx:4}),svg('text',{x:7,y:14},label));
        g.append(badge);
      }
      g.append(svg('text',{class:'node-issue',x:11,y:p.h-9},fit(findings[0].title,p.w-21,12)));
    }
    const visibleLabel=[...g.querySelectorAll('text')].map(t=>t.textContent).join(' ');
    if(p.actorSpace) g.append(userPeg());
    const inside=scopeMembers.has(n.id);
    g.dataset.inScope=String(inside);
    if(editingScope) g.setAttribute('aria-pressed',inside);
    g.setAttribute('aria-label',(p.actorSpace?'利用者が操作する画面。':'')+visibleLabel+'。'+n.name+'、'+kindNames.get(n.kind)+'、指摘'+findings.length+'件'+(inside?'、利用者の関与範囲内':''));
    nodesDrawn.set(n.id,g);
    if(active?.id===n.id) focusTarget=g;
    fragment.append(g);
  }
  diagram.replaceChildren(fragment);
  drawScope();
  diagram.classList.toggle('scope-editing',editingScope);
  $('scope-toggle').setAttribute('aria-pressed',editingScope);
  $('scope-toggle').textContent=editingScope?'指定を終える':'範囲を指定';
  const visibleScope=nodes.filter(n=>scopeMembers.has(n.id)).length;
  $('scope-count').textContent=visibleScope===scopeMembers.size?scopeMembers.size+'要素':'表示'+visibleScope+' / 全'+scopeMembers.size+'要素';
  $('scope-help').hidden=!editingScope;
  if(initial && width>=580) fitMap();
  else {
    if(initial) camera={x:0,y:0,zoom:1};
    updateCamera();
  }
  if(focusTarget) focusTarget.focus({preventScroll:true});
}

function select(id,showDetail=false) {
  if(!state.byId.has(id)) return;
  selectedWire=null;
  state.selected=id;
  $('choose').value=id;
  draw();details();
  reveal(nodesDrawn.get(id));
  $('status').textContent=state.byId.get(id).name+'、指摘'+issues(id).length+'件を選択しました。';
  if(showDetail && matchMedia('(max-width:900px)').matches) {
    $('detail').focus({preventScroll:true});$('detail').scrollIntoView({block:'start'});
  }
}
function selectWire(key,choices=[]) {
  if(!wiresDrawn.has(key)) return;
  selectedWire=key;wireChoices=choices;draw();details();
  $('choose').value='';
  $('status').textContent='接続を選択しました。'+$('detail-name').textContent;
  if(matchMedia('(max-width:900px)').matches) $('detail').focus();
}
function updateInputLines(){
  let marked=false;
  for(const {group,records} of wiresDrawn.values()){
    const modes=new Set(records.map(c=>c.userInput?.mode || ''));
    const input=modes.has('ui')?'ui':modes.size===1 && modes.has('internal')?'internal':'';
    group.dataset.input=input;
    if(modes.has('ui') || modes.has('internal')) marked=true;
    const {from,to}=records[0];
    const label=state.byId.get(from).name+' → '+state.byId.get(to).name;
    const classes=[...modes].map(mode=>inputModes.get(mode)).join('・');
    group.setAttribute('aria-label','接続　'+label+'、'+classes);
    group.querySelector('title').textContent=label+'　'+records.map(c=>c.label).join('・')+'（'+classes+'）';
  }
  $('input-legend').hidden=!marked;
}
function inputEditor(connection){
  const label=html('label','利用者の入力経路','connection-choice'), select=html('select');
  for(const [mode,name] of inputModes) select.add(new Option(name,mode));
  select.value=connection.userInput?.mode || '';
  label.append(select);
  const scopeLabel=html('label','画面から入力できる範囲（任意）','connection-choice');
  const scope=html('textarea');
  scope.maxLength=600;scope.rows=2;
  scope.value=connection.userInput?.scope || '';
  scopeLabel.append(scope);
  scopeLabel.hidden=select.value!=='ui';
  select.addEventListener('change',()=>{
    if(select.value) connection.userInput={mode:select.value};
    else delete connection.userInput;
    scope.value='';
    scopeLabel.hidden=select.value!=='ui';
    setEdited(true);draw();
  });
  scope.addEventListener('input',()=>{
    const value=scope.value.trim();
    if(value) connection.userInput.scope=value;
    else delete connection.userInput.scope;
    setEdited(true);
  });
  return [label,scopeLabel];
}
function details(){
  $('save-changes').hidden=$('unsaved').hidden || !selectedWire;
  if(selectedWire) {
    const records=wiresDrawn.get(selectedWire).records, {from,to}=records[0];
    const source=state.byId.get(from), target=state.byId.get(to);
    $('detail-kind').textContent='選択中の接続';
    $('detail-name').replaceChildren(html('span',source.name),' ',html('span','→ '+target.name));
    $('description').hidden=true;
    $('finding-count').textContent='接続の理由と根拠';
    $('findings').replaceChildren(...records.map(c=>{
      const box=html('div',undefined,'connection-record');
      box.setAttribute('role','group');box.setAttribute('aria-label',c.label);
      box.append(html('h4',c.label),html('p',c.evidence || '根拠の記録はありません。','link-evidence'),...inputEditor(c));
      return box;
    }));
    if(wireChoices.length>1) {
      const label=html('label','この位置の接続','connection-choice'), menu=html('select');
      for(const key of wireChoices) {
        const line=wiresDrawn.get(key)?.records[0];if(!line) continue;
        menu.add(new Option(state.byId.get(line.from).name+' → '+state.byId.get(line.to).name,key));
      }
      menu.id='wire-choice';menu.value=selectedWire;
      menu.addEventListener('change',()=>{selectWire(menu.value,wireChoices);$('wire-choice')?.focus({preventScroll:true});});
      label.append(menu);$('findings').append(label);
    }
    $('links-title').textContent='接続する要素';
    $('links').replaceChildren(...[[source,'接続元'],[target,'接続先']].map(([node,role])=>{
      const row=html('li'), button=html('button',node.name,'link');
      button.addEventListener('click',()=>select(node.id,true));
      row.append(html('p',role,'kind'),button);return row;
    }));
    return;
  }
  $('links-title').textContent='この要素の接続';
  const n=state.byId.get(state.selected), list=issues(n.id);
  $('detail-name').textContent=n.name;
  $('detail-kind').textContent=[kindNames.get(n.kind),n.type].filter(Boolean).join('　/　');
  $('description').textContent=n.description;
  $('description').hidden=!n.description;
  $('finding-count').textContent=list.length?'この要素の指摘　'+list.length+' 件':'この要素の指摘';
  const records=list.map(f=>{
    const item=html('article',undefined,'finding');
    item.dataset.severity=f.severity;
    const dl=html('dl');dl.append(html('dt','根拠'),html('dd',f.evidence),html('dt','確認・対応'),html('dd',f.action));
    item.append(html('span',levelName(f.severity),'level'),html('h3',f.title),dl);
    return item;
  });
  $('findings').replaceChildren(...(records.length?records:[html('p','このデータには指摘の記録がありません。','hint')]));
  const links=incident(n.id).map(c=>{
    const out=c.from===n.id, other=state.byId.get(out?c.to:c.from);
    const row=html('li');
    const open=html('button',c.label,'link');
    open.setAttribute('aria-label','入力経路を編集　'+c.label);
    open.addEventListener('click',()=>{
      const key=JSON.stringify([c.from,c.to]);
      if(!wiresDrawn.has(key)){state.mode='all';render();}
      selectWire(key);
      const index=wiresDrawn.get(key).records.indexOf(c);
      $('findings').querySelectorAll('.connection-record select')[index]?.focus();
    });
    const direction=html('p',(out?'接続先':'接続元')+'　·　','kind');
    direction.append(open);row.append(direction);
    const button=html('button',other.name,'link');
    button.addEventListener('click',()=>{
      if(state.mode==='repos' && other.kind!=='repository'){state.mode='all';state.selected=other.id;render();}
      select(other.id,true);
    });
    row.append(button);
    if(c.evidence) row.append(html('p',c.evidence,'link-evidence'));
    return row;
  });
  $('links').replaceChildren(...(links.length?links:[html('li','接続の記録はありません。','hint')]));
}
function render() {
  const nodes=eligible();
  $('repos').disabled=!state.map.nodes.some(n=>n.kind==='repository');
  $('repos').setAttribute('aria-pressed',state.mode==='repos');$('all').setAttribute('aria-pressed',state.mode==='all');
  $('choose').replaceChildren(...nodes.map(n=>new Option(n.name+(issues(n.id).length?'　· 指摘あり':''),n.id)));
  const wireOption=new Option('接続線を選択中','');wireOption.disabled=true;wireOption.hidden=true;
  $('choose').prepend(wireOption);
  $('choose').value=state.selected;
  $('map-title').textContent=state.mode==='repos'?'リポジトリの依存関係':'構成と接続';
  $('flags').replaceChildren();
  const flagged=nodes.filter(n=>issues(n.id).length);
  if(flagged.length) {
    $('flags').append(html('h3','指摘のある要素'));
    const more=html('details');
    more.append(html('summary','ほか '+Math.max(0,flagged.length-4)+' 件'));
    flagged.forEach((n,i)=>{
      const button=html('button',n.name,'chip');
      button.dataset.severity=issues(n.id)[0].severity;
      button.addEventListener('click',()=>select(n.id,true));
      (i<4 ? $('flags') : more).append(button);
    });
    if(flagged.length>4) $('flags').append(more);
  }
  $('note').textContent=state.note;
  draw();details();
}
for(const mode of ['all','repos']) $(mode).addEventListener('click',()=>{
  selectedWire=null;chart=null;
  state.mode=mode;const nodes=eligible();
  if(!nodes.some(n=>n.id===state.selected)) state.selected=initial(nodes);
  render();
});
$('choose').addEventListener('change',()=>select($('choose').value,true));
$('nearby').addEventListener('change',()=>{selectedWire=null;$('choose').value=state.selected;draw(true);details();});
function highlight(id) {
  if(selectedWire) return;
  for (const edge of $('diagram').querySelectorAll('.edge')) {
    const near=edge.dataset.from===id || edge.dataset.to===id;
    edge.classList.toggle('near',near);
    edge.setAttribute('marker-end',near?'url(#active-arrow)':'url(#arrow)');
  }
}
$('diagram').addEventListener('pointerover',e=>{
  const n=e.target.closest('.node'); if(e.pointerType==='mouse' && n) highlight(n.dataset.id);
});
$('diagram').addEventListener('pointerout',e=>{
  if(e.pointerType==='mouse' && !e.relatedTarget?.closest?.('.node')) highlight(state.selected);
});
$('diagram').addEventListener('focusin',e=>{
  if(e.target.dataset.id) highlight(e.target.dataset.id);
  if(e.target.matches(':focus-visible')) reveal(e.target);
});
$('diagram').addEventListener('focusout',e=>{if(!e.relatedTarget?.closest?.('.node')) highlight(state.selected);});
function moveNode(id,dx,dy) {
  const p=chart.positions.get(id);
  if(!p || (!dx && !dy)) return;
  const offset=offsets[state.mode].get(id) || {x:0,y:0};
  offsets[state.mode].set(id,{x:offset.x+dx,y:offset.y+dy});
  p.x+=dx;p.y+=dy;delete p.bandTop;
  nodesDrawn.get(id).setAttribute('transform','translate('+p.x+' '+p.y+')');
  if(scopeMembers.size) drawScope();
  $('reset-layout').disabled=false;
  for(const {records,path,hit} of wiresDrawn.values()) {
    const {from,to}=records[0];
    if(from!==id && to!==id) continue;
    const d=edgePath(chart.positions.get(from),chart.positions.get(to));
    path.setAttribute('d',d);hit.setAttribute('d',d);
  }
}
const pointers=new Map();
let gesture=null, pinch=null;
function resetGestures() {
  $('scope-marquee')?.remove();
  for(const id of pointers.keys()) if(diagram.hasPointerCapture(id)) diagram.releasePointerCapture(id);
  pointers.clear();gesture=null;pinch=null;diagram.classList.remove('dragging');
}
function fingerPair() {
  const [a,b]=pointers.values();
  return {x:(a.x+b.x)/2,y:(a.y+b.y)/2,distance:Math.max(1,Math.hypot(a.x-b.x,a.y-b.y))};
}
function wireDistance(path,point) {
  const length=path.getTotalLength(), segments=Math.max(4,Math.min(180,Math.ceil(length*camera.zoom/5)));
  let a=path.getPointAtLength(0), best=Infinity;
  for(let i=1;i<=segments;i++) {
    const b=path.getPointAtLength(length*i/segments),dx=b.x-a.x,dy=b.y-a.y;
    const t=Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/(dx*dx+dy*dy || 1)));
    best=Math.min(best,Math.hypot(point.x-a.x-t*dx,point.y-a.y-t*dy));a=b;
  }
  return best;
}
function activate(target,event) {
  if(editingScope){
    if(target?.classList.contains('node')) editScope([target.dataset.id],true);
    return;
  }
  if(target?.classList.contains('wire')) {
    if(!event){selectWire(target.dataset.wire);return;}
    const {clientX,clientY}=event, pixel=viewportPoint(clientX,clientY);
    const point={x:camera.x+pixel.x/camera.zoom,y:camera.y+pixel.y/camera.zoom};
    const keys=new Set(document.elementsFromPoint(clientX,clientY).map(el=>el.closest('.wire')?.dataset.wire).filter(Boolean));
    if(!keys.size){selectWire(target.dataset.wire);return;}
    if(keys.size===1){selectWire([...keys][0]);return;}
    const hits=[...keys].map(key=>({key,d:wireDistance(wiresDrawn.get(key).path,point)})).sort((a,b)=>a.d-b.d);
    selectWire(hits[0].key,hits.map(hit=>hit.key));
  }
  else if(target?.classList.contains('node')) select(target.dataset.id,true);
}
diagram.addEventListener('pointerdown',e=>{
  if(e.button!==0 && e.button!==1) return;
  if(e.button===1) e.preventDefault();
  const point=viewportPoint(e.clientX,e.clientY);
  pointers.set(e.pointerId,point);diagram.setPointerCapture(e.pointerId);
  if(pointers.size===1) {
    const target=e.button===0 ? e.target.closest('.node,.wire') : null;
    target?.focus({preventScroll:true});
    gesture={start:point,last:point,target,moved:false,selectArea:editingScope && e.button===0};
  } else if(pointers.size===2) {
    $('scope-marquee')?.remove();
    const pair=fingerPair();
    pinch={x:camera.x+pair.x/camera.zoom,y:camera.y+pair.y/camera.zoom,
      zoom:camera.zoom,distance:pair.distance};
    gesture=null;
  }
});
diagram.addEventListener('pointermove',e=>{
  if(!pointers.has(e.pointerId)) return;
  const point=viewportPoint(e.clientX,e.clientY);
  pointers.set(e.pointerId,point);
  if(pinch && pointers.size===2) {
    const pair=fingerPair(), zoom=Math.min(4,Math.max(.05,pinch.zoom*pair.distance/pinch.distance));
    camera={zoom,x:pinch.x-pair.x/zoom,y:pinch.y-pair.y/zoom};updateCamera();return;
  }
  if(!gesture) return;
  if(!gesture.moved && Math.hypot(point.x-gesture.start.x,point.y-gesture.start.y)<4) return;
  gesture.moved=true;diagram.classList.add('dragging');
  if(gesture.selectArea){
    const a=gesture.start;
    const x=camera.x+Math.min(a.x,point.x)/camera.zoom,y=camera.y+Math.min(a.y,point.y)/camera.zoom;
    const w=Math.abs(point.x-a.x)/camera.zoom,h=Math.abs(point.y-a.y)/camera.zoom;
    gesture.area={x,y,w,h};
    let rect=$('scope-marquee');
    if(!rect){rect=svg('rect',{id:'scope-marquee',class:'scope-marquee'});diagram.append(rect);}
    for(const [k,v] of Object.entries({x,y,width:w,height:h})) rect.setAttribute(k,v);
    return;
  }
  const dx=(point.x-gesture.last.x)/camera.zoom,dy=(point.y-gesture.last.y)/camera.zoom;
  const id=gesture.target?.dataset.id;
  if(id) moveNode(id,dx,dy);
  else {camera.x-=dx;camera.y-=dy;updateCamera();}
  gesture.last=point;
});
function endPointer(e) {
  if(!pointers.delete(e.pointerId)) return;
  const tapped=e.type==='pointerup' && gesture && !gesture.moved ? gesture.target : null;
  const area=e.type==='pointerup' && gesture?.area;
  $('scope-marquee')?.remove();
  gesture=null;pinch=null;diagram.classList.remove('dragging');
  if(area){
    const ids=[];
    for(const [id,p] of chart.positions){
      const x=p.x+p.w/2,y=p.y+p.h/2;
      if(x>=area.x && x<=area.x+area.w && y>=area.y && y<=area.y+area.h && !scopeMembers.has(id)) ids.push(id);
    }
    if(ids.length) editScope(ids);
  } else if(tapped) activate(tapped,e);
}
for(const type of ['pointerup','pointercancel','lostpointercapture']) diagram.addEventListener(type,endPointer);
diagram.addEventListener('click',e=>{if(e.detail===0) activate(e.target.closest('.node,.wire'));});
frame.addEventListener('keydown',e=>{
  if(e.key==='Escape' && editingScope){setScopeMode(false);$('scope-toggle').focus();return;}
  const target=e.target.closest('.node,.wire');
  if((e.key==='Enter'||e.key===' ') && target){e.preventDefault();activate(target);return;}
  const step=e.shiftKey?40:10, arrows={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]};
  if(Object.hasOwn(arrows,e.key)){
    e.preventDefault();const [x,y]=arrows[e.key];
    if(target?.dataset.id && !editingScope) {moveNode(target.dataset.id,x,y);reveal(target);}
    else {camera.x+=x/camera.zoom;camera.y+=y/camera.zoom;updateCamera();}
  } else if(e.key==='Home'){e.preventDefault();fitMap();}
  else if(!e.ctrlKey && !e.metaKey && ['+','=','-'].includes(e.key)){
    e.preventDefault();zoomAt(camera.zoom*(e.key==='-'?.8:1.25));
  }
});
diagram.addEventListener('wheel',e=>{
  if(e.deltaY===0) return;
  e.preventDefault();zoomAt(camera.zoom*(e.deltaY<0?1.15:1/1.15),viewportPoint(e.clientX,e.clientY));
},{passive:false});
$('zoom-in').addEventListener('click',()=>zoomAt(camera.zoom*1.25));
$('zoom-out').addEventListener('click',()=>zoomAt(camera.zoom*.8));
$('fit-map').addEventListener('click',fitMap);
$('scope-toggle').addEventListener('click',()=>setScopeMode(!editingScope));
function downloadMap(){
  try{
    const blob=new Blob([serializeMap(state.map,state.original)],{type:'application/json'});
    const url=URL.createObjectURL(blob), link=html('a');
    link.href=url;link.download='architecture-map.json';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    if(document.activeElement===$('save-changes')) $('findings').querySelector('select')?.focus({preventScroll:true});
    $('error').hidden=true;
    $('status').textContent='JSONのダウンロードを開始しました。';
    return true;
  }catch(e){error('構成図を保存できません。'+e.message);return false;}
}
function saveMap(){
  if(downloadMap() && !$('unsaved').hidden) {
    $('edited-label').hidden=true;
    $('confirm-saved').hidden=false;
  }
}
$('confirm-saved').addEventListener('click',()=>{
  setEdited(false);
  $('save-map').focus({preventScroll:true});
  $('status').textContent='構成図の保存を確認しました。';
});
$('save-map').addEventListener('click',saveMap);
$('scope-save').addEventListener('click',saveMap);
$('save-changes').addEventListener('click',saveMap);
$('reset-layout').addEventListener('click',()=>{offsets[state.mode].clear();draw();fitMap();});
$('return').addEventListener('click',()=>{const target=diagram.querySelector('[aria-pressed="true"]');target?.focus();frame.scrollIntoView({block:'center'});});
$('file').addEventListener('change',e=>{const files=[...e.target.files];e.target.value='';if(files.length) openFiles(files);});
$('reset').addEventListener('click',()=>loadChoice('sample'));
mapChoice.addEventListener('change',()=>{
  const requested=mapChoice.value;
  syncChoice();
  loadChoice(requested);
});
let chartWidth=0;
new ResizeObserver(([entry])=>{if(entry.contentRect.width!==chartWidth){chartWidth=entry.contentRect.width;draw(true);}else if(chart)updateCamera();}).observe(frame);
loadChoices();
if (!blueView && params.has('map')) loadChoice('map:'+params.get('map'),{fallback:true});
else loadChoice(params.get('system')==='aym' ? 'aym' : 'sample');

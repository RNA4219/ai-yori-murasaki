import { parseFile, importDocuments, validateMap, MAX_BYTES, kindNames, severityNames } from './model.mjs';

const $=id=>document.getElementById(id);
const ns='http://www.w3.org/2000/svg';
const priorities=[...severityNames.keys()];
const state={map:null,byId:new Map(),selected:null,mode:'all',packages:new Map(),load:0,note:''};
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
function issues(id) { return state.map.findings.filter(f=>f.node===id); }
function eligible() { return state.map.nodes.filter(n=>state.mode==='all' || n.kind==='repository'); }
function incident(id) { return state.map.connections.filter(c=>c.from===id || c.to===id); }
function initial(nodes) {
  const wanted=new Set(nodes.map(n=>n.id));
  return state.map.findings.find(f=>wanted.has(f.node))?.node ?? nodes[0].id;
}

function apply(result,sample=false) {
  state.map=result.map;
  state.map.findings.sort((a,b)=>priorities.indexOf(a.severity)-priorities.indexOf(b.severity));
  state.byId=new Map(state.map.nodes.map(n=>[n.id,n]));
  state.mode='all';
  state.packages=result.packages || new Map();
  state.selected=initial(state.map.nodes);
  state.note=result.note || '';
  $('nearby').checked=false;
  $('workspace').hidden=false;
  $('error').hidden=true;
  $('reset').hidden=sample;
  $('name').textContent=state.map.name;
  $('source').textContent=sample ? '見本データ' : '読み込み元　'+result.source;
  $('counts').textContent=state.map.nodes.length+' 要素　／　'+state.map.connections.length+' 接続　／　'+state.map.findings.length+' 件の指摘';
  render();
}
function error(message) { $('error').textContent=message; $('error').hidden=false; }
async function sample() {
  const version=++state.load;
  try {
    const response=await fetch('/architecture/sample.json',{credentials:'same-origin'});
    if (!response.ok) throw Error('見本ファイルを取得できませんでした。');
    const map=validateMap(await response.json());
    if (version===state.load) apply({map,note:'架空の構成とレビュー記録を使用しています。'},true);
  } catch(e) { if(version===state.load) error(e.message); }
}
async function openFiles(files) {
  const version=++state.load;
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
    if(version===state.load) { apply(result); $('status').textContent='構成図を読み込みました。'+result.source; }
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
function layout(nodes,edges,width) {
  const repoOnly=nodes.every(n=>n.kind==='repository');
  const bands=repoOnly ? repoBands(nodes,edges) : [
    {label:'リポジトリ',nodes:nodes.filter(n=>n.kind==='repository')},
    {label:'サービス',nodes:nodes.filter(n=>n.kind==='service' || n.kind==='external')},
    {label:'データストア',nodes:nodes.filter(n=>n.kind==='store')}
  ].filter(b=>b.nodes.length);
  const vertical=width<580 || bands.length>4;
  const positions=new Map(), zones=[];
  const height=80, gap=36, margin=12;
  let bottom=0;
  if(vertical) {
    let top=14;
    for(const band of bands){
      const columns=width>=460 ? 3 : width>=290 ? 2 : 1;
      const cell=(width-margin*2)/columns;
      const bw=Math.min(186,cell-18);
      const start=band.label ? 32 : 12;
      const rows=Math.ceil(band.nodes.length/columns);
      const zoneHeight=start+rows*(height+20);
      zones.push({x:margin,y:top,w:width-margin*2,h:zoneHeight,label:band.label});
      band.nodes.forEach((n,i)=>positions.set(n.id,{x:margin+(i%columns+.5)*cell-bw/2,y:top+start+Math.floor(i/columns)*(height+20),w:bw,h:height,bandTop:top,bandBottom:top+zoneHeight}));
      top+=zoneHeight+22;
    }
    bottom=top;
  } else {
    const widthPer=(width-2*margin)/bands.length;
    const bw=Math.min(205,widthPer-46);
    const rows=Math.max(...bands.map(b=>b.nodes.length));
    bottom=Math.max(300,rows*(height+gap)+64);
    bands.forEach((band,col)=>{
      const left=margin+col*widthPer;
      zones.push({x:left+2,y:16,w:widthPer-4,h:bottom-32,label:band.label});
      const firstY=(bottom-band.nodes.length*(height+gap)+gap)/2+10;
      band.nodes.forEach((n,i)=>positions.set(n.id,{x:left+(widthPer-bw)/2,y:firstY+i*(height+gap),w:bw,h:height}));
    });
  }
  return {positions,zones,height:bottom};
}
function edgePath(a,b) {
  const ax=a.x+a.w/2, ay=a.y+a.h/2, bx=b.x+b.w/2, by=b.y+b.h/2;
  if(a.bandTop!==undefined && a.bandTop!==b.bandTop) {
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
function draw(){
  if(!state.map) return;
  const visible=eligible(), near=new Set([state.selected]);
  for(const c of incident(state.selected)){near.add(c.from);near.add(c.to);}
  const nodes=$('nearby').checked ? visible.filter(n=>near.has(n.id)) : visible;
  const ids=new Set(nodes.map(n=>n.id));
  const edges=state.map.connections.filter(c=>ids.has(c.from)&&ids.has(c.to));
  const width=Math.max(235,$('frame').clientWidth);
  const chart=layout(nodes,edges,width);
  const diagram=$('diagram'), active=document.activeElement?.closest?.('.node')?.dataset.id;
  diagram.setAttribute('viewBox','0 0 '+width+' '+chart.height);
  diagram.setAttribute('width',width);diagram.setAttribute('height',chart.height);
  const fragment=document.createDocumentFragment(),defs=svg('defs');
  for(const [id,color] of [['arrow','#8c8192'],['active-arrow','#674779']]){
    const marker=svg('marker',{id,viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:6,markerHeight:6,orient:'auto'});
    marker.append(svg('path',{d:'M0 1 L9 5 L0 9 z',fill:color}));defs.append(marker);
  }
  fragment.append(defs);
  for(const z of chart.zones) if(z.label){
    fragment.append(svg('rect',{class:'lane',x:z.x,y:z.y,width:z.w,height:z.h,rx:9}));
    fragment.append(svg('text',{class:'lane-title',x:z.x+10,y:z.y+22},z.label));
  }
  const drawn=new Set();
  for(const c of edges) {
    const key=JSON.stringify([c.from,c.to]);
    if(drawn.has(key)) continue;
    drawn.add(key);
    const activeEdge=c.from===state.selected || c.to===state.selected;
    const path=svg('path',{class:'edge'+(activeEdge?' near':''),d:edgePath(chart.positions.get(c.from),chart.positions.get(c.to)),'data-from':c.from,'data-to':c.to,
      'marker-end':'url(#'+(activeEdge?'active-arrow':'arrow')+')'});
    path.append(svg('title',{},state.byId.get(c.from).name+' → '+state.byId.get(c.to).name+'　'+c.label));
    fragment.append(path);
  }
  let focusTarget;
  for(const n of nodes){
    const p=chart.positions.get(n.id), findings=issues(n.id), selected=n.id===state.selected;
    const g=svg('g',{class:'node'+(selected?' selected':'')+(findings.length?' flagged':'')+(near.has(n.id)?' near':''),
      transform:'translate('+p.x+' '+p.y+')',role:'button',tabindex:0,'aria-pressed':selected,'data-id':n.id});
    g.append(svg('rect',{class:'box',width:p.w,height:p.h,rx:8}));
    const color=n.kind==='repository'?'#68487a':n.kind==='store'?'#29676c':'#915031';
    g.append(svg('path',{class:'glyph',d:glyphs[n.kind],transform:'translate(9 8)',color}));
    g.append(svg('text',{class:'node-type',x:32,y:23},fit(n.type || kindNames.get(n.kind),p.w-(findings.length?61:42),12)));
    g.append(svg('text',{class:'node-title',x:11,y:48},fit(n.name,p.w-21,15,true)));
    if(findings.length){
      g.append(svg('text',{class:'node-issue',x:11,y:68},fit('指摘　'+findings[0].title,p.w-21,12)));
      g.append(svg('circle',{class:'alert-icon',cx:p.w-13,cy:15,r:9}),svg('text',{class:'alert-text',x:p.w-13,y:19,'text-anchor':'middle'},'!'));
    }
    const visibleLabel=[...g.querySelectorAll('text')].map(t=>t.textContent).join(' ');
    g.setAttribute('aria-label',visibleLabel+'。'+n.name+'、'+kindNames.get(n.kind)+'、指摘'+findings.length+'件');
    if(active===n.id) focusTarget=g;
    fragment.append(g);
  }
  diagram.replaceChildren(fragment);
  if(focusTarget) focusTarget.focus({preventScroll:true});
}

function select(id,showDetail=false) {
  if(!state.byId.has(id)) return;
  state.selected=id;
  $('choose').value=id;
  draw();details();
  $('status').textContent=state.byId.get(id).name+'、指摘'+issues(id).length+'件を選択しました。';
  if(showDetail && matchMedia('(max-width:900px)').matches) {
    $('detail').focus({preventScroll:true});$('detail').scrollIntoView({block:'start'});
  }
}
function details(){
  const n=state.byId.get(state.selected), list=issues(n.id);
  $('detail-name').textContent=n.name;
  $('detail-kind').textContent=[kindNames.get(n.kind),n.type].filter(Boolean).join('　/　');
  $('description').textContent=n.description;
  $('description').hidden=!n.description;
  $('finding-count').textContent=list.length?'この要素の指摘　'+list.length+' 件':'この要素の指摘';
  const records=list.map(f=>{
    const item=html('article',undefined,'finding');
    const dl=html('dl');dl.append(html('dt','根拠'),html('dd',f.evidence),html('dt','確認・対応'),html('dd',f.action));
    item.append(html('span','重要度　'+severityNames.get(f.severity),'level'),html('h3',f.title),dl);
    return item;
  });
  $('findings').replaceChildren(...(records.length?records:[html('p','このデータには指摘の記録がありません。','hint')]));
  const links=incident(n.id).map(c=>{
    const out=c.from===n.id, other=state.byId.get(out?c.to:c.from);
    const row=html('li');
    row.append(html('p',(out?'接続先':'接続元')+'　·　'+c.label,'kind'));
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
      button.addEventListener('click',()=>select(n.id,true));
      (i<4 ? $('flags') : more).append(button);
    });
    if(flagged.length>4) $('flags').append(more);
  }
  $('note').textContent=state.note;
  draw();details();
}
for(const mode of ['all','repos']) $(mode).addEventListener('click',()=>{
  state.mode=mode;const nodes=eligible();
  if(!nodes.some(n=>n.id===state.selected)) state.selected=initial(nodes);
  render();
});
$('choose').addEventListener('change',()=>select($('choose').value,true));
$('nearby').addEventListener('change',draw);
function highlight(id) {
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
$('diagram').addEventListener('focusin',e=>{if(e.target.dataset.id) highlight(e.target.dataset.id);});
$('diagram').addEventListener('focusout',e=>{if(!e.relatedTarget?.closest?.('.node')) highlight(state.selected);});
$('diagram').addEventListener('click',e=>{const node=e.target.closest('.node');if(node) select(node.dataset.id,true);});
$('diagram').addEventListener('keydown',e=>{
  if(e.key==='Enter'||e.key===' '){const node=e.target.closest('.node');if(node){e.preventDefault();select(node.dataset.id,true);}}
});
$('return').addEventListener('click',()=>{const target=$('diagram').querySelector('[aria-pressed="true"]');target?.focus();target?.scrollIntoView({block:'center'});});
$('file').addEventListener('change',e=>{const files=[...e.target.files];e.target.value='';if(files.length) openFiles(files);});
$('reset').addEventListener('click',sample);
let chartWidth=0;
new ResizeObserver(([entry])=>{if(entry.contentRect.width!==chartWidth){chartWidth=entry.contentRect.width;draw();}}).observe($('frame'));
sample();

/* The Slate page engine (S1). Ink kit copied from slate-mockup v3; the page API is window.slate. */
'use strict';
const NS='http://www.w3.org/2000/svg';
const $=s=>document.querySelector(s);
function el(n,a={},p){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e}
let uid=0;const f1=n=>Math.round(n*10)/10;
const rad=d=>d*Math.PI/180;

function cr(pts,per=10){
  if(pts.length<2)return pts.slice();
  const P=[pts[0],...pts,pts[pts.length-1]],out=[];
  for(let i=1;i<P.length-2;i++){
    const [p0,p1,p2,p3]=[P[i-1],P[i],P[i+1],P[i+2]];
    for(let s=0;s<per;s++){const t=s/per,t2=t*t,t3=t2*t;
      out.push([0,1].map(k=>0.5*((2*p1[k])+(-p0[k]+p2[k])*t+(2*p0[k]-5*p1[k]+4*p2[k]-p3[k])*t2+(-p0[k]+3*p1[k]-3*p2[k]+p3[k])*t3)));}
  }
  out.push(pts[pts.length-1]);return out;
}
function plen(S){let l=0;for(let i=1;i<S.length;i++)l+=Math.hypot(S[i][0]-S[i-1][0],S[i][1]-S[i-1][1]);return l}
function poly(S){return 'M'+S.map(p=>f1(p[0])+' '+f1(p[1])).join('L')}
function ribbon(S,w){
  const n=S.length,cum=[0];
  for(let i=1;i<n;i++)cum[i]=cum[i-1]+Math.hypot(S[i][0]-S[i-1][0],S[i][1]-S[i-1][1]);
  const tot=cum[n-1]||1,ph=Math.random()*6,L=[],R=[];
  for(let i=0;i<n;i++){
    const a=S[Math.max(0,i-1)],b=S[Math.min(n-1,i+1)];
    let dx=b[0]-a[0],dy=b[1]-a[1];const m=Math.hypot(dx,dy)||1;dx/=m;dy/=m;
    const t=cum[i]/tot;
    const press=0.32+0.68*Math.pow(Math.sin(Math.PI*Math.min(1,Math.max(0,t))),0.45);
    const hw=w*press*(1+0.09*Math.sin(cum[i]*0.09+ph))/2;
    L.push([S[i][0]-dy*hw,S[i][1]+dx*hw]);R.push([S[i][0]+dy*hw,S[i][1]-dx*hw]);
  }
  const fmt=A=>A.map(p=>f1(p[0])+' '+f1(p[1])).join('L');
  return 'M'+fmt(L)+'L'+fmt(R.reverse())+'Z';
}
/* write one stroke into group g; returns ms until finished (incl. delay) */
function ink(g,pts,o={}){
  const {w=7,color='var(--ink)',speed=.9,delay=0,j=1.0,per=10,minDur=140,opacity=1,ease='cubic-bezier(.42,.08,.4,1)'}=o;
  const P=pts.map(p=>[p[0]+(Math.random()-.5)*2*j,p[1]+(Math.random()-.5)*2*j]);
  const S=cr(P,per),len=plen(S)||1,id='m'+(++uid);
  const m=el('mask',{id,maskUnits:'userSpaceOnUse',x:-300,y:-300,width:5000,height:5000},g);
  const mp=el('path',{d:poly(S),fill:'none',stroke:'#fff','stroke-width':w*2.3,'stroke-linecap':'round','stroke-linejoin':'round'},m);
  const r=el('path',{d:ribbon(S,w*(0.92+Math.random()*.2)),mask:`url(#${id})`},g);
  r.style.fill=color;if(opacity!==1)r.style.opacity=opacity;
  mp.style.strokeDasharray=len+' '+(len+4);mp.style.strokeDashoffset=len;
  const dur=Math.max(minDur,len/speed);
  mp.animate([{strokeDashoffset:len},{strokeDashoffset:0}],{duration:dur/(window.FAST||1),delay:delay/(window.FAST||1),easing:ease,fill:'both'});
  return delay+dur;
}
function still(g,pts,w,color='#1a1f2b',opacity=1,per=8){
  const r=el('path',{d:ribbon(cr(pts,per),w)},g);r.style.fill=color;if(opacity!==1)r.style.opacity=opacity;return r;
}
function loop(cx,cy,rx,ry,a0=-1.95,sweep=2*Math.PI+.5,n=16){
  const pts=[];
  for(let i=0;i<=n;i++){const t=i/n,a=a0+t*sweep,k=1+0.03*Math.sin(a*2+1)+0.07*t*t;
    pts.push([cx+Math.cos(a)*rx*k,cy+Math.sin(a)*ry*k*(1-0.02*t)]);}
  return pts;
}
const RUNFAST=()=>window.FAST||1;
const sleep=ms=>new Promise(r=>setTimeout(r,ms/RUNFAST()));
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
let data=null,parts=[],gen=0;

/* ---- maths ---- */
function katexHTML(tex,inline){
  try{return katex.renderToString(tex,{throwOnError:false,strict:'ignore',trust:c=>c.command==='\\htmlId'&&/^ans\d+$/.test(c.id)})}
  catch(e){return esc(tex)}
}
/* index of the last "=" outside braces, or -1 */
function lastEq(t){let d=0,k=-1;for(let i=0;i<t.length;i++){const c=t[i];
  if(c==='\\'){i++;continue}
  if(c==='{')d++;else if(c==='}')d--;else if(c==='='&&d===0)k=i}
  return k}
/* Bug 1: the answer is its own element (\htmlId), so the red mark measures it and nothing else. */
function mathTex(b,i){
  let t=String(b.tex||'');const id='ans'+i;
  if(t.includes('\\ans')&&b.ans&&b.ans.value!=null)return t.split('\\ans').join('\\htmlId{'+id+'}{'+b.ans.value+'}');
  if(b.mark){const k=lastEq(t);return k<0?'\\htmlId{'+id+'}{'+t+'}':t.slice(0,k+1)+'\\htmlId{'+id+'}{'+t.slice(k+1)+'}'}
  return t;
}
/* Bug 2: "$...$" inside a sentence is KaTeX, the rest is words. Each unit is one wipeable piece. */
function units(text){
  const out=[],re=/\$([^$]+)\$/g;let last=0,m;
  const words=s=>s.split(/\s+/).filter(Boolean).forEach(w=>out.push({html:esc(w),w:w.length}));
  while((m=re.exec(text))){words(text.slice(last,m.index));out.push({html:katexHTML(m[1],true),w:Math.max(2,m[1].length),math:true});last=re.lastIndex}
  words(text.slice(last));return out;
}
const wordSpans=text=>units(text).map(u=>`<span class="w${u.math?' m':''}" data-w="${u.w}">${u.html}</span>`).join('');


/* ---- S4: diagrams. Python (slate_geo.py) sends exact strokes in a 1000-wide grid; here we only add a light hand wobble. ---- */
const WOBBLE_PX=1.2;   /* at most 1.5 px on screen, always */
/* Light wobble along the normal. Zero at both ends of an open stroke (so shared points stay shared); periodic on a closed one
   (its first and last point get the same shift, so it still closes). amp is in grid units. */
function wobble(pts,closed,amp,seed=0){
  const D=[pts[0]];
  for(let i=1;i<pts.length;i++){
    const a=pts[i-1],b=pts[i],n=Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/40));
    for(let k=1;k<=n;k++)D.push([a[0]+(b[0]-a[0])*k/n,a[1]+(b[1]-a[1])*k/n]);
  }
  if(closed)D[D.length-1]=D[0].slice();
  const n=D.length,cum=[0];
  for(let i=1;i<n;i++)cum[i]=cum[i-1]+Math.hypot(D[i][0]-D[i-1][0],D[i][1]-D[i-1][1]);
  const L=cum[n-1]||1,TAU=2*Math.PI,r=k=>{const x=Math.sin(seed*12.9898+k*78.233)*43758.5453;return x-Math.floor(x)};
  const f1=closed?2:Math.max(1,Math.round(L/300)),f2=closed?3:Math.max(2,Math.round(L/170)),p1=r(1)*TAU,p2=r(2)*TAU;
  return D.map((q,i)=>{
    const u=cum[i]/L;
    const pa=D[closed?(i===0||i===n-1?n-2:i-1):Math.max(0,i-1)],pb=D[closed?(i===0||i===n-1?1:i+1):Math.min(n-1,i+1)];
    let dx=pb[0]-pa[0],dy=pb[1]-pa[1];const m=Math.hypot(dx,dy)||1;dx/=m;dy/=m;
    const env=closed?1:Math.pow(Math.sin(Math.PI*u),0.6);
    const d=amp*env*(0.6*Math.sin(TAU*f1*u+p1)+0.4*Math.sin(TAU*f2*u+p2));
    return [q[0]-dy*d,q[1]+dx*d];
  });
}
const geoInk=c=>c==='axis'?'var(--ink2)':c==='red'?'var(--red)':'var(--ink)';
/* a label may hold $...$ maths: those parts are KaTeX, the rest plain words */
const labHTML=t=>String(t).split(/\$([^$]*)\$/).map((x,i)=>i%2?katexHTML(x,true):esc(x)).join('');
function geoHTML(g,i){
  const lab=g.labels.map(l=>`<span class="w gl${l.tick?' tk':''}${l.c==='red'?' rd':''}${l.at?' at':''}" data-w="${Math.max(2,l.s.length)}" style="left:${(l.x/g.w*100).toFixed(2)}%;top:${(l.y/g.h*100).toFixed(2)}%${l.at?`;width:${(l.w/g.w*100).toFixed(2)}%;height:${(l.h/g.w*100).toFixed(2)}cqw;line-height:${(l.h/g.w*100).toFixed(2)}cqw`:''}">${labHTML(l.s)}</span>`).join('');
  return `<div class="dg geo" id="b${i}" style="--h:${g.h};--r:${g.w/g.h}"><svg viewBox="0 0 ${g.w} ${g.h}" width="100%"></svg>${lab}</div>`;
}
/* ink every stroke of block i, one after another, within about ms; resolves with the ms used */
function drawGeo(i,ms){
  const g=data.blocks[i].geo,root=$('#'+parts[i].id),svg=root.querySelector('svg');
  const sc=(svg.getBoundingClientRect().width||640)/g.w,amp=WOBBLE_PX/sc;
  const grp=el('g',{},svg),S=g.strokes.map((s,k)=>{
    let w=wobble(s.pts,s.closed,amp,k+1);
    if(s.closed)w=w.concat([w[1],w[2]]);                /* run over the start again: the loop is closed, not butted */
    return {s,pts:w,len:plen(w)*(s.minor?.4:1)};
  });
  const tot=S.reduce((a,x)=>a+x.len,0)||1,budget=ms*.92;let t=0;
  S.forEach(x=>{
    const dur=Math.max(45,budget*x.len/tot);
    ink(grp,x.pts,{w:x.s.w,color:geoInk(x.s.c),speed:plen(x.pts)/dur,delay:t,j:0,per:x.s.k==='line'||x.s.k==='dash'||x.s.k==='tick'?4:8,minDur:45});
    t+=dur;
  });
  g.dots.forEach(d=>{const c=el('circle',{cx:d.x,cy:d.y,r:d.r?d.r+.5:7.5,fill:geoInk(d.c)},grp);c.style.opacity=0;
    c.animate([{opacity:0,transform:'scale(.2)'},{opacity:1,transform:'scale(1)'}],{duration:160/RUNFAST(),delay:t/RUNFAST(),fill:'forwards'});c.style.transformOrigin=d.x+'px '+d.y+'px'});
  return t+200;
}

/* ---- the page: one row per block, red notes sit under the block above ---- */
function build(s,from=0){
  const blocks=s.blocks||[];if(!from)parts=[];let h='',open=false;
  const close=()=>{if(open){h+='</div>';open=false}};
  blocks.forEach((b,i)=>{
    if(i<from)return;
    const k=b.k;
    if(k==='note'&&open){h+=`<span class="note${blocks[i-1]&&blocks[i-1].mark?' am':''}" id="b${i}">${wordSpans(b.text||'')}</span>`;parts[i]={k,id:'b'+i};return}
    close();h+='<div class="ln">';open=true;
    if(k==='math')h+=`<span class="pc eq" id="b${i}" style="filter:url(#roughText)">${katexHTML(mathTex(b,i))}</span>`;
    else if(k==='text')h+=`<span class="sent" id="b${i}">${wordSpans(b.text||'')}</span>`;
    else if(k==='note')h+=`<span class="note solo" id="b${i}">${wordSpans(b.text||'')}</span>`;
    else if(k==='steps')h+=`<ol class="steps" id="b${i}">`+(b.items||[]).map((t,j)=>`<li><span class="num pc" style="filter:url(#roughText)">${j+1}</span><p>${wordSpans(t)}</p></li>`).join('')+'</ol>';
    else if(k==='turn')h+=`<div class="turn" id="b${i}"><div class="sent">${wordSpans(b.prompt||b.text||'Your turn')}</div><div class="space"></div></div>`;
    else if(b.geo)h+=geoHTML(b.geo,i);
    else h+=`<div class="dg pc" id="b${i}"><span>diagram</span></div>`;   /* chemistry things arrive in S5 */
    parts[i]={k,id:'b'+i};
  });
  close();return h;
}
/* every piece a block wipes in, with its share of the time */
function pieces(i){
  const root=$('#'+parts[i].id);if(!root)return [];
  if(root.classList.contains('pc'))return [{e:root,w:1}];
  const ps=[];
  root.querySelectorAll('.num,.w').forEach(e=>ps.push({e,w:e.classList.contains('num')?2:+e.dataset.w||3}));
  if(!ps.length)ps.push({e:root,w:1});
  const sp=root.querySelector('.space');if(sp)ps.push({e:sp,w:3});
  return ps;
}
/* Bug 3: Web Animations, driven by .finished. The final state (class "on") is set on finish, cancel and a timeout. */
/* S6: `cur` is the line being inked. Its `rate` is how fast its animations run against their planned length; retime() changes it
   so the wipe lands exactly when the audio does. `nom` is planned ms done so far (animation time, not wall time). */
let cur=null;
const nowMs=()=>performance.now();
/* All of a line's pieces are started together, each with a delay (the sum of the ones before it), instead of one after another:
   waiting on every piece in turn costs a frame each, which added up to over 100 ms on a diagram with a dozen labels. */
function wipes(items,c){
  return new Promise(done=>{
    let over=false,t=0,off=0;const as=[];
    const kf=[{maskPosition:'100% 0',webkitMaskPosition:'100% 0'},{maskPosition:'0 0',webkitMaskPosition:'0 0'}];
    const end=()=>{if(over)return;over=true;clearTimeout(t);as.forEach(a=>a.cancel());items.forEach(x=>x.e.classList.add('on'));if(c.end===end)c.end=null;done()};
    for(const x of items){
      const dur=Math.max(40,x.ms)/RUNFAST();
      const a=x.e.animate(kf,{duration:dur,delay:off,easing:'cubic-bezier(.4,.1,.35,1)',fill:'both'});
      a.playbackRate=c.rate;as.push(a);off+=dur;
    }
    c.as=as;c.end=end;
    /* the stall guard: twice what is left at the current speed, re-armed whenever the speed changes */
    c.arm=()=>{clearTimeout(t);const last=as[as.length-1];const left=Math.max(0,off-(last.currentTime||0))/Math.max(last.playbackRate||1,.05);t=setTimeout(end,left*2+200)};
    c.arm();
    if(!as.length)return end();
    as[as.length-1].finished.then(end,end);
  });
}
function span(els){const r=els.map(e=>e.getBoundingClientRect());const l=Math.min(...r.map(q=>q.left)),t=Math.min(...r.map(q=>q.top)),rt=Math.max(...r.map(q=>q.right)),b=Math.max(...r.map(q=>q.bottom));
  return {getBoundingClientRect:()=>({left:l,top:t,right:rt,bottom:b,width:rt-l,height:b-t})}}
function markEl(e,kind,o={}){
  const pg=$('#pg'),pr=pg.getBoundingClientRect(),r=e.getBoundingClientRect();
  let ov=$('#ovl');
  if(!ov){ov=el('svg',{id:'ovl',width:pr.width,height:pr.height,viewBox:`0 0 ${pr.width} ${pr.height}`});pg.appendChild(ov);ov.appendChild(el('g',{filter:'url(#rough)',id:'ovg'}))}
  const g=$('#ovg'),x=r.left-pr.left,y=r.top-pr.top,w=r.width,h=r.height,col='var(--red)';
  if(kind==='circle')return ink(g,loop(x+w/2,y+h/2,w/2+12,h/2+6),{w:4.4,color:col,speed:.9,j:1.4,per:12});
  const yy=y+h-(o.up??6);
  let ms=ink(g,[[x-2,yy],[x+w*.5,yy+2],[x+w+2,yy-1]],{w:3.2,color:col,speed:.8,j:.7,per:8});
  if(kind==='double')ms=Math.max(ms,ink(g,[[x-2,yy+9],[x+w*.5,yy+11],[x+w+2,yy+8]],{w:3.2,color:col,speed:.8,j:.7,per:8}));
  return ms;
}
/* what the red mark goes under: the answer element, the last step, or the whole line */
function markTarget(i){
  const b=data.blocks[i],root=$('#'+parts[i].id);
  const a=root.querySelector('[id^=ans]');if(a)return a;
  if(b.k==='steps'){const li=[...root.querySelectorAll('li')].pop();return li?span([...li.querySelectorAll('.w')]):root}
  if(b.k==='text'||b.k==='note')return span([...root.querySelectorAll('.w')]);
  return root;
}
window.slateWobble=wobble;
window.slate={
  /* build the page from a slate; everything starts unwritten */
  show(s){
    gen++;data=s;
    const st=$('#stage');st.style.transition='none';st.classList.remove('fade');void st.offsetWidth;st.style.transition='';
    const n=(s.blocks||[]).length;
    const wide=(s.blocks||[]).some(b=>b.geo);
    st.innerHTML=`<div class="page${n===1?' big':''}${n>3?' dense':''}${wide?' wide':''}" id="pg"><div class="heard">${esc(s.title||'')}</div><div class="col">${build(s)}${s.ask?'<div class="rb" id="readback"></div>':''}</div></div>`;
  },
  /* S10: more blocks for the slate on screen (a lesson reveals step by step). They go under the ones already written. */
  append(blocks){
    if(!data)return;const from=data.blocks.length;data.blocks.push(...blocks);
    const col=document.querySelector('#pg .col');if(!col)return;
    const t=document.createElement('div');t.innerHTML=build(data,from);
    const rb=$('#readback');[...t.childNodes].forEach(n=>col.insertBefore(n,rb||null));
    const pg=$('#pg');if(pg&&data.blocks.length>3)pg.classList.add('dense');
    const ov=$('#ovl');if(ov){ov.setAttribute('height',pg.scrollHeight)}
    const last=col.lastElementChild;if(last&&pg.scrollHeight>innerHeight)last.scrollIntoView({block:'end',behavior:'smooth'});
  },
  /* ink block i in over ms (words by character share); then its red mark. Resolves when done. */
  async reveal(i,ms){
    if(!parts[i])return 0;const g=gen,ps=pieces(i);
    const tot=ps.reduce((a,p)=>a+p.w,0)||1;let total=ms||900;
    const c=cur={i,g,plan:0,rate:1,t:nowMs(),nom:0,stop:false,as:null,end:null,arm:null,geo:[],wipeEnd:0,target:0};
    let wipeTotal=total;
    if(data.blocks[i].geo){
      const d=drawGeo(i,total*.72);c.t=nowMs();         /* building the strokes takes a while: the clock starts when they can move */
      c.geo=[...$('#'+parts[i].id).querySelectorAll('svg')].flatMap(v=>v.getAnimations({subtree:true}));
      wipeTotal=total*.28;
      /* the true planned length: read off the drawing's own animations (stroke minimums and the dots shift it from .72 of ms),
         plus every label's wipe with its 40 ms floor, so a diagram with many labels is not under-planned */
      c.plan=Math.max(0,...c.geo.map(a=>a.effect.getComputedTiming().endTime))*RUNFAST()+ps.reduce((a,p)=>a+Math.max(40,wipeTotal*p.w/tot),0);
      const until=nowMs()+(d*6+300)/RUNFAST();
      while(g===gen&&!c.stop&&nowMs()<until&&c.geo.some(a=>a.playState!=='finished'&&a.playState!=='idle'))await new Promise(r=>setTimeout(r,12));
    }else c.plan=ps.reduce((a,p)=>a+Math.max(40,total*p.w/tot),0);
    if(g!==gen)return 0;
    if(c.stop)ps.forEach(p=>p.e.classList.add('on'));
    else await wipes(ps.map(p=>({e:p.e,ms:wipeTotal*p.w/tot})),c);
    c.wipeEnd=nowMs();
    if(g!==gen)return 0;
    const b=data.blocks[i];let m=0;
    if(b.mark&&b.k!=='diagram'&&!c.stop){m=markEl(markTarget(i),b.mark,{up:b.mark==='circle'?4:-2});await sleep(m)}
    return m;
  },
  /* S6: the audio for line i is fully generated and ends `ms` from now: speed the rest of the wipe up or down to land there.
     Planned progress is tracked as animation time, so a speed change never loses its place. */
  retime(i,ms){
    const c=cur;if(!c||c.i!==i||c.g!==gen||c.stop)return;
    const t=nowMs();c.nom+=(t-c.t)*c.rate*RUNFAST();c.t=t;
    const left=Math.max(1,c.plan-c.nom);
    c.rate=Math.min(20,Math.max(.2,left/(Math.max(30,ms)*RUNFAST())));
    c.target=t+ms;
    (c.as||[]).forEach(a=>a.updatePlaybackRate(c.rate));
    c.geo.forEach(a=>{try{a.updatePlaybackRate(c.rate)}catch(e){}});
    if(c.arm)c.arm();
  },
  /* S6: a barge-in. The line being inked is finished at once (no half-written word); the rest of the page stays unwritten. */
  finishLine(){
    const c=cur;if(!c||c.g!==gen)return;
    c.stop=true;
    if(c.end)c.end();
    c.geo.forEach(a=>{try{a.finish()}catch(e){}});
    const r=parts[c.i]&&$('#'+parts[c.i].id);
    if(r)r.querySelectorAll('.pc,.w,.space').forEach(e=>e.classList.add('on'));
    if(r&&r.classList.contains('pc')||r&&r.classList.contains('sent')||r&&r.classList.contains('note'))r.classList.add('on');
    const o=$('#ovg');if(o)o.getAnimations({subtree:true}).forEach(a=>{try{a.finish()}catch(e){}});
  },
  /* S8: the waiter read-back. The question inks in under the steps, then two quiet pills appear. A pill tells the app, which
     sends the same word he would have said (the engine answers ok / change / which step without a model). */
  async readback(ms){
    const rb=$('#readback');if(!rb||rb.dataset.on)return;rb.dataset.on='1';const g=gen;
    rb.innerHTML=`<div class="q">${wordSpans(data.ask||'Everything look right?')}</div>`;
    const ws=[...rb.querySelectorAll('.w')],tot=ws.reduce((a,e)=>a+(+e.dataset.w||3),0)||1;
    await wipes(ws.map(e=>({e,ms:(ms||1100)*(+e.dataset.w||3)/tot})),{rate:1});
    if(g!==gen)return;
    const pills=document.createElement('div');pills.className='pills';
    pills.innerHTML='<button class="pill soft" data-a="ok">Looks good</button><button class="pill" data-a="change">Change something</button>';
    rb.appendChild(pills);void pills.offsetWidth;pills.classList.add('on');
    pills.querySelectorAll('button').forEach(b=>b.onclick=()=>{
      const m=window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.slate;
      if(m)m.postMessage({pill:b.dataset.a});
      if(b.dataset.a==='ok')pills.classList.remove('on');
    });
  },
  /* S8: step j of block i now says `text`. Only that line is rewritten and re-inked; the rest of the page is untouched. */
  async updateItem(i,j,text){
    if(!data||!parts[i])return;const g=gen,root=$('#'+parts[i].id);if(!root)return;
    data.blocks[i].items[j]=text;
    const li=root.querySelectorAll('li')[j];if(!li)return;
    const p=li.querySelector('p');p.innerHTML=wordSpans(text);
    const ws=[...p.querySelectorAll('.w')],tot=ws.reduce((a,e)=>a+(+e.dataset.w||3),0)||1,ms=Math.max(700,text.length*45);
    await wipes(ws.map(e=>({e,ms:ms*(+e.dataset.w||3)/tot})),{rate:1});
    if(g!==gen)return;
  },
  /* S9: the drawer's list of past slates [{id,title,created_at}], grouped by day, newest first */
  setHistory(list){
    const box=$('#hlist');if(!box)return;box.innerHTML='';
    if(!list.length){box.innerHTML='<div class="none">Nothing yet</div>';return}
    let day='';
    for(const r of list){
      const d=new Date(r.created_at*1000),label=d.toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'});
      if(label!==day){day=label;const h=document.createElement('div');h.className='day';h.textContent=label;box.appendChild(h)}
      const it=document.createElement('div');it.className='it';it.dataset.id=r.id;
      it.innerHTML=`<span></span><button class="rp">replay</button>`;it.firstChild.textContent=r.title||'Untitled';
      it.onclick=e=>post({open:r.id,replay:e.target.classList.contains('rp')});
      box.appendChild(it);
    }
  },
  /* S9: a saved slate, shown finished at once, or inked again (replay, no voice) */
  async openSaved(page,replay){
    $('#hist').classList.remove('open');
    if(replay){await this.play({...page,kind:'stay'},{fade:false});return}
    this.show(page);const f=window.FAST;window.FAST=60;
    for(let i=0;i<page.blocks.length;i++)await this.reveal(i,1);
    window.FAST=f;this.finishNow();
  },
  /* for tests: how late the last wipe ended against the time the audio was due to end (ms, + is late) */
  lastSync(){return cur&&cur.wipeEnd&&cur.target?Math.round(cur.wipeEnd-cur.target):null},
  /* the whole slate at its own pace; the app drives show/reveal itself, this is for the dev page */
  async play(s,o={}){
    this.show(s);const g=gen,per=o.pace??55;
    for(let i=0;i<s.blocks.length;i++){
      if(g!==gen)return;
      await this.reveal(i,Math.max(700,String(s.blocks[i].say||'').length*per));await sleep(220);
    }
    if(g===gen&&s.kind==='quick'&&o.fade!==false){await sleep(4000);if(g===gen)this.fade()}
  },
  finishNow(){document.querySelectorAll('.geo svg mask path').forEach(p=>p.getAnimations().forEach(a=>a.finish()));document.querySelectorAll('.pc,.w,.space').forEach(e=>e.classList.add('on'))},
  fade(){$('#stage').classList.add('fade')},
  /* for tests: how many pieces are not fully written */
  unfinished(){return [...document.querySelectorAll('.pc,.w,.space')].filter(e=>!e.classList.contains('on')).length}
};

/* S8: tell the app when the pointer is on the page, so a quick slate does not fade under it. */
(function(){let on=false;const post=v=>{if(v===on)return;on=v;const m=window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.slate;if(m)m.postMessage({hover:v})};
  document.addEventListener('mouseover',()=>post(true));
  document.documentElement.addEventListener('mouseleave',()=>post(false));
  window.slateHover=post;})();

/* S9: the drawer */
function post(m){const h=window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.slate;if(h)h.postMessage(m)}
(function(){const b=document.getElementById('hbtn');if(!b)return;
  b.onclick=()=>{const h=document.getElementById('hist');const o=!h.classList.contains('open');h.classList.toggle('open',o);if(o)post({hist:'list'})}})();

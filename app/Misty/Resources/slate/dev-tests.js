/* S3 page tests. Open dev.html?manual=1&fast=50 and run `await runSlateTests()`; every value in the result should be true. */
window.runSlateTests=async()=>{
  const R={};
  /* bug 1: the mark sits under the answer's box, not the whole line */
  await slate.play(FIXTURES['working-with-note'],{fade:false});
  const ab=document.querySelector('#ans0').getBoundingClientRect(),lb=document.querySelector('#b0').getBoundingClientRect();
  const mb=[...document.querySelectorAll('#ovg path')].filter(p=>!p.closest('mask'))[0].getBoundingClientRect();
  R.bug1_mark_under_answer=Math.abs(mb.left-ab.left)<8&&Math.abs(mb.right-ab.right)<8&&ab.left>lb.left+20&&mb.top>=ab.top+ab.height*.5;
  /* bug 2: maths inside a note is KaTeX */
  slate.show({title:'t',kind:'quick',blocks:[{k:'math',tex:'a=1',say:'x',mark:'under'},{k:'note',text:'It becomes $-5\\sin(2x)$ here.',say:'x'}]});
  await slate.reveal(0,500);await slate.reveal(1,500);
  R.bug2_katex_in_note=document.querySelectorAll('.note .katex').length===1&&!document.querySelector('.note').textContent.includes('$');
  /* bug 3: 200 replays leave every word fully shown */
  const n=['five-times-five','protocol-readback','lesson-with-turn','inline-math-text','working-with-note'];
  for(let i=0;i<200;i++)await slate.play(FIXTURES[n[i%5]],{fade:false});
  R.bug3_200_replays_all_shown=slate.unfinished()===0&&[...document.querySelectorAll('.w,.pc')].every(e=>getComputedStyle(e).maskPosition.startsWith('0'));
  /* all fixtures play without an error */
  let ok=true;for(const k of Object.keys(FIXTURES)){try{await slate.play(FIXTURES[k],{fade:false});if(slate.unfinished())ok=false}catch(e){ok=false}}
  R.all_fixtures_render=ok;
  Object.assign(R,await runS4Tests());
  return R;
};

/* S4 geometry tests (fast): window.runS4Tests() */
window.runS4Tests=async()=>{
  const R={};
  /* S4: real measured label boxes against the real strokes, on every drawn diagram; wobble keeps its promises */
  let overlaps=0,diagrams=0;
  for(const k of Object.keys(FIXTURES)){
    const b=FIXTURES[k].blocks.findIndex(x=>x.geo);if(b<0)continue;diagrams++;
    slate.show(FIXTURES[k]);const root=document.querySelector('#b'+b),svgr=root.querySelector('svg').getBoundingClientRect(),g=FIXTURES[k].blocks[b].geo,sc=svgr.width/g.w;
    const pts=[];g.strokes.forEach(s=>{for(let i=1;i<s.pts.length;i++){const a=s.pts[i-1],c=s.pts[i],n=Math.max(1,Math.ceil(Math.hypot(c[0]-a[0],c[1]-a[1])/3));for(let j=0;j<=n;j++)pts.push([a[0]+(c[0]-a[0])*j/n,a[1]+(c[1]-a[1])*j/n])}});
    g.dots.forEach(d=>pts.push([d.x,d.y]));
    const boxes=[...root.querySelectorAll('.gl')].map(e=>{const r=e.getBoundingClientRect();return [(r.left-svgr.left)/sc,(r.top-svgr.top)/sc,(r.right-svgr.left)/sc,(r.bottom-svgr.top)/sc]});
    boxes.forEach((r,a)=>{
      if(pts.some(q=>q[0]>=r[0]-1&&q[0]<=r[2]+1&&q[1]>=r[1]-1&&q[1]<=r[3]+1))overlaps++;
      boxes.forEach((o,c)=>{if(c>a&&!(r[2]<=o[0]||o[2]<=r[0]||r[3]<=o[1]||o[3]<=r[1]))overlaps++});
    });
  }
  R.s4_diagrams_checked=diagrams;R.s4_label_overlaps_real_boxes=overlaps;
  const amp=1.2/0.64;let closedGap=0,endMoved=0,maxd=0;
  for(const k of Object.keys(FIXTURES))for(const b of FIXTURES[k].blocks)if(b.geo)b.geo.strokes.forEach((s,n)=>{
    const w=slateWobble(s.pts,s.closed,amp,n+1);
    if(s.closed)closedGap=Math.max(closedGap,Math.hypot(w[0][0]-w[w.length-1][0],w[0][1]-w[w.length-1][1])*0.64);
    else endMoved=Math.max(endMoved,Math.hypot(w[0][0]-s.pts[0][0],w[0][1]-s.pts[0][1]),Math.hypot(w[w.length-1][0]-s.pts[s.pts.length-1][0],w[w.length-1][1]-s.pts[s.pts.length-1][1]));
    const d0=[];for(let i=1;i<s.pts.length;i++){const a=s.pts[i-1],c=s.pts[i],m=Math.max(1,Math.ceil(Math.hypot(c[0]-a[0],c[1]-a[1])/40));for(let j=(i===1?0:1);j<=m;j++)d0.push([a[0]+(c[0]-a[0])*j/m,a[1]+(c[1]-a[1])*j/m])}
    w.forEach((q,i)=>{if(d0[i])maxd=Math.max(maxd,Math.hypot(q[0]-d0[i][0],q[1]-d0[i][1])*0.64)});
  });
  R.s4_closed_gap_px=+closedGap.toFixed(4);R.s4_closed_gap_under_1px=closedGap<1;R.s4_open_ends_fixed=endMoved<1e-9;R.s4_max_wobble_px=+maxd.toFixed(3);R.s4_wobble_at_most_1_5px=maxd<=1.5;
  return R;
};

/* S6 voice sync (real time, FAST must be 1): for each line, start the wipe on an estimate, then hand it the exact audio end
   a little later (as the speaker does), and measure how far the wipe's end is from that audio end. Pass n slates (default 10). */
window.runS6Tests=async(n=10)=>{
  const all=Object.keys(FIXTURES).filter(k=>FIXTURES[k].blocks.length>=2),stride=Math.max(1,Math.floor(all.length/n));
  const keys=Array.from({length:n},(_,j)=>all[(j*stride)%all.length]);   /* spread across maths, steps, chemistry, diagrams */
  const rows=[];let barge=0,bargeOk=0;
  for(const k of keys){
    const s=FIXTURES[k];slate.show(s);
    for(let i=0;i<Math.min(3,s.blocks.length);i++){
      const chars=String(s.blocks[i].say||s.blocks[i].tex||'').length||20;
      const est=Math.max(500,Math.round(chars*62));
      const real=Math.max(500,Math.round(est*(0.6+Math.random()*0.9)));   /* the true audio is 0.6x to 1.5x the estimate */
      const p=slate.reveal(i,est);
      const gap=40+Math.round(Math.random()*160);                         /* the exact end arrives this long after the start */
      await new Promise(r=>setTimeout(r,gap));
      slate.retime(i,real-gap);
      await p;
      rows.push({slate:k,line:i,est,audio:real,deltaMs:slate.lastSync()});
      await new Promise(r=>setTimeout(r,60));
    }
  }
  /* barge-in: cut a line in the middle; every piece of that line must be fully shown, none half-written */
  for(let j=0;j<5;j++){
    const s=FIXTURES[keys[j%keys.length]];slate.show(s);
    const p=slate.reveal(0,1500);await new Promise(r=>setTimeout(r,300+j*120));
    slate.finishLine();await p;barge++;
    const root=document.querySelector('#b0');
    const pieces=[...root.querySelectorAll('.pc,.w,.space')];if(root.classList.contains('pc'))pieces.push(root);
    const ok=pieces.every(e=>e.classList.contains('on'))&&root.getAnimations({subtree:true}).every(a=>a.playState!=='running'||!a.effect);
    if(ok)bargeOk++;
  }
  const bad=rows.filter(r=>r.deltaMs===null||Math.abs(r.deltaMs)>150);
  return {lines:rows.length,within150:rows.length-bad.length,worst:Math.max(...rows.map(r=>Math.abs(r.deltaMs??9999))),bad,barge,bargeOk,rows};
};

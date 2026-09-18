async(page)=>{
  await page.goto('http://127.0.0.1:5173');
  await page.getByRole('button',{name:'KIS Find exact scene'}).click();
  await page.getByRole('button',{name:'New search',exact:true}).click();
  const payload=await (await page.request.get('http://127.0.0.1:8010/api/media/frames?limit=100')).json();
  const results=payload.frames.map((f,i)=>({id:`profile-${i}`,rank:i+1,video_id:f.video_id,video_code:f.video_code,
    frame_id:f.id,frame_idx:f.frame_idx,timestamp_ms:f.timestamp_ms,score:1,score_breakdown:{},sequence_frames:[],thumbnail_url:f.thumbnail_url}));
  await page.route('**/api/retrieval/search',r=>r.fulfill({json:{query_run_id:'profile',query_type:'KIS',normalized_query:{},results}}));
  try{
    await page.getByLabel('Search query',{exact:true}).fill('profile grid');
    await page.getByRole('button',{name:'Run search',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.frame-card').length===100);
    const mount=await page.evaluate(()=>performance.getEntriesByName('result-grid-commit').map(e=>e.duration));
    await page.evaluate(()=>{
      performance.clearMeasures('result-grid-commit');performance.clearMarks('result-grid-render');
      window.__interactionDurations=[];
      window.__interactionObserver=new PerformanceObserver(list=>window.__interactionDurations.push(...list.getEntries().filter(e=>e.interactionId).map(e=>e.duration)));
      window.__interactionObserver.observe({type:'event',durationThreshold:16});
    });
    await page.getByLabel('Search query',{exact:true}).pressSequentially(' measure typing on one hundred frames',{delay:25});
    await page.waitForTimeout(300);
    const measured=await page.evaluate(()=>{
      window.__interactionObserver.disconnect();
      return {renders:performance.getEntriesByName('result-grid-render').length,commits:performance.getEntriesByName('result-grid-commit').map(e=>e.duration),maxEventDurationMs:Math.max(0,...window.__interactionDurations)};
    });
    if(measured.renders!==0 || measured.commits.some(ms=>ms>0))throw new Error(JSON.stringify(measured));
    return {frames:100,initialGridCommitDurationsMs:mount,...measured,scope:'Lab Event Timing proxy; zero means below 16ms threshold, not field INP'};
  } finally{await page.unrouteAll({behavior:'ignoreErrors'});}
}

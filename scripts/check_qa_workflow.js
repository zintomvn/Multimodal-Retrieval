async(page)=>{
  const assert=(ok,message)=>{if(!ok)throw new Error(message);};
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.goto('http://127.0.0.1:5173');
  await page.getByRole('button',{name:'QA Answer from video'}).click();
  await page.getByRole('button',{name:'New search',exact:true}).click();
  const frames=await (await page.request.get('http://127.0.0.1:8010/api/media/frames?limit=1')).json();
  const frame=frames.frames[0];
  const result={id:'qa-fixture',rank:1,video_id:frame.video_id,video_code:frame.video_code,frame_id:frame.id,
    frame_idx:frame.frame_idx,timestamp_ms:frame.timestamp_ms,answer:null,score:1,score_breakdown:{},sequence_frames:[],thumbnail_url:frame.thumbnail_url};
  let plans=0,answers=0;
  await page.route('**/api/retrieval/plan',r=>{plans++;return r.fulfill({json:{normalized_query:{}}});});
  await page.route('**/api/retrieval/qa',r=>{
    assert(r.request().postDataJSON().options.defer_qa===true,'QA generation not deferred');
    return r.fulfill({json:{query_run_id:'qa-fixture',query_type:'QA',normalized_query:{},results:[result]}});
  });
  await page.route('**/api/retrieval/results/qa-fixture/answer',r=>{answers++;return r.fulfill({json:{answer:'red',evidence:[{text:'red'}],mode:'text_evidence'}});});
  try {
    await page.getByLabel('Search query',{exact:true}).fill('what color?');
    await page.getByRole('button',{name:'Run search',exact:true}).click();
    await page.getByLabel('QA answer',{exact:true}).waitFor();
    assert(answers===0 && plans===0,'Unexpected duplicate planning or eager answer');
    await page.getByRole('button',{name:'Generate from evidence',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[aria-label="QA answer"]')?.value==='red');
    assert(answers===1,'Answer not generated exactly once');
    await page.getByLabel('QA answer',{exact:true}).fill('blue');
    await page.locator('.frame-card').getByRole('button',{name:'Pick',exact:true}).click();
    assert((await page.locator('.selected-row').allTextContents()).some(t=>t.includes('blue')),'Manual answer not selected');
    await page.evaluate(()=>performance.clearMarks('result-grid-render'));
    await page.getByLabel('Search query',{exact:true}).pressSequentially(' revised');
    const renders=await page.evaluate(()=>performance.getEntriesByName('result-grid-render').length);
    assert(renders===0,`Typing rerendered grid ${renders} times`);
    await page.screenshot({path:'output/playwright/optimization-qa-workflow.png'});
    return {deferred:true,singlePlanning:true,manualAnswer:true,gridRendersWhileTyping:renders};
  } finally {await page.unrouteAll({behavior:'ignoreErrors'});}
}

async(page)=>{
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.goto('http://127.0.0.1:5173');
  await page.getByRole('button',{name:'TRAKE Order key events'}).click();
  await page.getByRole('button',{name:'New search',exact:true}).click();
  const events=page.getByRole('textbox',{name:'Ordered events',exact:true});
  await events.fill('event one\nevent two\nevent three');
  let payload;
  await page.route('**/api/retrieval/trake',r=>{
    payload=r.request().postDataJSON();
    const frames=[100,200,300].map((idx,i)=>({frame_id:`frame-${idx}`,frame_idx:idx,video_code:'V1',timestamp_ms:idx*40,score:1,event_index:i+1,order_index:i+1}));
    return r.fulfill({json:{query_run_id:'trake-test',query_type:'TRAKE',normalized_query:{temporal_event_count:3,temporal_events:payload.options.temporal_events},results:[{id:'sequence',rank:1,video_id:'V1',video_code:'V1',frame_id:null,frame_idx:100,timestamp_ms:4000,answer:null,score:1,score_breakdown:{},sequence_frames:frames}]}});
  });
  try {
    await page.getByRole('button',{name:'Run search',exact:true}).click();
    await page.locator('.trake-cell').first().waitFor();
    const cells=page.locator('.trake-cell');
    await cells.nth(0).getByRole('button',{name:'Pick',exact:true}).click();
    await cells.nth(2).getByRole('button',{name:'Pick',exact:true}).click();
    await events.fill('event one\nchanged second event\nevent three');
    if(await page.locator('.trake-cell.selected').count()!==2)throw new Error('Editing E2 lost E1/E3');
    await page.getByRole('button',{name:'Run search',exact:true}).click();
    await page.waitForTimeout(300);
    if(await page.locator('.trake-cell.selected').count()!==2)throw new Error('Rerun lost locked picks');
    if(payload.options.temporal_events[1]!=='changed second event')throw new Error('Edited event not sent');
    await cells.nth(1).getByRole('button',{name:'Pick',exact:true}).click();
    await page.screenshot({path:'output/playwright/optimization-trake-editor.png'});
    return {editedEventPayload:true,preservedOtherEvents:true,preservedAcrossRerun:true,eventCount:3};
  } finally {await page.unrouteAll({behavior:'ignoreErrors'});}
}

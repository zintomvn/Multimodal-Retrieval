// Playwright CLI: compare against the original UI served at :5174, then test :5173.
async(page)=>{
  const testContext=await page.context().browser().newContext();
  page=await testContext.newPage();
  try {
  const assert=(ok,message)=>{if(!ok)throw new Error(message);};
  const results={};
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.setViewportSize({width:1440,height:900});
  await page.addInitScript(token=>{
    if(sessionStorage.getItem('logic-reset')!==token){localStorage.removeItem('multimodal-workspace-v1');sessionStorage.setItem('logic-reset',token);}
  },String(Date.now()));
  const snapshots=[];
  for(const port of [5174,5173]){
    await page.goto(`http://127.0.0.1:${port}`);
    await page.locator('article').nth(47).waitFor();
    await page.evaluate(()=>document.fonts.ready);
    snapshots.push(await page.evaluate(()=>({
      tabs:[...document.querySelectorAll('[role="tab"]')].map(e=>e.textContent),
      buttons:[...document.querySelectorAll('button')].map(e=>[e.textContent?.trim(),e.getAttribute('aria-label')]),
      cards:[...document.querySelectorAll('article')].map(e=>({text:e.textContent,classes:e.className})),
      layout:['.left-sidebar','.right-sidebar','.composer-wrap','.frame-grid'].map(selector=>{const e=document.querySelector(selector);if(!e)return null;const r=e.getBoundingClientRect();return [selector,r.x,r.y,r.width,r.height];})
    })));
    await page.screenshot({path:`output/playwright/logic-ui-${port}.png`});
  }
  assert(JSON.stringify(snapshots[0])===JSON.stringify(snapshots[1]),'Original UI controls, cards or layout changed');
  results.originalUiParity=true;
  // Install all subsequent checks on the optimized UI only.
  const input=page.getByRole('textbox',{name:'Search query'});
  await input.fill('retained KIS draft');
  await page.locator('article').first().getByRole('button',{name:'Pick',exact:true}).click();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  const expansion=page.getByRole('button',{name:'Expansion',exact:true});
  if((await expansion.getAttribute('class')??'').includes('active'))await expansion.click();
  await page.keyboard.press('Escape');
  assert(await page.getByRole('dialog',{name:'Settings',exact:true}).count()===0,'Settings Escape failed');
  await page.getByRole('button',{name:'QA Answer from video'}).click();await input.fill('retained QA draft');
  await page.getByRole('button',{name:'KIS Find exact scene'}).click();
  assert(await input.inputValue()==='retained KIS draft','KIS draft lost');
  await page.waitForTimeout(400);await page.reload();await page.locator('article').first().waitFor();
  assert(await input.inputValue()==='retained KIS draft','Reload draft lost');
  assert(await page.locator('.selected-row').count()===1,'Reload selection lost');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  assert(!(await expansion.getAttribute('class')??'').includes('active'),'Reload options lost');
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'QA Answer from video'}).click();
  assert(await input.inputValue()==='retained QA draft','QA draft lost');
  await page.getByRole('button',{name:'KIS Find exact scene'}).click();
  results.workspace=true;
  await page.route('**/api/retrieval/plan',r=>r.fulfill({json:{normalized_query:{}}}));
  let calls=0;
  await page.route('**/api/retrieval/search',async r=>{calls++;await page.waitForTimeout(900);await r.fulfill({json:{query_run_id:'late',query_type:'KIS',normalized_query:{},results:[]}}).catch(()=>{});});
  await input.press('Enter');await page.waitForTimeout(150);await input.press('Enter');
  assert(calls===1,'Duplicate Enter sent multiple requests');
  await page.getByRole('button',{name:'QA Answer from video'}).click();
  await page.route('**/api/retrieval/qa',r=>r.fulfill({json:{query_run_id:'current',query_type:'QA',normalized_query:{},results:[]}}));
  await input.press('Enter');await page.waitForTimeout(1200);
  assert(!await page.getByRole('button',{name:'Run search'}).isDisabled(),'Stale request left loading locked');
  assert((await page.locator('.history-item').allTextContents()).length===1,'Cancelled search entered history');
  results.duplicateAndStaleGuard=true;
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.getByRole('button',{name:'KIS Find exact scene'}).click();
  await page.getByRole('button',{name:'New search',exact:true}).click();
  await page.locator('article').nth(47).waitFor();
  await page.evaluate(()=>performance.clearMarks('result-grid-render'));
  await input.pressSequentially(' typing');await page.waitForTimeout(300);
  const renders=await page.evaluate(()=>performance.getEntriesByName('result-grid-render').length);
  assert(renders===0,'Typing rerendered the grid');results.gridRendersWhileTyping=renders;
  const preview=page.locator('article').first().getByRole('button',{name:'Video',exact:true});
  await preview.click();const dialog=page.getByRole('dialog',{name:'Video preview'});await dialog.waitFor();
  assert(await dialog.evaluate(e=>e.contains(document.activeElement)),'Preview did not acquire focus');
  await page.keyboard.press('Shift+Tab');assert(await dialog.evaluate(e=>e.contains(document.activeElement)),'Focus escaped modal');
  await page.keyboard.press('Escape');assert(await dialog.count()===0,'Preview did not close');
  assert(await preview.evaluate(e=>e===document.activeElement),'Opener focus not restored');results.focus=true;
  const downloads=[];const onDownload=d=>downloads.push(d);page.on('download',onDownload);
  let creates=0;
  await page.route('**/api/submissions',async r=>{creates++;await page.waitForTimeout(500);return r.fulfill({json:{id:'invalid',status:'DRAFT'}});});
  await page.route('**/api/submissions/invalid/items',r=>r.fulfill({json:{status:'ok'}}));
  await page.route('**/api/submissions/invalid/export',r=>r.fulfill({json:{validation_report:{valid:false,errors:['invalid'],warnings:[]},csv_uri:null,zip_uri:null}}));
  const exportButton=page.getByRole('button',{name:'Export CSV',exact:true});
  await exportButton.click();await exportButton.click();await page.waitForTimeout(800);
  assert(creates===1,'Duplicate export request');assert(downloads.length===0,'Invalid export downloaded local fallback');
  await page.unroute('**/api/submissions/invalid/export');
  await page.route('**/api/submissions/invalid/export',r=>r.fulfill({status:503,json:{detail:'unavailable'}}));
  await exportButton.click();await page.waitForTimeout(800);assert(downloads.length===0,'Failed export downloaded local fallback');
  page.off('download',onDownload);await page.unrouteAll({behavior:'ignoreErrors'});
  results.exportGuard=true;
  const response=await page.request.get('http://127.0.0.1:5173/api/media/frames?limit=1');
  const frame=(await response.json()).frames[0];
  const fixture={id:'logic-fixture',rank:1,video_id:frame.video_id,video_code:frame.video_code,frame_id:frame.id,
    frame_idx:frame.frame_idx,timestamp_ms:frame.timestamp_ms,answer:'fixture answer',score:1,score_breakdown:{},sequence_frames:[],thumbnail_url:frame.thumbnail_url};
  const payload=type=>({query_run_id:'logic-'+type,query_type:type,normalized_query:{},results:[fixture]});
  await page.route('**/api/retrieval/plan',r=>r.fulfill({json:{normalized_query:{}}}));
  await page.route('**/api/retrieval/search',r=>r.fulfill({json:payload('KIS')}));
  await page.route('**/api/retrieval/qa',r=>r.fulfill({json:payload('QA')}));
  try{
    results.modeGridRenders={};
    for(const mode of ['Auto','Chat']){
      await page.getByRole('tab',{name:mode,exact:true}).click();
      const modeInput=page.getByRole('textbox',{name:mode==='Chat'?'Chat message':'Search query'});
      await modeInput.fill('mode fixture');
      const done=page.waitForResponse(r=>r.url().includes('/api/retrieval/'+(mode==='Chat'?'qa':'search')));
      await modeInput.press('Enter');await done;await page.locator('article').first().waitFor();
      if(mode==='Chat'){
        await page.getByText('fixture answer',{exact:true}).waitFor();results.chatInlineAnswer=true;
        continue;
      }
      await page.waitForTimeout(100);await page.evaluate(()=>performance.clearMarks('result-grid-render'));
      await modeInput.pressSequentially(' edit');await page.waitForTimeout(300);
      const renders=await page.evaluate(()=>performance.getEntriesByName('result-grid-render').length);
      assert(renders===0,mode+' typing rerendered grid');results.modeGridRenders[mode]=renders;
    }
    const historyCount=await page.locator('.history-item').count();
    assert(historyCount===2,'Expected successful QA and Auto searches in existing history');
    await page.waitForTimeout(300);await page.reload();
    assert(await page.getByRole('tab',{name:'Chat',exact:true}).getAttribute('aria-selected')==='true','Mode lost after reload');
    assert(await page.locator('.history-item').count()===historyCount,'Search history lost after reload');
    results.modeAndHistory=true;
  }finally{await page.unrouteAll({behavior:'ignoreErrors'});}
  return results;
  } finally { await testContext.close(); }
}

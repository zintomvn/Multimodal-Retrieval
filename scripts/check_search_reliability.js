// Run with playwright-cli run-code --filename=scripts/check_search_reliability.js
async (page) => {
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.setViewportSize({width:1440,height:900});
  try {
    await page.route('**/api/datasets', route=>route.fulfill({status:503,json:{detail:'test outage'}}));
    await page.goto('http://127.0.0.1:5173');
    await page.getByRole('button',{name:'Retry data'}).waitFor();
    assert(await page.locator('article').count()===0,'Outage displayed sample frames');
    assert(await page.getByRole('button',{name:'Run search'}).isDisabled(),'Search enabled with no dataset');
    await page.unroute('**/api/datasets');
    await page.getByRole('button',{name:'Retry data'}).click();
    await page.locator('article').first().waitFor();
    await page.route('**/api/retrieval/plan',route=>route.fulfill({json:{normalized_query:{}}}));
    let kisCalls=0;
    await page.route('**/api/retrieval/search',async route=>{
      kisCalls++;
      await page.waitForTimeout(1500);
      await route.fulfill({json:{query_run_id:'late-kis',query_type:'KIS',normalized_query:{},results:[{id:'late',rank:1,video_id:'L28_V002',video_code:'LATE_KIS',frame_id:null,frame_idx:100,timestamp_ms:4000,answer:null,score:0.5,score_breakdown:{},sequence_frames:[]}]}}).catch(()=>{});
    });
    await page.route('**/api/retrieval/qa',route=>route.fulfill({json:{query_run_id:'current-qa',query_type:'QA',normalized_query:{},results:[]}}));
    const input=page.getByRole('textbox',{name:'Search query'});
    await input.fill('delayed request');
    await input.press('Enter');
    await page.waitForTimeout(200);
    await input.press('Enter');
    assert(kisCalls===1,'Duplicate Enter started another request');
    await page.getByRole('button',{name:'QA Answer from video'}).click();
    await input.press('Enter');
    await page.waitForTimeout(1800);
    assert(await page.getByText('LATE_KIS',{exact:true}).count()===0,'Stale KIS overwrote QA');
    assert(!(await page.getByRole('button',{name:'Run search'}).isDisabled()),'Old finally left UI locked');
    await page.unroute('**/api/retrieval/qa');
    await page.route('**/api/retrieval/qa',route=>route.fulfill({status:503,json:{detail:'source unavailable'}}));
    await input.press('Enter');
    await page.getByRole('button',{name:'Retry search'}).waitFor();
    assert(await page.getByText('No frames match this query.',{exact:true}).count()===0,'Error presented as no-match');
    await page.screenshot({path:'output/playwright/optimization-search-error.png'});
    return {outageNoMock:true,retryRecovered:true,duplicateBlocked:true,staleResponseBlocked:true,errorDistinctFromEmpty:true};
  } finally {
    await page.unrouteAll({behavior:'ignoreErrors'});
  }
}

async (page) => {
  const assert=(ok,msg)=>{if(!ok) throw new Error(msg);};
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.setViewportSize({width:1440,height:900});
  await page.goto('http://127.0.0.1:5173');
  await page.locator('article').first().waitFor();
  const downloads=[];
  const listener=d=>downloads.push(d);
  page.on('download',listener);
  try {
    await page.locator('article').first().getByRole('button',{name:/select|pick/i}).click();
    let creates=0,downloadCalls=0;
    await page.route('**/api/submissions',route=>{creates++;return route.fulfill({json:{id:'test-invalid',status:'DRAFT'}});});
    await page.route('**/api/submissions/test-invalid/items',route=>route.fulfill({json:{status:'ok'}}));
    await page.route('**/api/submissions/test-invalid/export',route=>route.fulfill({json:{submission_id:'test-invalid',status:'FAILED',csv_uri:null,zip_uri:null,validation_report:{valid:false,errors:['QA answer required'],warnings:[]}}}));
    await page.route('**/api/submissions/test-invalid/download',route=>{downloadCalls++;return route.abort();});
    await page.getByRole('button',{name:'Export current query CSV'}).click();
    await page.getByRole('status').filter({hasText:'QA answer required'}).waitFor();
    assert(creates===1,'Submission was duplicated');
    assert(downloadCalls===0 && downloads.length===0,'Invalid export triggered download or local fallback');
    await page.unroute('**/api/submissions/test-invalid/export');
    await page.route('**/api/submissions/test-invalid/export',route=>route.fulfill({status:503,json:{detail:'unavailable'}}));
    await page.getByRole('button',{name:'Export current query CSV'}).click();
    await page.getByRole('status').filter({hasText:'Export failed'}).waitFor();
    assert(downloadCalls===0 && downloads.length===0,'Outage triggered download or local fallback');
    return {invalidBlocked:true,outageBlocked:true,noLocalFallback:true};
  } finally {
    page.off('download',listener);
    await page.unrouteAll({behavior:'ignoreErrors'});
  }
}

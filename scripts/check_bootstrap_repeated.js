async(page)=>{
  const cases=[];
  for(let i=0;i<5;i++){
    await page.unrouteAll({behavior:'ignoreErrors'});
    await page.goto('http://127.0.0.1:5175');
    await page.locator('article').first().waitFor();
    await page.waitForTimeout(500);
    let calls=0;
    await page.route('**/api/datasets',r=>{calls++;return r.fulfill({status:503,json:{detail:'outage'}});});
    await page.goto('http://127.0.0.1:5175');
    await page.waitForTimeout(1000);
    cases.push({iteration:i,calls,retry:await page.getByRole('button',{name:'Retry data'}).count(),frames:await page.locator('article').count(),disabled:await page.getByRole('button',{name:'Run search'}).isDisabled()});
  }
  await page.unrouteAll({behavior:'ignoreErrors'});
  return cases;
}

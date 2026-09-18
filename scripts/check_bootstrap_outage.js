async(page)=>{
  await page.unrouteAll({behavior:'ignoreErrors'});
  let intercepted=0;
  const responses=[];
  const listener=r=>{if(r.url().includes('/api/datasets'))responses.push({status:r.status(),url:r.url()});};
  page.on('response',listener);
  await page.route('**/api/datasets',route=>{intercepted++;return route.fulfill({status:503,json:{detail:'test outage'}});});
  try{
    await page.goto('http://127.0.0.1:5175');
    await page.waitForTimeout(2500);
    return {intercepted,responses,alerts:await page.getByRole('alert').allTextContents(),frames:await page.locator('article').count(),retry:await page.getByRole('button',{name:'Retry data'}).count(),searchDisabled:await page.getByRole('button',{name:'Run search'}).isDisabled()};
  }finally{page.off('response',listener);await page.unrouteAll({behavior:'ignoreErrors'});}
}

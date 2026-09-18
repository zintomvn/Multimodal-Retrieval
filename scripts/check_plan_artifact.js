async(page)=>{
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('file:///D:/Jobs/AIC/Multimodal-Retrieval/output/ui-search-optimization-plan.html');
  const backlog=page.locator('#current-backlog');
  if(!await backlog.isVisible())throw new Error('Current status missing');
  if(!await backlog.getByText('18 mục đủ bằng chứng',{exact:false}).count())throw new Error('Completion count missing');
  const rows=await backlog.locator('tr').count();
  if(rows!==23)throw new Error(`Expected 22 items plus header, got ${rows}`);
  await page.screenshot({path:'output/playwright/optimization-plan-updated.png'});
  return {rows,visible:true};
}

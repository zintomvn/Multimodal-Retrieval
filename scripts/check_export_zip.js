async(page)=>{
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.goto('http://127.0.0.1:5173');
  await page.getByRole('button',{name:'KIS Find exact scene'}).click();
  for(let i=0;i<2;i++){
    await page.getByRole('button',{name:'New search',exact:true}).click();
    await page.locator('.frame-card').first().getByRole('button',{name:'Pick',exact:true}).click();
  }
  const downloaded=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export all queries ZIP',exact:true}).click();
  const file=await downloaded;
  await file.saveAs('output/playwright/optimization-live-export.zip');
  return {filename:file.suggestedFilename(),rows:await page.locator('.selected-row').count()};
}

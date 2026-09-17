async(page)=>{
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.setViewportSize({width:1440,height:900});
  const rows=[];
  for(const [variant,port] of [['before',5174],['after',5175]]){
    await page.goto(`http://127.0.0.1:${port}`);
    await page.getByRole('button',{name:'KIS Find exact scene'}).click();
    await page.getByRole('button',{name:'New search',exact:true}).click();
    await page.locator('article').first().waitFor();
    await page.getByRole('button',{name:'Settings',exact:true}).click();
    for(const name of ['Expansion','Agent plan']){
      const button=page.getByRole('button',{name,exact:true});
      if((await button.getAttribute('class')??'').includes('active'))await button.click();
    }
    await page.getByRole('button',{name:'Close settings',exact:true}).click();
    const source=page.getByLabel('Search source',{exact:true});
    if(await source.count())await source.selectOption('auto');
    for(let iteration=0;iteration<3;iteration++){
      await page.getByRole('textbox',{name:'Search query'}).fill('PHU XUAN GIA DINH');
      const responsePromise=page.waitForResponse(r=>r.url().includes('/api/retrieval/search')&&r.request().method()==='POST',{timeout:60000});
      const start=Date.now();
      await page.getByRole('button',{name:'Run search',exact:true}).click();
      const response=await responsePromise;
      const payload=await response.json();
      await page.getByRole('button',{name:'Run search',exact:true}).waitFor({state:'visible'});
      await page.waitForFunction(()=>!document.querySelector('[aria-label="Run search"]')?.disabled);
      await page.locator('article').first().waitFor();
      rows.push({variant,flow:'KIS live search',iteration,ms:Date.now()-start,status:response.status(),results:payload.results?.length,frames:payload.results?.map(r=>r.frame_id),options:response.request().postDataJSON().options});
    }
    await page.getByRole('button',{name:'Video Find video and frame'}).click();
    await page.getByLabel('Video name / code',{exact:true}).fill('L26_V191');
    await page.getByLabel('Frame index',{exact:true}).fill('596');
    const responsePromise=page.waitForResponse(r=>r.url().includes('/api/media/frames?')&&r.url().includes('L26_V191'));
    const start=Date.now();
    await page.getByLabel('Frame index',{exact:true}).press('Enter');
    const response=await responsePromise;
    const payload=await response.json();
    await page.locator('article').first().waitFor();
    rows.push({variant,flow:'Video lookup',ms:Date.now()-start,status:response.status(),results:payload.frames?.length,correctVideo:payload.frames?.every(f=>f.video_code==='L26_V191')});
    await page.screenshot({path:`output/playwright/e2e-${variant}-video-lookup.png`});
  }
  return rows;
}

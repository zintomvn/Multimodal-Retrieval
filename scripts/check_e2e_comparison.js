// Run through playwright-cli run-code --filename=...
async (page) => {
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.setViewportSize({width:1440,height:900});
  const report={variants:{},scope:'Production browser, live API/database/media, no response mocks'};
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  for(const [label,port] of [['before',5174],['after',5175]]){
    const base=`http://127.0.0.1:${port}`;
    const data={gallery_ms:[],preview_ms:[],checks:{}};
    report.variants[label]=data;
    await page.goto(base);
    await page.evaluate(()=>localStorage.clear());
    for(let i=0;i<10;i++){
      const start=Date.now();
      await page.goto(base);
      await page.locator('article').nth(47).waitFor({timeout:30000});
      data.gallery_ms.push(Date.now()-start);
    }
    data.checks.gallery48=await page.locator('article').count()===48;
    for(let i=0;i<5;i++){
      const start=Date.now();
      await page.locator('article').first().getByRole('button',{name:'Video',exact:true}).click();
      const dialog=page.getByRole('dialog',{name:'Video preview'});
      await dialog.waitFor({timeout:30000});
      data.preview_ms.push(Date.now()-start);
      data.checks.previewFocus=await dialog.evaluate(el=>el.contains(document.activeElement));
      if(i===0){
        await page.waitForTimeout(2000);
        data.video=await page.locator('video').evaluate(el=>({readyState:el.readyState,error:el.error?.code??null,duration:Number.isFinite(el.duration)?el.duration:null}));
      }
      await page.keyboard.press('Escape');
      if(await dialog.isVisible()){
        data.checks.escapeClosesPreview=false;
        await dialog.getByRole('button',{name:/close/i}).first().click();
      }else data.checks.escapeClosesPreview=true;
    }
    await page.locator('article').first().getByRole('button',{name:'Pick',exact:true}).click();
    data.checks.selection=await page.locator('.selected-row').count()===1;
    const selected=await page.locator('.selected-row').first().innerText();
    const started=Date.now();
    const downloaded=page.waitForEvent('download',{timeout:30000});
    await page.getByRole('button',{name:label==='before'?'Export CSV':'Export current query CSV',exact:true}).click();
    const file=await downloaded;
    await file.saveAs(`output/playwright/e2e-${label}-export.download`);
    data.export={ms:Date.now()-started,filename:file.suggestedFilename(),selected};
    data.checks.exportDownloaded=!(await file.failure());
    await page.getByRole('textbox',{name:'Search query'}).fill('e2e retained draft');
    await page.waitForTimeout(400);
    await page.reload();
    await page.locator('article').first().waitFor();
    data.checks.draftReload=await page.getByRole('textbox',{name:'Search query'}).inputValue()==='e2e retained draft';
    data.checks.selectionReload=await page.locator('.selected-row').count()===1;
    await page.screenshot({path:`output/playwright/e2e-${label}-desktop.png`});
    data.responsive=[];
    for(const width of [390,900,1024,1152,1180,1440]){
      await page.setViewportSize({width,height:900});
      await page.waitForTimeout(200);
      const open=page.getByRole('button',{name:'Open selected frames sidebar',exact:true});
      if(await open.isVisible())await open.click();
      await page.waitForTimeout(200);
      data.responsive.push({width,searchUncovered:await page.getByRole('button',{name:'Run search'}).evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
      await page.keyboard.press('Escape');
    }
    await page.setViewportSize({width:1440,height:900});
    data.pageErrors=errors.splice(0);
  }
  return report;
}

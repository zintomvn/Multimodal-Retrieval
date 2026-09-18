async (page) => {
  const assert=(ok,msg)=>{if(!ok)throw new Error(msg);};
  await page.unrouteAll({behavior:'ignoreErrors'});
  const sizes=[];
  for (const width of [390,1024,1180,1440]) {
    await page.setViewportSize({width,height:800});
    await page.goto('http://127.0.0.1:5173');
    await page.locator('article').first().waitFor();
    const open=page.getByRole('button',{name:'Open selected frames sidebar',exact:true});
    if(await open.count()) await open.click();
    await page.waitForTimeout(250);
    const clear=await page.getByRole('button',{name:'Run search'}).evaluate(b=>{
      const r=b.getBoundingClientRect();return b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
    });
    assert(clear,`Search covered at ${width}`);
    await page.keyboard.press('Escape');
    if(width<=1180) assert(await page.getByRole('button',{name:'Open selected frames sidebar',exact:true}).count()===1,'Escape did not close drawer');
    sizes.push(width);
  }
  const preview=page.locator('article').first().getByRole('button',{name:'Video',exact:true});
  await preview.click();
  const modal=page.getByRole('dialog',{name:'Video preview'});
  await modal.waitFor();
  assert(await modal.evaluate(e=>e.contains(document.activeElement)),'Modal did not acquire focus');
  await page.keyboard.press('Shift+Tab');
  assert(await modal.evaluate(e=>e.contains(document.activeElement)),'Focus escaped on Shift+Tab');
  await page.keyboard.press('Tab');
  assert(await modal.evaluate(e=>e.contains(document.activeElement)),'Focus escaped on Tab');
  await page.screenshot({path:'output/playwright/optimization-video-focus.png'});
  await page.keyboard.press('Escape');
  assert(await modal.count()===0,'Escape did not close preview');
  assert(await preview.evaluate(e=>e===document.activeElement),'Focus not returned to opener');
  return {searchUncoveredAt:sizes,modalFocus:true,tabCycle:true,escape:true,focusRestored:true};
}

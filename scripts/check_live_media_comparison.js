async(page)=>{
  const report=[];
  await page.unrouteAll({behavior:'ignoreErrors'});
  for(const [variant,port] of [['before',5174],['after',5175]]){
    await page.goto(`http://127.0.0.1:${port}`);
    await page.getByRole('button',{name:'KIS Find exact scene'}).click();
    await page.getByRole('button',{name:'New search',exact:true}).click();
    await page.locator('article').first().waitFor();
    const requests=[];
    const listener=req=>{if(req.url().includes('/api/media/'))requests.push(req.url().split('/api/media/')[1].split('?')[0]);};
    page.on('request',listener);
    await page.locator('article').first().getByRole('button',{name:'Video',exact:true}).click();
    const video=page.locator('video');
    await video.waitFor();
    await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
    const playback=await video.evaluate(async el=>{el.muted=true;const before=el.currentTime;await el.play();await new Promise(r=>setTimeout(r,600));const after=el.currentTime;el.pause();return {before,after,advanced:after>before};});
    const show=page.getByRole('button',{name:'Show text evidence',exact:true});
    if(await show.count())await show.click();
    await page.waitForTimeout(1200);
    const evidenceText=await page.locator('.video-modal').innerText();
    const firstRequests=[...requests];
    requests.length=0;
    await page.getByRole('button',{name:'Close video',exact:true}).click();
    await page.locator('article').first().getByRole('button',{name:'Video',exact:true}).click();
    await video.waitFor();
    if(await show.count())await show.click();
    await page.waitForTimeout(1200);
    const reopenedRequests=[...requests];
    await page.screenshot({path:`output/playwright/e2e-${variant}-evidence.png`});
    report.push({variant,playback,evidenceSections:{ocr:/OCR/i.test(evidenceText),asr:/ASR|Speech|Transcript/i.test(evidenceText),caption:/Caption/i.test(evidenceText)},firstRequests,reopenedRequests});
    page.off('request',listener);
    await page.getByRole('button',{name:'Close video',exact:true}).click();
  }
  return report;
}

async(page)=>{
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.goto('http://127.0.0.1:5173');
  await page.getByRole('button',{name:'KIS Find exact scene'}).click();
  await page.getByRole('button',{name:'New search',exact:true}).click();
  const checks=[];
  for(const width of [390,900,1024,1152,1180,1440]){
    await page.setViewportSize({width,height:800});
    for(const side of ['left','selected frames']){
      await page.keyboard.press('Escape');
      const open=page.getByRole('button',{name:`Open ${side} sidebar`,exact:true});
      if(await open.count())await open.click();
      await page.waitForTimeout(150);
      const uncovered=await page.getByRole('button',{name:'Run search',exact:true}).evaluate(b=>{
        const r=b.getBoundingClientRect();return b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
      });
      if(!uncovered)throw new Error(`Search covered: ${width} ${side}`);
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
      if(overflow)throw new Error(`Horizontal overflow: ${width} ${side}`);
      checks.push({width,side,uncovered});
    }
  }
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Settings',exact:true});
  if(!await dialog.evaluate(e=>e.contains(document.activeElement)))throw new Error('Settings focus missing');
  for(const label of ['Expansion','Metadata','Agent plan']){
    if(await dialog.getByRole('button',{name:label,exact:true}).getAttribute('aria-pressed')===null)throw new Error('Toggle semantics missing');
  }
  for(const theme of ['Light','Dark']){
    await dialog.getByRole('button',{name:theme,exact:true}).click();
    const ratio=await page.evaluate(()=>{
      const s=getComputedStyle(document.documentElement);
      const lum=hex=>{const v=hex.trim().slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return v[0]*.2126+v[1]*.7152+v[2]*.0722;};
      const text=lum(s.getPropertyValue('--text-3')), bg=lum(s.getPropertyValue('--bg-active'));
      return (Math.max(text,bg)+.05)/(Math.min(text,bg)+.05);
    });
    if(ratio<4.5)throw new Error(`${theme} muted text contrast ${ratio}`);
  }
  await page.keyboard.press('Escape');
  if(await dialog.count())throw new Error('Settings Escape failed');
  return {drawers:checks,zoom125EquivalentCssWidth:1152,settingsFocus:true,toggleSemantics:true,mutedContrast:true};
}

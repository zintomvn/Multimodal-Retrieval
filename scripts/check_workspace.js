async(page)=>{
  await page.addInitScript(token=>{
    if(sessionStorage.getItem('workspace-test-reset')!==token){
      localStorage.removeItem('multimodal-workspace-v1');sessionStorage.setItem('workspace-test-reset',token);
    }
  },String(Date.now()));
  await page.goto('http://127.0.0.1:5173');
  await page.locator('article').first().waitFor();
  const query=page.getByRole('textbox',{name:'Search query'});
  await query.fill('my retained KIS');
  await page.getByLabel('Search source',{exact:true}).selectOption('ocr');
  await page.locator('article').first().getByRole('button',{name:'Pick',exact:true}).click();
  await page.getByRole('button',{name:'QA Answer from video'}).click();
  await query.fill('independent QA draft');
  await page.getByRole('button',{name:'KIS Find exact scene'}).click();
  if(await query.inputValue()!=='my retained KIS') throw new Error('KIS draft lost');
  await page.waitForTimeout(350);await page.reload();
  await page.locator('article').first().waitFor();
  if(await query.inputValue()!=='my retained KIS') throw new Error('Reload lost draft');
  if(await page.getByLabel('Search source',{exact:true}).inputValue()!=='ocr') throw new Error('Options lost');
  if(await page.locator('.selected-row').count()!==1) throw new Error('Selections lost');
  const before=await page.getByLabel('Export CSV file name').inputValue();
  await page.getByRole('button',{name:'New search',exact:true}).click();
  const after=await page.getByLabel('Export CSV file name').inputValue();
  if(before===after) throw new Error('Query identity reused');
  if(await page.locator('.selected-row').count()!==0) throw new Error('New search retained selected frames');
  if(!await page.getByRole('button',{name:'Export current query CSV'}).isDisabled()) throw new Error('Previous selection leaked into new query');
  return {taskDrafts:true,reload:true,options:true,selections:true,uniqueQuery:true};
}

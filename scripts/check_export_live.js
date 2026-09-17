async (page) => {
  await page.unrouteAll({behavior:'ignoreErrors'});
  await page.goto('http://127.0.0.1:5173');
  await page.locator('article').first().waitFor();
  await page.locator('article').first().getByRole('button',{name:/select|pick/i}).click();
  const expected = await page.locator('.selected-row').first().innerText();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button',{name:'Export current query CSV'}).click();
  const file = await downloaded;
  await file.saveAs('output/playwright/optimization-live-export.csv');
  return {filename:file.suggestedFilename(),expected};
}

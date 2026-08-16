const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle2' });
  
  // Try to find anything that looks like a stock symbol
  const symbols = await page.evaluate(() => {
    // This is a guess of the DOM structure, but let's just grab all text that might be a symbol
    return document.body.innerText;
  });
  
  console.log("PAGE TEXT:");
  console.log(symbols);
  await browser.close();
})();

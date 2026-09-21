import { chromium } from 'playwright';
const GOAL = 'write a 2 sentence story about a dog';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' });
await page.waitForTimeout(1000);
await page.fill('textarea', GOAL);
await page.click('button:has-text("Execute (API)")');
let finalBody = null;
for (let i = 0; i < 600; i++) {
  await page.waitForTimeout(1000);
  finalBody = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('[class*="shadow"]')];
    for (const c of cards) {
      if (c.textContent && c.textContent.includes('Final Result')) return c.innerText;
    }
    return null;
  });
  if (finalBody && finalBody.includes('At 3 AM')) break;
}
await page.screenshot({ path: '/tmp/pw_final.png', fullPage: true });
console.log('=== FINAL RESULT CARD ===');
console.log(finalBody);
await browser.close();

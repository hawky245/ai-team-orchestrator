import { chromium } from 'playwright';
const GOAL = 'write a 2 sentence story about a dog';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on('pageerror', e => pageErrors.push(e.message));
await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' });
await page.waitForTimeout(1500);

async function captureToasts() {
  return page.evaluate(() => {
    const nodes = [...document.querySelectorAll('*')];
    const texts = [];
    for (const n of nodes) {
      if (n.children.length === 0 && n.textContent) {
        const t = n.textContent.trim();
        if (t && (t.includes('Error') || t.includes('Connection') || t.includes('Lost') || t.includes('Complete'))) {
          texts.push(t);
        }
      }
    }
    return texts;
  });
}

await page.fill('textarea', GOAL);
await page.click('button:has-text("Execute (WS)")');

let finalBody = null;
let toastTexts = [];
for (let i = 0; i < 600; i++) {
  await page.waitForTimeout(1000);
  finalBody = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('[class*="shadow"]')];
    for (const c of cards) {
      if (c.textContent && c.textContent.includes('Final Result')) return c.innerText;
    }
    return null;
  });
  toastTexts = await captureToasts();
  if (finalBody && finalBody.includes('At 3 AM')) break;
}
await page.screenshot({ path: '/tmp/pw_ws_fix.png', fullPage: true });
console.log('=== FINAL RESULT CARD ===');
console.log(finalBody);
console.log('=== TOASTS SEEN ===');
console.log(toastTexts.join('\n---\n') || '(none)');
console.log('=== PAGE ERRORS ===');
console.log(pageErrors.join('\n') || '(none)');
await browser.close();
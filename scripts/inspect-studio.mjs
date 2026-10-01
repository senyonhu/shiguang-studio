import { chromium } from '@playwright/test';
const browser = await chromium.launch({channel:'msedge',headless:true});
const page = await browser.newPage({viewport:{width:1440,height:960},reducedMotion:'reduce'});
await page.goto('http://127.0.0.1:5173/studio.html');
await page.locator('img.character').waitFor();
await page.locator('img.character').evaluate(img => img.decode());
console.log(await page.locator('img.character').evaluate(img => { const css=getComputedStyle(img); const canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight; const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0); return {rect:img.getBoundingClientRect().toJSON(),css:{opacity:css.opacity,display:css.display,visibility:css.visibility,zIndex:css.zIndex,position:css.position},center:[...ctx.getImageData(512,512,1,1).data],parentCss:getComputedStyle(img.parentElement).zIndex};}));
await page.screenshot({path:'.runtime/studio-inspect.png',fullPage:true});
await browser.close();

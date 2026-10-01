import { test, expect } from '@playwright/test';
test.use({contextOptions: {reducedMotion: 'reduce'}});

test.beforeEach(async ({page}) => {
  await page.goto('/studio.html');
  await expect(page.getByRole('heading', {name: '序章 · 风停在旧塔'})).toBeVisible();
});

test('studio renders real artwork, edits persist, export carries edits, and small layouts fit', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await expect(page.locator('.stage-bg')).toBeVisible();
  await expect.poll(() => page.locator('.stage-bg').evaluate((e: HTMLImageElement) => e.naturalWidth)).toBeGreaterThan(1000);
  await expect.poll(() => page.locator('img.character').evaluate((e: HTMLImageElement) => e.naturalWidth)).toBeGreaterThan(500);
  await page.locator('img.character').evaluate((e: HTMLImageElement) => e.decode());
  await page.screenshot({path: '.runtime/studio-workbench.png', fullPage: true});
  const line = '今晚，有你一起守着这束光。';
  await page.getByRole('textbox', {name:'台词内容'}).fill(line);
  await expect(page.locator('.dialogue-box p')).toHaveText(line);
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('shiguang-studio-project-v1') ?? 'null')?.nodes.find((n:any) => n.id === 'lantern')?.text)).toBe(line);
  await page.reload(); await expect(page.getByRole('textbox', {name:'台词内容'})).toHaveValue(line);
  const download = page.waitForEvent('download'); await page.getByRole('button', {name:'导出配置',exact:true}).click();
  expect((await download).suggestedFilename()).toBe('last-light-studio.json');
  await page.getByRole('button', {name:'AI 变更审查'}).click();
  await expect(page.getByText('原文已由你修改，需人工合并')).toBeVisible();
  await page.getByRole('button', {name:'剧情编排'}).click();
  await page.setViewportSize({width:390,height:844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path:'.runtime/studio-mobile.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('AI review accepts, undoes, and respects manually locked content', async ({page}) => {
  await page.getByRole('button', {name:'AI 变更审查'}).click();
  await expect(page.getByText('这是一组预置的 AI 修改示例')).toBeVisible();
  await page.screenshot({path:'.runtime/studio-review.png',fullPage:true});
  const first = page.locator('.review-card').first();
  await first.getByRole('button', {name:'采用修改'}).click();
  await expect(first.getByText('已采用',{exact:true})).toBeVisible();
  await first.getByRole('button', {name:'撤回修改'}).click();
  await expect(first.getByRole('button',{name:'采用修改'})).toBeVisible();
  await page.getByRole('checkbox',{name:'锁定此段内容'}).check();
  await expect(first.getByText('这段内容已锁定')).toBeVisible();
  await expect(page.getByRole('textbox',{name:'台词内容'})).toBeDisabled();
  await page.getByRole('checkbox',{name:'锁定此段内容'}).uncheck();
  await first.getByRole('button',{name:'保留原文'}).click();
  await expect(first.getByText('已保留原文')).toBeVisible();
});

test('story branches, HTML puzzle checkpoints, reload recovery, completion and ending', async ({page}) => {
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.getByRole('button',{name:'试玩故事',exact:true}).click();
  await expect(page.locator('.dialogue-box p')).toContainText('海风停在门外');
  await page.getByRole('button',{name:'继续',exact:true}).click();
  await page.locator('img.character').evaluate((e: HTMLImageElement) => e.decode());
  await page.screenshot({path:'.runtime/studio-play.png',fullPage:true});
  await page.getByRole('button',{name:'继续',exact:true}).click();
  await page.getByRole('button',{name:'先告诉我，这座塔的故事。'}).click();
  await expect(page.locator('.dialogue-box p')).toContainText('迷路的船只');
  await page.getByRole('button',{name:'继续',exact:true}).click();
  await page.getByRole('button',{name:'进入星图'}).click();
  const game = page.frameLocator('iframe');
  await expect(page.getByText('已连接剧情引擎')).toBeVisible();
  await page.screenshot({path:'.runtime/studio-puzzle.png',fullPage:true});
  await game.getByRole('button',{name:'转动外环',exact:true}).click();
  await expect(game.locator('#angle0')).toContainText('135°');
  await page.reload();
  await page.getByRole('button',{name:'继续上次试玩'}).click();
  await page.getByRole('button',{name:'进入星图'}).click();
  await expect(game.locator('#angle0')).toContainText('135°');
  await page.evaluate(() => window.postMessage({type:'level:complete',steps:[0,0,0],token:'forged'},'*'));
  await expect(page.locator('iframe')).toBeVisible();
  for(const [ring, name, start, count] of [[0,'转动外环',3,5],[1,'转动中环',5,3],[2,'转动内环',3,5]] as const) {
    for(let i=0;i<count;i++) {
      await game.getByRole('button',{name,exact:true}).click();
      const step = (start+i+1)%8;
      await expect(game.locator(`#angle${ring}`)).toContainText(step === 0 ? '已对齐' : `${step*45}°`);
    }
  }
  await expect(game.getByRole('heading',{name:'星图，再次亮起。'})).toBeVisible();
  await game.getByRole('button',{name:'带着星光，回到故事 →'}).click();
  await expect(page.locator('.dialogue-box p')).toContainText('海面上也有光了');
  await page.getByRole('button',{name:'继续',exact:true}).click();
  await expect(page.getByRole('heading',{name:'你为旧塔，带来了光。'})).toBeVisible();
  await page.getByRole('button',{name:'回到工作台',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'台词内容'})).toBeVisible();
  expect(errors).toEqual([]);
});

test('malformed import does not replace the current project', async ({page}) => {
  await page.locator('input[type=file]').first().setInputFiles({name:'broken.json',mimeType:'application/json',buffer:Buffer.from('{"version":1,"assets":[],"nodes":[]}')});
  await expect(page.getByRole('status')).toContainText('工程编号格式不正确');
  await expect(page.getByRole('textbox',{name:'台词内容'})).toHaveValue(/你终于来了/);
});

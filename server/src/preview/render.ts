// 像素级渲染产物（P2 / KI-002）：用无头浏览器把结构化舞台快照渲染成 PNG。
//
// 为什么单独成文件：这是可选能力——需要本机安装 Playwright 浏览器（开发依赖）。
// 若浏览器不可用，必须返回明确错误，绝不能伪造一张“看起来成功”的图片。
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { sha256Hex } from '../core/canonical.ts';
import { StudioError } from '../core/errors.ts';
import { ensureDir } from '../store/fsx.ts';
import { projectPaths } from '../store/paths.ts';

export interface PixelRenderRequest {
  projectId: string;
  html: string;
  width: number;
  height: number;
  label: string;
}

export interface PixelRenderResult {
  renderPath: string;
  absolutePath: string;
  sha256: string;
  width: number;
  height: number;
  bytes: number;
  renderer: string;
  note: string;
}

/** 环境变量：禁用像素渲染（用于验证“能力不可用时给出明确错误”的失败路径）。 */
const DISABLED = () => process.env.STUDIO_RENDER_DISABLE === '1';

export async function renderHtmlToPng(workspaceRoot: string, request: PixelRenderRequest): Promise<PixelRenderResult> {
  if (DISABLED()) {
    throw new StudioError('RENDER_UNAVAILABLE', '像素级渲染已被 STUDIO_RENDER_DISABLE=1 禁用（结构化快照仍然可用）');
  }
  let playwright: typeof import('playwright');
  try {
    playwright = await import('playwright');
  } catch (error) {
    throw new StudioError('RENDER_UNAVAILABLE', `像素级渲染需要本机安装 Playwright（${(error as Error).message}）`);
  }
  const paths = projectPaths(workspaceRoot, request.projectId);
  const tempDir = mkdtempSync(path.join(os.tmpdir(), 'studio-render-'));
  const htmlFile = path.join(tempDir, 'stage.html');
  writeFileSync(htmlFile, request.html, 'utf8');
  const dir = path.join(paths.studioDir, 'renders');
  ensureDir(dir);
  const name = `${request.label}-${Date.now().toString(36)}.png`;
  const abs = path.join(dir, name);

  let browser: Awaited<ReturnType<typeof playwright.chromium.launch>> | null = null;
  const attempts: { label: string; options: Parameters<typeof playwright.chromium.launch>[0] }[] = [
    // 优先用本机已安装的 Chromium 系浏览器：不需要额外下载 Playwright 自带内核。
    { label: 'edge', options: { channel: 'msedge', headless: true } },
    { label: 'chrome', options: { channel: 'chrome', headless: true } },
    { label: 'bundled-chromium', options: { headless: true } },
  ];
  const failures: string[] = [];
  let usedChannel = '';
  try {
    for (const attempt of attempts) {
      try {
        browser = await playwright.chromium.launch(attempt.options);
        usedChannel = attempt.label;
        break;
      } catch (error) {
        failures.push(`${attempt.label}: ${(error as Error).message.split('\n')[0]}`);
      }
    }
    if (!browser) {
      throw new StudioError('RENDER_UNAVAILABLE', `像素级渲染需要本机可用的 Chromium 系浏览器（Edge/Chrome，或执行 npx playwright install chromium）。尝试结果：${failures.join('；')}`);
    }
    const page = await browser.newPage({ viewport: { width: request.width, height: request.height }, deviceScaleFactor: 1 });
    await page.goto(`file://${htmlFile.replace(/\\/g, '/')}`, { waitUntil: 'load' });
    await page.screenshot({ path: abs, type: 'png' });
  } catch (error) {
    if (error instanceof StudioError) throw error;
    throw new StudioError('RENDER_FAILED', `像素级渲染失败：${(error as Error).message}`);
  } finally {
    await browser?.close().catch(() => undefined);
    rmSync(tempDir, { recursive: true, force: true });
  }

  const bytes = statSync(abs).size;
  return {
    renderPath: path.relative(paths.root, abs).replace(/\\/g, '/'),
    absolutePath: abs,
    sha256: sha256Hex(readFileSync(abs)),
    width: request.width,
    height: request.height,
    bytes,
    renderer: `playwright chromium（${usedChannel}）${request.width}x${request.height}`,
    note: '像素级渲染产物（无头浏览器截图），基于与运行内核一致的结构化舞台快照。',
  };
}

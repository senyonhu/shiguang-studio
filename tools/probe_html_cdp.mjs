// Real WebView2 browser input probe. Never calls a page's victory function.
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import crypto from "node:crypto";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const argv = process.argv.slice(2);
const value = (name, fallback) => {
  const i = argv.indexOf(name);
  return i < 0 ? fallback : argv[i + 1];
};
const port = Number(value("--port", "0"));
const out = path.resolve(value("--output", path.join(root, "evidence/html-h0-browser/runs", Date.now().toString())));
const actionsPath = value("--actions", null);
const prefix = value("--url-prefix", "https://minigame.local/");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("A loopback development port is required.");
if (!out.startsWith(root + path.sep)) throw new Error("Evidence output must be inside the workspace.");
await fs.mkdir(out, { recursive: true });
const started = performance.now();
const report = { scope: "actual WebView2 DOM/input via development-only CDP", status: "failed",
  started_at: new Date().toISOString(), port, required_url_prefix: prefix, actions: [], files: {}, page_errors: [], console_errors: [],
  tool_sha256: crypto.createHash("sha256").update(await fs.readFile(fileURLToPath(import.meta.url))).digest("hex") };
let ws, target, next = 1;
const pending = new Map();
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async (fn, seconds = 45) => {
  const deadline = performance.now() + seconds * 1000;
  let last;
  while (performance.now() < deadline) {
    try { const r = await fn(); if (r) return r; } catch (e) { last = e; }
    await delay(150);
  }
  throw new Error("Timed out: " + (last?.message ?? "condition not reached"));
};
function command(method, params = {}) {
  const id = next++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error("CDP timeout: " + method)); }, 10000);
    pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function node(selector) {
  const { root: doc } = await command("DOM.getDocument");
  const { nodeId } = await command("DOM.querySelector", { nodeId: doc.nodeId, selector });
  return nodeId || null;
}
async function textAt(selector) {
  const result = await command("Runtime.evaluate", {
    expression: "document.querySelector(" + JSON.stringify(selector) + ")?.textContent ?? null",
    returnByValue: true,
  });
  return result.result.value;
}
async function clickableNode(selector) {
  const result = await command("Runtime.evaluate", {
    expression: "(() => { const e = document.querySelector(" + JSON.stringify(selector) + "); return !!e && !e.disabled && e.getClientRects().length > 0; })()",
    returnByValue: true,
  });
  return result.result.value ? node(selector) : null;
}
async function click(selector, timeout = 15) {
  const nodeId = await until(() => clickableNode(selector), timeout);
  await command('DOM.scrollIntoViewIfNeeded', {nodeId});
  const { model } = await command("DOM.getBoxModel", { nodeId });
  const b = model.content;
  const x = (b[0] + b[2] + b[4] + b[6]) / 4;
  const y = (b[1] + b[3] + b[5] + b[7]) / 4;
  await command("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await command("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
}
function solveBoard(initial) {
  if (initial.length !== 9 || new Set(initial).size !== 9 || initial.some(value => !Number.isInteger(value) || value < 0 || value > 8)) {
    throw new Error('Observed board is not a supported 3×3 puzzle');
  }
  const start = initial.join("");
  const goal = "123456780";
  const queue = [start], prior = new Map([[start, null]]);
  for (let i = 0; i < queue.length; i++) {
    const current = queue[i];
    if (current === goal) {
      const moves = [];
      for (let value = goal; prior.get(value); value = prior.get(value).previous) moves.push(prior.get(value).tile);
      return moves.reverse();
    }
    const zero = current.indexOf("0");
    const neighbours = [zero - 3, zero + 3, zero % 3 ? zero - 1 : -1, zero % 3 < 2 ? zero + 1 : -1]
      .filter(value => value >= 0 && value < 9);
    for (const slot of neighbours) {
      const values = current.split("");
      const tile = values[slot];
      [values[zero], values[slot]] = [values[slot], values[zero]];
      const candidate = values.join("");
      if (!prior.has(candidate)) { prior.set(candidate, { previous: current, tile }); queue.push(candidate); }
    }
  }
  throw new Error("Observed puzzle is not solvable");
}
async function screenshot(name) {
  if (!/^[a-zA-Z0-9_-]+$/.test(name)) throw new Error("Invalid screenshot name");
  const result = await command("Page.captureScreenshot", { format: "png" });
  const bytes = Buffer.from(result.data, "base64");
  const file = path.join(out, name + ".png");
  await fs.writeFile(file, bytes);
  report.files[name] = { path: file, sha256: crypto.createHash("sha256").update(bytes).digest("hex"), size: bytes.length };
}
try {
  target = await until(async () => {
    const r = await fetch("http://127.0.0.1:" + port + "/json/list");
    const targets = await r.json();
    return targets.find(t => t.type === "page" && t.url.startsWith(prefix));
  }, 60);
  report.target = { id: target.id, url: target.url, title: target.title };
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  ws.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') report.page_errors.push(message.params.exceptionDetails);
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') report.console_errors.push(message.params);
    const p = pending.get(message.id);
    if (!p) return;
    clearTimeout(p.timer); pending.delete(message.id);
    message.error ? p.reject(new Error(JSON.stringify(message.error))) : p.resolve(message.result);
  });
  ws.addEventListener("close", () => {
    for (const p of pending.values()) { clearTimeout(p.timer); p.reject(new Error("WebView2 closed")); }
    pending.clear();
  });
  await command("Page.enable");
  await command('Runtime.enable');
  await screenshot("entered");
  const actions = actionsPath ? JSON.parse(await fs.readFile(actionsPath, "utf8")) : [];
  report.actions_sha256 = shaActions(actions);
  for (const action of actions) {
    const began = performance.now();
    if (action.type === "click") {
      await click(action.selector, action.timeout ?? 15);
    } else if (action.type === "solve_puzzle") {
      await until(() => clickableNode("[data-tile]:not([disabled])"));
      const result = await command("Runtime.evaluate", {
        expression: "document.querySelector('[data-testid=board]').dataset.order", returnByValue: true,
      });
      const board = result.result.value.split(",").map(Number);
      const moves = solveBoard(board);
      report.puzzle = { observed_board: board, actual_move_sequence: moves };
      await screenshot("puzzle_ready");
      for (let i = 0; i < moves.length; i++) {
        if (i === moves.length - 1) await screenshot("before_victory");
        await click('[data-tile="' + moves[i] + '"]');
      }
    } else if (action.type === "follow_signal") {
      await until(() => clickableNode('.target.lit:not([disabled])'));
      const progress = await textAt('#progress');
      const match = /^\s*0\s*\/\s*(\d+)\s*$/.exec(progress ?? '');
      const rounds = Number(match?.[1]);
      if (!Number.isInteger(rounds) || rounds < 3 || rounds > 24) throw new Error('A fresh signal board with 3-24 visible rounds is required');
      report.signal = { observed_rounds: rounds, actual_terminal_sequence: [] };
      for (let i = 0; i < rounds; i++) {
        const observed = await command('Runtime.evaluate', {
          expression: "document.querySelector('.target.lit:not([disabled])')?.getAttribute('data-target')", returnByValue: true,
        });
        const terminal = observed.result.value;
        if (!/^[0-3]$/.test(terminal ?? '')) throw new Error('The actual lit terminal is unavailable');
        if (i === rounds - 1) await screenshot('before_signal_connected');
        await click('[data-target="' + terminal + '"].lit:not([disabled])');
        report.signal.actual_terminal_sequence.push(Number(terminal));
      }
    } else if (action.type === "key") {
      await command("Input.dispatchKeyEvent", { type: "keyDown", key: action.key, code: action.code ?? action.key,
        windowsVirtualKeyCode: action.virtual_key ?? 0 });
      await command("Input.dispatchKeyEvent", { type: "keyUp", key: action.key, code: action.code ?? action.key,
        windowsVirtualKeyCode: action.virtual_key ?? 0 });
    } else if (action.type === "wait_selector") {
      await until(() => node(action.selector), action.timeout ?? 15);
    } else if (action.type === "wait_text") {
      await until(async () => (await textAt(action.selector))?.includes(action.text), action.timeout ?? 15);
    } else if (action.type === "assert_text") {
      const actual = await textAt(action.selector);
      if (!actual?.includes(action.text)) throw new Error("Expected text " + action.text + "; got " + actual);
      report.actions.push({ ...action, actual, duration_ms: performance.now() - began });
      continue;
    } else if (action.type === "capture") {
      await screenshot(action.name);
    } else if (action.type === "delay") {
      if (!Number.isFinite(action.ms) || action.ms < 0 || action.ms > 15000) throw new Error("Invalid bounded input delay");
      await delay(action.ms);
    } else {
      throw new Error("Unknown action " + action.type);
    }
    report.actions.push({ ...action, duration_ms: performance.now() - began });
  }
  if (report.page_errors.length || report.console_errors.length) throw new Error('The actual page reported JavaScript errors');
  report.status = "passed";
} catch (error) {
  report.error = { message: error.message, stack: error.stack };
  if (ws?.readyState === WebSocket.OPEN) await screenshot("failure").catch(() => {});
} finally {
  ws?.close();
  report.finished_at = new Date().toISOString();
  report.duration_ms = performance.now() - started;
  await fs.writeFile(path.join(out, "report.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ status: report.status, report: path.join(out, "report.json"), actions: report.actions.length }));
  if (report.status !== "passed") process.exitCode = 1;
}

function shaActions(actions) { return crypto.createHash('sha256').update(JSON.stringify(actions)).digest('hex'); }


// Isolated real WebView2 host probe. Uses actual DOM mouse input, never injects success.
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import crypto from "node:crypto";
import { spawn } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const option = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
const out = path.resolve(option("--output", path.join(root, "evidence/html-h0-host/runs", Date.now().toString())));
if (!out.startsWith(root + path.sep)) throw new Error("Output must be in the workspace.");
await fs.mkdir(out, { recursive: true });
const executable = path.resolve(option("--host", path.join(root, "runtimes/html-minigame-host/win-x64/HtmlMinigameHost.exe")));
const basePort = Number(option("--base-port", "9341"));
const sha = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, ms = 30000) {
  const deadline = performance.now() + ms;
  let last;
  while (performance.now() < deadline) {
    try { const result = await fn(); if (result) return result; } catch (error) { last = error; }
    await delay(70);
  }
  throw new Error("Timeout: " + (last?.message ?? "condition was not reached"));
}
async function packageHash(directory) {
  const files = [];
  async function walk(dir) {
    for (const item of await fs.readdir(dir, { withFileTypes: true })) {
      if (item.isSymbolicLink()) throw new Error("Package links are forbidden.");
      const full = path.join(dir, item.name);
      if (item.isDirectory()) await walk(full);
      else if (item.isFile()) files.push({ rel: path.relative(directory, full).split(path.sep).join("/"), full });
    }
  }
  await walk(directory);
  files.sort((a, b) => Buffer.compare(Buffer.from(a.rel), Buffer.from(b.rel)));
  const digest = crypto.createHash("sha256");
  for (const file of files) digest.update(Buffer.concat([Buffer.from(file.rel), Buffer.from([0]), Buffer.from(sha(await fs.readFile(file.full)) + "\n")]));
  return digest.digest("hex");
}
async function cdp(port, origin) {
  const target = await until(async () => (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json())
    .find(item => item.type === "page" && item.url.startsWith(origin)));
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.addEventListener("open", resolve, { once: true }); ws.addEventListener("error", reject, { once: true }); });
  let next = 1;
  const requests = new Map();
  ws.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data), pending = requests.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timer); requests.delete(message.id);
    message.error ? pending.reject(new Error(JSON.stringify(message.error))) : pending.resolve(message.result);
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = next++;
    const timer = setTimeout(() => { requests.delete(id); reject(new Error("CDP timeout: " + method)); }, 5000);
    requests.set(id, { resolve, reject, timer }); ws.send(JSON.stringify({ id, method, params }));
  });
  const read = async expression => {
    const result = await command("Runtime.evaluate", { expression, returnByValue: true });
    if (result.exceptionDetails) throw new Error("Read-only DOM query failed.");
    return result.result.value;
  };
  const click = async selector => {
    const { root: doc } = await command("DOM.getDocument");
    const { nodeId } = await command("DOM.querySelector", { nodeId: doc.nodeId, selector });
    if (!nodeId) throw new Error("Missing actual control: " + selector);
    const { model } = await command("DOM.getBoxModel", { nodeId });
    const [x1,y1,x2,y2,x3,y3,x4,y4] = model.content, x = (x1+x2+x3+x4)/4, y = (y1+y2+y3+y4)/4;
    await command("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await command("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await command("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
  };
  const capture = async file => {
    const { data } = await command("Page.captureScreenshot", { format: "png" });
    const bytes = Buffer.from(data, "base64"); await fs.writeFile(file, bytes);
    return { path: file, bytes: bytes.length, sha256: sha(bytes) };
  };
  const close = () => { for (const request of requests.values()) { clearTimeout(request.timer); request.reject(new Error("CDP closed")); } requests.clear(); ws.close(); };
  return { target, command, read, click, capture, close };
}
function solvePuzzle(board) {
  const goal = "1,2,3,4,5,6,7,8,0", queue = [{ board, path: [] }], seen = new Set([board.join(",")]);
  for (let offset = 0; offset < queue.length; offset++) {
    const current = queue[offset], key = current.board.join(",");
    if (key === goal) return current.path;
    const empty = current.board.indexOf(0);
    const adjacent = [empty-3, empty+3, empty%3 ? empty-1 : -1, empty%3<2 ? empty+1 : -1].filter(i => i>=0 && i<9);
    for (const index of adjacent) {
      const next = [...current.board], tile = next[index]; [next[empty], next[index]] = [next[index], next[empty]];
      const nextKey = next.join(","); if (seen.has(nextKey)) continue;
      seen.add(nextKey); queue.push({ board: next, path: [...current.path, tile] });
    }
  }
  throw new Error("Puzzle has no solution.");
}
const report = { scope: "isolated self-contained Windows host + real WebView2 CDP DOM input", status: "failed", started_at: new Date().toISOString(),
  executable, executable_sha256: sha(await fs.readFile(executable)), cases: [], limitations: ["Current workstation only; no multi-DPI/multi-monitor certification", "Not Ren'Py integration; not real system-output audio validation"] };

async function runCase(name, game, inputs, play, { snapshot = null, cancel = false, abrupt = false } = {}) {
  const dir = path.join(out, name); await fs.mkdir(dir, { recursive: true });
  const packageRoot = path.join(root, "examples/html_minigames", game);
  const manifest = JSON.parse(await fs.readFile(path.join(packageRoot, "manifest.json"), "utf8"));
  const hash = await packageHash(packageRoot), session = "host_probe_" + crypto.randomUUID().replaceAll("-", "");
  const port = basePort + report.cases.length;
  const command = ["--package-root", packageRoot, "--entry", manifest.entry, "--session-id", session,
    "--game-id", manifest.game_id, "--package-version", manifest.version, "--package-hash", hash,
    "--user-data-root", path.join(dir, "user-data"), "--development", "--devtools-port", String(port)];
  const record = { name, game, package_hash: hash, session_id: session, command, events: [], sent: [], stderr: "", status: "failed", files: [] };
  report.cases.push(record);
  const child = spawn(executable, command, { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  let buffer = "", exit = null, browser;
  child.stdout.setEncoding("utf8"); child.stdout.on("data", chunk => {
    buffer += chunk; let index;
    while ((index = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 1);
      if (!line) continue;
      try { record.events.push(JSON.parse(line)); } catch { record.events.push({ parse_error: line }); }
    }
  });
  child.stderr.setEncoding("utf8"); child.stderr.on("data", chunk => record.stderr += chunk);
  child.on("exit", (code, signal) => exit = { code, signal });
  const send = (type, payload = {}, override = {}) => {
    const message = { protocol: "1.0", session_id: session, event_id: crypto.randomUUID().replaceAll("-", ""), type, payload, ...override };
    record.sent.push(message); child.stdin.write(JSON.stringify(message) + "\n"); return message;
  };
  const event = type => until(() => record.events.find(item => item.type === type));
  const ack = message => send("ack", { event_id: message.event_id, accepted: true });
  try {
    const ready = await event("ready");
    if (ready.session_id !== session || !ready.payload.host.webview2_runtime) throw new Error("Missing actual runtime/ready identity.");
    record.actual_runtime = ready.payload.host.webview2_runtime;
    browser = await cdp(port, `https://minigame-${manifest.game_id}.invalid/`);
    send("init", { game_id: manifest.game_id, package_version: manifest.version, package_hash: hash, inputs, snapshot, settings: { audio_volume: 0.7 }, capabilities: { checkpoint: true } });
    send("start");
    await until(() => browser.read("document.querySelector('[data-testid=status]').textContent !== '正在等待游戏开始……'"));
    record.files.push(await browser.capture(path.join(dir, "entered.png")));
    if (abrupt) { child.kill(); await until(() => exit); record.exit = exit; record.status = "passed"; return; }
    if (cancel) { send("cancel", { reason: "probe_cancel" }); await event("cancel"); }
    else {
      // Deliberately mismatched parent message must be rejected without changing the valid session.
      send("pause", {}, { session_id: "wrong_session" });
      await until(() => record.events.find(item => item.type === "error" && item.payload.code === "PARENT_MESSAGE_REJECTED"));
      await play({ browser, send, event, ack, record });
      const finish = await event("finish");
      record.result = finish.payload;
      record.files.push(await browser.capture(path.join(dir, "completed-before-ack.png")));
      if (exit) throw new Error("Host exited before finish acknowledgement.");
      ack(finish); await event("closed");
    }
    await until(() => exit, 10000); record.exit = exit;
    if (exit.code !== 0 || record.events.some(item => item.parse_error)) throw new Error("Unexpected exit or stdout pollution.");
    record.status = "passed";
  } catch (error) { record.error = { message: error.message, stack: error.stack }; throw error; }
  finally {
    browser?.close();
    if (!exit) { try { send("cancel", { reason: "probe_cleanup" }); } catch {} await delay(400); if (!exit) child.kill(); }
    await fs.writeFile(path.join(dir, "events.json"), JSON.stringify(record.events, null, 2) + "\n");
    await fs.writeFile(path.join(dir, "stderr.txt"), record.stderr);
  }
}
try {
  await runCase("puzzle-win", "puzzle", { seed: 1742, scramble_moves: 12 }, async ({ browser, send, event, record }) => {
    const order = await browser.read("document.querySelector('[data-testid=board]').dataset.order");
    record.observed_initial_board = order; const solution = solvePuzzle(order.split(",").map(Number)); record.actual_tile_clicks = solution;
    send("pause"); await until(() => browser.read("document.querySelector('[data-testid=status]').textContent.includes('已暂停')"));
    const paused = await browser.read("document.querySelector('[data-testid=board]').dataset.order");
    await browser.click(`[data-testid=tile-${solution[0]}]`);
    if (await browser.read("document.querySelector('[data-testid=board]').dataset.order") !== paused) throw new Error("Paused puzzle accepted movement.");
    send("resume"); await until(() => browser.read("document.querySelector('[data-testid=status]').textContent.includes('已继续')"));
    for (const tile of solution) await browser.click(`[data-testid=tile-${tile}]`);
    if ((await event("finish")).payload.outcome !== "win") throw new Error("Puzzle did not win from actual moves.");
  });
  await runCase("timed-win", "timed", { duration_seconds: 12, rounds: 3 }, async ({ browser, event }) => {
    await browser.click("#begin"); for (const target of [0,2,1]) await browser.click(`[data-target='${target}']`);
    if ((await event("finish")).payload.outcome !== "win") throw new Error("Timed success mismatch.");
  });
  await runCase("timed-lose", "timed", { duration_seconds: 3, rounds: 3 }, async ({ browser, event }) => {
    await browser.click("#begin"); if ((await event("finish")).payload.outcome !== "lose") throw new Error("Timed expiration mismatch.");
  });
  await runCase("checkpoint-win", "checkpoint", { target_temperature: 37 }, async ({ browser, event, ack, record }) => {
    await browser.click("[data-part=temperature]"); const first = await event("checkpoint"); ack(first);
    await until(() => browser.read("!document.querySelector('[data-wire=blue]').disabled"));
    await browser.click("[data-wire=blue]");
    const second = await until(() => record.events.find(item => item.type === "checkpoint" && item.event_id !== first.event_id)); ack(second);
    record.checkpoints = [first.payload, second.payload];
    await until(() => browser.read("!document.querySelector('#plus').disabled"));
    for (let index=0; index<3; index++) await browser.click("#plus"); await browser.click("#verify");
  });
  await runCase("parent-cancel", "puzzle", { seed: 1, scramble_moves: 2 }, async () => {}, { cancel: true });
  await runCase("forced-host-termination", "puzzle", { seed: 1, scramble_moves: 2 }, async () => {}, { abrupt: true });
  report.status = "passed";
} catch (error) { report.error = { message: error.message, stack: error.stack }; }
finally {
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(out, "report.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ status: report.status, cases: report.cases.map(item => ({ name: item.name, status: item.status })), report: path.join(out, "report.json") }));
  if (report.status !== "passed") process.exitCode = 1;
}

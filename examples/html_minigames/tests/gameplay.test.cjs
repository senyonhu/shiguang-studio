"use strict";
// Runs the actual page scripts with a small DOM/clock adapter. This is not browser or release evidence.
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = process.env.HTML_MINIGAME_TEST_ROOT || path.join(__dirname, "..");

class Element {
  constructor(tag, attributes = "") {
    this.tagName = tag;
    this.dataset = {};
    this.children = [];
    this.listeners = new Map();
    this.disabled = /\bdisabled\b/.test(attributes);
    this.hidden = /\bhidden\b/.test(attributes);
    this.textContent = "";
    this.className = (/class="([^"]*)"/.exec(attributes) || [])[1] || "";
    this.classes = new Set(this.className.split(/\s+/).filter(Boolean));
    this.classList = { add: name => this.classes.add(name), contains: name => this.classes.has(name),
      toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name) };
    for (const match of attributes.matchAll(/data-([a-z-]+)="([^"]*)"/g)) this.dataset[match[1].replace(/-([a-z])/g, (_, character) => character.toUpperCase())] = match[2];
    this.volume = 1;
    this.currentTime = 0;
    this.playCount = 0;
  }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  setAttribute() {}
  replaceChildren() { this.children = []; }
  append(element) { this.children.push(element); }
  click() { if (!this.disabled) this.listeners.get("click")?.({}); }
  play() { this.playCount++; return Promise.resolve(); }
  pause() {}
}

function fixture(game) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, game, "manifest.json"), "utf8"));
  const elements = [];
  const byId = new Map();
  const html = fs.readFileSync(path.join(root, game, "index.html"), "utf8");
  for (const match of html.matchAll(/<(button|div|strong|section|audio|p|main)\b([^>]*)>/g)) {
    const element = new Element(match[1], match[2]);
    elements.push(element);
    const id = (/\bid="([^"]*)"/.exec(match[2]) || [])[1];
    if (id) byId.set(id, element);
  }
  const documentEvents = new Map();
  const document = {
    getElementById: id => byId.get(id),
    createElement: tag => new Element(tag),
    addEventListener: (name, callback) => documentEvents.set(name, callback),
    querySelectorAll: selector => elements.filter(element => {
      if (selector === "button") return element.tagName === "button";
      const attribute = /^\[data-([a-z-]+)\]$/.exec(selector);
      return attribute && Object.hasOwn(element.dataset, attribute[1]);
    })
  };
  let time = 0;
  let messageListener;
  const posted = [];
  const intervals = new Map();
  const timeouts = new Map();
  let id = 0;
  const setInterval = callback => { intervals.set(++id, callback); return id; };
  const clearInterval = timer => intervals.delete(timer);
  const setTimeout = callback => { timeouts.set(++id, callback); return id; };
  const clearTimeout = timer => timeouts.delete(timer);
  const window = { chrome: { webview: {
    postMessage: message => posted.push(JSON.parse(JSON.stringify(message))),
    addEventListener: (_, callback) => { messageListener = callback; }
  } }, addEventListener() {}, setInterval, clearInterval, setTimeout, clearTimeout };
  const context = vm.createContext({ window, document, performance: { now: () => time },
    TextEncoder, setInterval, clearInterval, setTimeout, clearTimeout, console,
    fetch: async filename => ({ ok: true, text: async () => fs.readFileSync(path.join(root, game, filename), "utf8") }) });
  vm.runInContext(fs.readFileSync(path.join(root, game, "bridge-sdk.js"), "utf8"), context);
  context.GalgameBridge = window.GalgameBridge;
  vm.runInContext(fs.readFileSync(path.join(root, game, "game.js"), "utf8"), context);
  const send = (type, payload = {}) => messageListener({ data: { protocol: "1.0",
    session_id: "page-session", event_id: "host-" + type, type, payload } });
  return {
    posted, byId, elements, send,
    init: (inputs = {}, snapshot = null, settings = undefined) => { send("init", { game_id: manifest.game_id, inputs, snapshot, settings }); send("start"); },
    tick: milliseconds => { time += milliseconds; for (const callback of [...intervals.values()]) callback(); },
    ack: message => send("ack", { event_id: message.event_id, accepted: true }),
    testid: value => elements.find(element => element.dataset.testid === value),
    flush: () => new Promise(resolve => setImmediate(resolve))
  };
}

module.exports = { fixture };

function solve(initial) {
  const target = "1,2,3,4,5,6,7,8,0";
  const pending = [{ board: initial, path: [] }];
  const seen = new Set([initial.join(",")]);
  for (let cursor = 0; cursor < pending.length; cursor++) {
    const { board, path } = pending[cursor];
    if (board.join(",") === target) return path;
    const empty = board.indexOf(0);
    const adjacent = [empty - 3, empty + 3, empty % 3 ? empty - 1 : -1, empty % 3 < 2 ? empty + 1 : -1].filter(index => index >= 0 && index < 9);
    for (const position of adjacent) {
      const next = board.slice();
      [next[empty], next[position]] = [next[position], next[empty]];
      if (!seen.has(next.join(","))) { seen.add(next.join(",")); pending.push({ board: next, path: [...path, board[position]] }); }
    }
  }
  throw new Error("Unsovable puzzle");
}

test("puzzle only wins after actual legal tile operations", async () => {
  const f = fixture("puzzle");
  f.init();
  const board = f.byId.get("board");
  const initial = board.dataset.order.split(",").map(Number);
  assert.notEqual(initial.join(","), "1,2,3,4,5,6,7,8,0");
  const route = solve(initial);
  assert(route.length > 0);
  for (const value of route) {
    const tile = board.children.find(element => element.dataset.tile === String(value));
    assert.equal(tile.disabled, false);
    tile.click();
  }
  const finish = f.posted.find(message => message.type === "finish");
  assert.equal(finish.payload.outcome, "win");
  assert.equal(finish.payload.results.moves, route.length);
  assert.equal(board.dataset.order, "1,2,3,4,5,6,7,8,0");
  f.ack(finish);
  await f.flush();
});

test("timed game reaches true victory and true timeout failure", async () => {
  const win = fixture("timed");
  win.init();
  win.testid("begin").click();
  for (let index = 0; index < 6; index++) {
    const target = win.elements.find(element => element.dataset.target !== undefined && element.classList.contains("lit"));
    assert(target);
    target.click();
  }
  const finish = win.posted.find(message => message.type === "finish");
  assert.equal(finish.payload.outcome, "win");
  assert.equal(finish.payload.results.hits, 6);
  assert(win.byId.get("tone").playCount >= 6);
  win.ack(finish);
  await win.flush();
  const lose = fixture("timed");
  lose.init({ duration_seconds: 3 });
  lose.testid("begin").click();
  lose.tick(3100);
  assert.equal(lose.posted.find(message => message.type === "finish").payload.outcome, "lose");
});

test("timed pause does not consume countdown", () => {
  const f = fixture("timed");
  f.init({ duration_seconds: 3 });
  f.testid("begin").click();
  f.send("pause");
  f.tick(30000);
  assert.equal(f.posted.some(message => message.type === "finish"), false);
  assert.equal(f.byId.get("remaining").textContent, "3.0");
  f.send("resume");
  f.tick(3100);
  assert.equal(f.posted.find(message => message.type === "finish").payload.outcome, "lose");
});

test("checkpoint actual steps restore JSON and require real calibration", async () => {
  const f = fixture("checkpoint");
  f.init();
  f.testid("part-humidity").click();
  assert.equal(f.byId.get("workshop").dataset.stage, "0");
  f.testid("part-temperature").click();
  const first = f.posted.find(message => message.type === "checkpoint");
  assert.equal(first.payload.state.stage, 1);
  f.ack(first);
  await f.flush();
  f.testid("wire-blue").click();
  const second = f.posted.filter(message => message.type === "checkpoint").at(-1);
  assert.equal(second.payload.state.stage, 2);
  const restored = fixture("checkpoint");
  restored.init({}, second.payload);
  assert.equal(restored.byId.get("workshop").dataset.stage, "2");
  restored.testid("verify-calibration").click();
  assert.equal(restored.posted.some(message => message.type === "finish"), false);
  for (let index = 0; index < 3; index++) restored.testid("calibration-plus").click();
  restored.testid("verify-calibration").click();
  const finish = restored.posted.find(message => message.type === "finish");
  assert.equal(finish.payload.outcome, "win");
  assert.equal(finish.payload.results.calibrated_temperature, 37);
  assert.equal(finish.payload.results.mistakes, 2);
  restored.ack(finish);
  await restored.flush();
  f.ack(second);
  await f.flush();
});

test("invalid checkpoint is rejected instead of auto winning or resetting progress", () => {
  const f = fixture("checkpoint");
  f.init({}, { snapshot_version: "1.0", state: { stage: 2, sensor: null, wire: "blue", calibration: 37, mistakes: 0 } });
  assert(f.posted.some(message => message.type === "error" && message.payload.code === "SNAPSHOT_INVALID"));
  f.testid("verify-calibration").click();
  assert.equal(f.posted.some(message => message.type === "finish"), false);
});

"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const packageRoot = process.env.HTML_MINIGAME_TEST_ROOT || path.join(__dirname, "..");
const source = fs.readFileSync(path.join(packageRoot, "sdk/bridge-sdk.js"), "utf8");

function fixture() {
  const posts = [];
  const listeners = new Map();
  const intervals = new Map();
  const timeouts = new Map();
  let timerId = 0;
  let receive;
  const window = {
    chrome: { webview: {
      postMessage: message => posts.push(JSON.parse(JSON.stringify(message))),
      addEventListener: (name, callback) => { if (name === "message") receive = callback; }
    } },
    addEventListener: (name, callback) => listeners.set(name, callback),
    setInterval: callback => { intervals.set(++timerId, callback); return timerId; },
    clearInterval: id => intervals.delete(id),
    setTimeout: callback => { timeouts.set(++timerId, callback); return timerId; },
    clearTimeout: id => timeouts.delete(id)
  };
  vm.runInNewContext(source, { window, TextEncoder, Map, Date, Math, JSON, Promise });
  const bridge = window.GalgameBridge.create({ gameId: "test", checkpoint: true });
  const send = (type, payload = {}, session = "session-current") => receive({ data: {
    protocol: "1.0", session_id: session, event_id: "host-" + type, type, payload
  } });
  const init = settings => { send("init", { game_id: "test", inputs: { difficulty: 2 }, settings }); send("start"); };
  return { bridge, posts, send, init, intervals, timeouts };
}

module.exports = { fixture };

test("ready is explicit and finish is unavailable before init/start", async () => {
  const f = fixture();
  assert.equal(f.posts[0].type, "ready");
  assert.equal(f.posts[0].session_id, "");
  await assert.rejects(f.bridge.finish("win", { score: 1 }), /尚未运行/);
  assert.equal(f.posts.length, 1);
});

test("old-session and invalid-game init messages do not mutate active session", () => {
  const f = fixture();
  f.send("init", { game_id: "other" });
  assert.equal(f.bridge.phase, "loading");
  f.init();
  f.send("pause", {}, "session-old");
  assert.equal(f.bridge.phase, "running");
  f.send("pause");
  assert.equal(f.bridge.phase, "paused");
  f.send("resume");
  assert.equal(f.bridge.phase, "running");
});

test("finish retry reuses exact event and ack confirms once", async () => {
  const f = fixture();
  f.init();
  let confirmations = 0;
  f.bridge.on("confirmed", () => confirmations++);
  const promise = f.bridge.finish("win", { score: 42 });
  const first = f.posts.at(-1);
  [...f.intervals.values()][0]();
  assert.deepEqual(f.posts.at(-1), first);
  assert.equal(f.bridge.finish("win", { score: 42 }), promise);
  await assert.rejects(f.bridge.finish("lose", { score: 0 }), /不能改变/);
  f.send("ack", { event_id: first.event_id, accepted: true });
  await promise;
  f.send("ack", { event_id: first.event_id, accepted: true });
  assert.equal(confirmations, 1);
  assert.equal(f.bridge.phase, "confirmed");
  assert.equal(f.intervals.size, 0);
  assert.equal(f.timeouts.size, 0);
});

test("checkpoint snapshots are copied and old ack cannot consume current request", async () => {
  const f = fixture();
  f.init();
  const state = { stage: 1, values: [2] };
  const promise = f.bridge.checkpoint(state, "one");
  const checkpoint = f.posts.at(-1);
  state.values.push(3);
  assert.deepEqual(checkpoint.payload.state.values, [2]);
  f.send("ack", { event_id: checkpoint.event_id, accepted: true }, "session-old");
  assert.equal(f.intervals.size, 1);
  f.send("ack", { event_id: checkpoint.event_id, accepted: true });
  await promise;
  assert.equal(f.bridge.phase, "running");
});

test("reject and timeout leave finished gameplay unavailable", async () => {
  const f = fixture();
  f.init();
  const promise = f.bridge.finish("win", { score: 2 });
  const request = f.posts.at(-1);
  f.send("ack", { event_id: request.event_id, accepted: false, message: "类型不正确" });
  await assert.rejects(promise, /类型不正确/);
  assert.equal(f.bridge.phase, "finishing");
  assert.equal(f.intervals.size, 0);
  const other = fixture();
  other.init();
  const timedOut = other.bridge.finish("lose", { score: 0 });
  [...other.timeouts.values()][0]();
  await assert.rejects(timedOut, /未确认|暂未保存/);
  assert.equal(other.intervals.size, 0);
});

"use strict";
// Actual SDK/page logic with a clock/DOM adapter; no native-window certification.
const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");
process.env.HTML_MINIGAME_TEST_ROOT = path.join(__dirname, "../production/v1.0.1");
const { fixture: sdk } = require("./sdk.test.cjs");
const { fixture: page } = require("./gameplay.test.cjs");

test("initial mute is kept, dynamic gain reaches listeners, and wrong sessions are ignored", () => {
  const f = sdk();
  const changes = [];
  f.bridge.on("settings", settings => changes.push(settings.audio_volume));
  f.init({ audio_volume: 0 });
  assert.equal(f.bridge.audioVolume, 0);
  f.send("settings", { audio_volume: 0.25 });
  assert.equal(f.bridge.audioVolume, 0.25);
  f.send("settings", { audio_volume: 1 }, "session-old");
  assert.equal(f.bridge.audioVolume, 0.25);
  assert.deepEqual(changes, [0, 0.25]);
});

test("settings never coerces invalid values and resumes with the current valid gain", () => {
  const f = sdk();
  f.init({ audio_volume: 0.25 });
  for (const value of [NaN, Infinity, -Infinity, -0.01, 1.01, null, true, "0.7", {}, []]) {
    f.send("settings", { audio_volume: value });
    assert.equal(f.bridge.audioVolume, 0.25);
  }
  f.send("pause");
  f.send("resume", { settings: { audio_volume: 0 } });
  assert.equal(f.bridge.phase, "running");
  assert.equal(f.bridge.audioVolume, 0);
  f.send("pause");
  f.send("resume", { settings: { audio_volume: "1" } });
  assert.equal(f.bridge.audioVolume, 0);
});

test("invalid init settings retain the default and pre-init settings do not mutate it", () => {
  const f = sdk();
  f.send("settings", { audio_volume: 0 });
  assert.equal(f.bridge.audioVolume, 1);
  f.init({ audio_volume: "0" });
  assert.equal(f.bridge.audioVolume, 1);
});

test("timed page updates the actual audio element while playing, paused, and resumed", () => {
  const f = page("timed");
  f.init({}, null, { audio_volume: 0 });
  const tone = f.byId.get("tone");
  assert.equal(tone.volume, 0);
  f.testid("begin").click();
  assert.equal(tone.volume, 0);
  assert.equal(tone.playCount, 1);
  f.send("settings", { audio_volume: 0.25 });
  assert.equal(tone.volume, 0.25);
  assert.equal(tone.playCount, 1);
  f.send("pause");
  f.send("settings", { audio_volume: 0 });
  assert.equal(tone.volume, 0);
  f.send("resume", { settings: { audio_volume: 0.75 } });
  assert.equal(tone.volume, 0.75);
  f.elements.find(element => element.dataset.target !== undefined && element.classList.contains("lit")).click();
  assert.equal(tone.playCount, 2);
  assert.equal(tone.volume, 0.75);
  f.send("settings", { audio_volume: 3 });
  assert.equal(tone.volume, 0.75);
});

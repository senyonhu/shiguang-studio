(function () {
  "use strict";
  const bridge = GalgameBridge.create({ gameId: "prod_checkpoint", packageVersion: "1.0.1", outcomes: ["win"], checkpoint: true });
  const status = document.getElementById("status");
  let state = { stage: 0, sensor: null, wire: null, calibration: 34, mistakes: 0 };
  let target = 37;
  let busy = false;
  let complete = false;

  function render() {
    document.getElementById("workshop").dataset.stage = String(state.stage);
    document.getElementById("stage").textContent = Math.min(3, state.stage + 1) + " / 3";
    document.getElementById("mistakes").textContent = String(state.mistakes);
    document.getElementById("value").textContent = String(state.calibration);
    document.getElementById("target").textContent = String(target);
    ["sensor-step", "wire-step", "calibration-step"].forEach((id, index) => {
      document.getElementById(id).hidden = state.stage !== index || complete;
    });
    for (const button of document.querySelectorAll("button")) {
      button.disabled = busy || complete || bridge.phase !== "running";
    }
  }
  function wrong() { state.mistakes += 1; status.textContent = "与检修说明不符，请检查后再试。"; render(); }
  async function checkpoint() {
    busy = true;
    status.textContent = "这一步已完成，正在保存进度……";
    render();
    try {
      await bridge.checkpoint(state, "assembly_step_" + state.stage);
      busy = false;
      status.textContent = "继续下一道工序。";
      render();
    } catch (error) { status.textContent = error.message; }
  }
  function selectSensor(value) {
    if (state.stage !== 0 || busy || bridge.phase !== "running") return;
    if (value !== "temperature") { wrong(); return; }
    state.sensor = value;
    state.stage = 1;
    checkpoint();
  }
  function selectWire(value) {
    if (state.stage !== 1 || busy || bridge.phase !== "running") return;
    if (value !== "blue") { wrong(); return; }
    state.wire = value;
    state.stage = 2;
    checkpoint();
  }
  function adjust(change) {
    if (state.stage !== 2 || busy || complete || bridge.phase !== "running") return;
    state.calibration = Math.max(0, Math.min(99, state.calibration + change));
    render();
  }
  function verify() {
    if (state.stage !== 2 || busy || complete || bridge.phase !== "running") return;
    if (state.calibration !== target || state.sensor !== "temperature" || state.wire !== "blue") { wrong(); return; }
    complete = true;
    status.classList.add("success");
    status.textContent = "探头读数稳定，温室已恢复！正在确认结果……";
    render();
    bridge.finish("win", { score: Math.max(0, 300 - state.mistakes * 25),
      steps_completed: 3, mistakes: state.mistakes, calibrated_temperature: target })
      .catch(error => { status.textContent = error.message; });
  }
  function validSnapshot(value) {
    return value && Number.isInteger(value.stage) && value.stage >= 0 && value.stage <= 2
      && Number.isInteger(value.calibration) && value.calibration >= 0 && value.calibration <= 99
      && Number.isInteger(value.mistakes) && value.mistakes >= 0 && value.mistakes <= 100000
      && (value.stage === 0 ? value.sensor === null : value.sensor === "temperature")
      && (value.stage < 2 ? value.wire === null : value.wire === "blue");
  }
  bridge.on("init", payload => {
    target = Number.isInteger(payload.inputs.target_temperature) ? Math.max(20, Math.min(50, payload.inputs.target_temperature)) : 37;
    state.calibration = target - 3;
    if (payload.snapshot) {
      const snapshot = payload.snapshot;
      const value = snapshot.state || snapshot;
      if (snapshot.snapshot_version && snapshot.snapshot_version !== "1.0" || !validSnapshot(value)) {
        busy = true;
        status.textContent = "这份进度无法恢复，请退出后重新进入。";
        bridge.reportError("SNAPSHOT_INVALID", "checkpoint snapshot does not match 1.0.0 / 1.0");
      } else state = { stage: value.stage, sensor: value.sensor, wire: value.wire,
        calibration: value.calibration, mistakes: value.mistakes };
    }
    render();
  }).on("start", () => { if (!busy) status.textContent = state.stage ? "已恢复完成的工序，请继续修复。" : "请按检修说明选择探头。"; render(); })
    .on("pause", () => { status.textContent = "已暂停。"; render(); })
    .on("resume", () => { status.textContent = "已继续，请完成当前工序。"; render(); })
    .on("confirmed", () => { status.textContent = "温室重新苏醒，即将继续剧情。"; });
  document.querySelectorAll("[data-part]").forEach(button => button.addEventListener("click", () => selectSensor(button.dataset.part)));
  document.querySelectorAll("[data-wire]").forEach(button => button.addEventListener("click", () => selectWire(button.dataset.wire)));
  document.getElementById("minus").addEventListener("click", () => adjust(-1));
  document.getElementById("plus").addEventListener("click", () => adjust(1));
  document.getElementById("verify").addEventListener("click", verify);
  document.addEventListener("keydown", event => {
    if (/^[1-3]$/.test(event.key)) {
      if (state.stage === 0) selectSensor(["humidity", "temperature", "light"][Number(event.key) - 1]);
      else if (state.stage === 1) selectWire(["red", "blue", "yellow"][Number(event.key) - 1]);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") { event.preventDefault(); adjust(-1); }
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") { event.preventDefault(); adjust(1); }
    else if (event.key === "Enter") { event.preventDefault(); verify(); }
  });
  fetch("instructions.txt").then(response => {
    if (!response.ok) throw new Error("本地操作单不可读");
    return response.text();
  }).then(text => { document.getElementById("package-instructions").textContent = text; })
    .catch(error => bridge.reportError("LOCAL_INSTRUCTIONS_MISSING", error.message));
  document.getElementById("preview").hidden = !bridge.standalone;
  render();
})();

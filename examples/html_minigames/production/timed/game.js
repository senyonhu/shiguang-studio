(function () {
  "use strict";
  const bridge = GalgameBridge.create({ gameId: "prod_timed", packageVersion: "1.0.0", outcomes: ["win", "lose"] });
  const buttons = [...document.querySelectorAll("[data-target]")];
  const begin = document.getElementById("begin");
  const status = document.getElementById("status");
  const tone = document.getElementById("tone");
  const pattern = [0, 2, 1, 3, 0, 1, 2, 3];
  let duration = 12000;
  let remaining = duration;
  let required = 6;
  let hits = 0;
  let misses = 0;
  let tick = performance.now();
  let begun = false;
  let complete = false;

  function render() {
    document.getElementById("remaining").textContent = (Math.max(0, remaining) / 1000).toFixed(1);
    document.getElementById("progress").textContent = hits + " / " + required;
    document.getElementById("misses").textContent = String(misses);
    begin.disabled = bridge.phase !== "running" || begun;
    buttons.forEach((button, index) => {
      button.disabled = bridge.phase !== "running" || !begun || complete;
      button.classList.toggle("lit", begun && !complete && index === pattern[hits % pattern.length]);
    });
  }
  function playTone() {
    tone.volume = bridge.audioVolume;
    tone.currentTime = 0;
    tone.play().catch(() => { /* Gameplay remains available when the device has no audio output. */ });
  }
  function finish(outcome) {
    complete = true;
    tone.pause();
    status.classList.add(outcome === "win" ? "success" : "failure");
    status.textContent = outcome === "win" ? "信号已经接通，正在确认结果……" : "时间已到，信号中断。正在确认结果……";
    render();
    bridge.finish(outcome, { score: Math.max(0, hits * 100 - misses * 20), hits, misses,
      elapsed_ms: Math.round(duration - Math.max(0, remaining)) }).catch(error => { status.textContent = error.message; });
  }
  function clock() {
    const now = performance.now();
    if (begun && !complete && bridge.phase === "running") {
      remaining -= now - tick;
      if (remaining <= 0) finish("lose");
    }
    tick = now;
    render();
  }
  function startPlay() {
    if (begun || bridge.phase !== "running") return;
    begun = true;
    tick = performance.now();
    playTone();
    status.textContent = "按下发亮端子，接通所有信号。";
    render();
  }
  function select(index) {
    if (!begun || complete || bridge.phase !== "running") return;
    clock();
    if (complete) return;
    if (index === pattern[hits % pattern.length]) {
      hits += 1;
      playTone();
      status.textContent = "接线正确！继续寻找下一个发亮端子。";
      if (hits >= required) finish("win");
    } else {
      misses += 1;
      remaining -= 500;
      status.textContent = "按错端子，扣除 0.5 秒。";
      if (remaining <= 0) finish("lose");
    }
    render();
  }
  bridge.on("init", payload => {
    duration = Number.isFinite(payload.inputs.duration_seconds) ? Math.max(3, Math.min(60, payload.inputs.duration_seconds)) * 1000 : 12000;
    required = Number.isInteger(payload.inputs.rounds) ? Math.max(3, Math.min(24, payload.inputs.rounds)) : 6;
    remaining = duration;
    render();
  }).on("start", () => { tick = performance.now(); status.textContent = "准备好后，按开始接线。"; render(); })
    .on("pause", () => { tick = performance.now(); tone.pause(); status.textContent = "已暂停，倒计时停止。"; render(); })
    .on("resume", () => { tick = performance.now(); status.textContent = begun ? "已继续，请按发亮端子。" : "已继续，可以开始接线。"; render(); })
    .on("confirmed", payload => { status.textContent = payload.outcome === "win" ? "信号接通！即将继续剧情。" : "信号中断，即将返回剧情。"; });
  begin.addEventListener("click", startPlay);
  buttons.forEach((button, index) => button.addEventListener("click", () => select(index)));
  document.addEventListener("keydown", event => {
    if (event.key === "Enter" && !begun) { event.preventDefault(); startPlay(); }
    else if (/^[1-4]$/.test(event.key)) { event.preventDefault(); select(Number(event.key) - 1); }
  });
  fetch("instructions.txt").then(response => {
    if (!response.ok) throw new Error("本地操作单不可读");
    return response.text();
  }).then(text => { document.getElementById("package-instructions").textContent = text; })
    .catch(error => bridge.reportError("LOCAL_INSTRUCTIONS_MISSING", error.message));
  document.getElementById("preview").hidden = !bridge.standalone;
  render();
  setInterval(clock, 50);
})();

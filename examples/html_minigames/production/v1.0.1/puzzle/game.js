(function () {
  "use strict";
  const bridge = GalgameBridge.create({ gameId: "prod_puzzle", packageVersion: "1.0.1", outcomes: ["win"] });
  const boardElement = document.getElementById("board");
  const status = document.getElementById("status");
  let board = [1, 2, 3, 4, 5, 6, 7, 8, 0];
  let moves = 0;
  let elapsedMs = 0;
  let tick = performance.now();
  let complete = false;

  function adjacent(empty) {
    return [empty - 3, empty + 3, empty % 3 ? empty - 1 : -1,
      empty % 3 < 2 ? empty + 1 : -1].filter(index => index >= 0 && index < 9);
  }
  function solved() { return board.every((value, index) => value === (index + 1) % 9); }
  function render() {
    boardElement.replaceChildren();
    boardElement.dataset.order = board.join(",");
    const canMove = adjacent(board.indexOf(0));
    board.forEach((value, index) => {
      const tile = document.createElement(value ? "button" : "div");
      tile.className = value ? "tile" : "blank";
      tile.dataset.slot = String(index);
      if (value) {
        tile.textContent = String(value);
        tile.dataset.tile = String(value);
        tile.dataset.testid = "tile-" + value;
        tile.setAttribute("aria-label", "移动数字 " + value);
        tile.disabled = bridge.phase !== "running" || complete || !canMove.includes(index);
        tile.addEventListener("click", () => move(index));
      } else tile.setAttribute("aria-label", "空格");
      boardElement.append(tile);
    });
    document.getElementById("moves").textContent = String(moves);
  }
  function advanceClock() {
    const now = performance.now();
    if (bridge.phase === "running" && !complete) elapsedMs += now - tick;
    tick = now;
    document.getElementById("elapsed").textContent = (elapsedMs / 1000).toFixed(1);
  }
  function move(index) {
    if (bridge.phase !== "running" || complete) return;
    const empty = board.indexOf(0);
    if (!adjacent(empty).includes(index)) return;
    [board[empty], board[index]] = [board[index], board[empty]];
    moves += 1;
    advanceClock();
    if (solved()) {
      complete = true;
      status.classList.add("success");
      status.textContent = "星图已归位！正在确认开锁结果……";
      render();
      bridge.finish("win", { score: Math.max(0, 1000 - moves * 10), moves,
        elapsed_ms: Math.round(elapsedMs) }).catch(error => { status.textContent = error.message; });
    } else {
      status.textContent = "继续移动空格旁的数字，把星图排好。";
      render();
    }
  }

  bridge.on("init", payload => {
    const inputs = payload.inputs;
    let seed = Number.isInteger(inputs.seed) ? inputs.seed >>> 0 : 1742;
    const count = Number.isInteger(inputs.scramble_moves) ? Math.max(2, Math.min(80, inputs.scramble_moves)) : 12;
    let previousEmpty = -1;
    for (let step = 0; step < count; step++) {
      const empty = board.indexOf(0);
      const choices = adjacent(empty).filter(index => index !== previousEmpty);
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      const selected = choices[seed % choices.length];
      [board[empty], board[selected]] = [board[selected], board[empty]];
      previousEmpty = empty;
    }
    if (solved()) [board[7], board[8]] = [board[8], board[7]];
    render();
  }).on("start", () => { tick = performance.now(); status.textContent = "移动空格旁的数字，开始解谜。"; render(); })
    .on("pause", () => { tick = performance.now(); status.textContent = "已暂停。"; render(); })
    .on("resume", () => { tick = performance.now(); status.textContent = "已继续。"; render(); })
    .on("confirmed", () => { status.textContent = bridge.standalone ? "星图锁已打开！试玩完成。" : "星图锁已打开，即将继续剧情。"; });
  document.addEventListener("keydown", event => {
    const empty = board.indexOf(0);
    const next = { ArrowUp: empty - 3, ArrowDown: empty + 3,
      ArrowLeft: empty % 3 ? empty - 1 : -1, ArrowRight: empty % 3 < 2 ? empty + 1 : -1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    move(next);
  });
  fetch("instructions.txt").then(response => {
    if (!response.ok) throw new Error("本地操作单不可读");
    return response.text();
  }).then(text => { document.getElementById("package-instructions").textContent = text; })
    .catch(error => bridge.reportError("LOCAL_INSTRUCTIONS_MISSING", error.message));
  document.getElementById("preview").hidden = !bridge.standalone;
  render();
  setInterval(advanceClock, 100);
})();

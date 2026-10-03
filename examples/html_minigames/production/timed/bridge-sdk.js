(function (global) {
  "use strict";

  const PROTOCOL = "1.0";
  const MAX_MESSAGE_BYTES = 262144;

  function cloneJson(value) {
    const text = JSON.stringify(value);
    if (typeof text !== "string" || new TextEncoder().encode(text).length > MAX_MESSAGE_BYTES) {
      throw new Error("消息不是有效 JSON，或超过大小限制。");
    }
    return JSON.parse(text);
  }

  function create(options) {
    if (!options || typeof options.gameId !== "string") throw new Error("缺少 gameId。");
    const webview = global.chrome && global.chrome.webview;
    const standalone = !webview;
    const handlers = new Map();
    const pending = new Map();
    const prefix = "page_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 10);
    let sequence = 0;
    let sessionId = "";
    let phase = "loading";
    let initialized = false;
    let finished = null;
    let audioVolume = 1;

    function emit(name, payload) {
      for (const callback of handlers.get(name) || []) {
        try { callback(payload); }
        catch (error) { if (name !== "error") reportError("HANDLER_ERROR", error.message || String(error)); }
      }
    }

    function post(message) {
      if (webview) webview.postMessage(message);
      else emit("preview-event", message);
    }

    function envelope(type, payload) {
      return cloneJson({ protocol: PROTOCOL, session_id: sessionId,
        event_id: prefix + "_" + (++sequence), type, payload });
    }

    function acknowledged(type, payload) {
      if (!initialized || !sessionId) return Promise.reject(new Error("小游戏还未准备好。"));
      const message = envelope(type, payload);
      if (standalone) {
        post(message);
        return Promise.resolve({ accepted: true, preview: true, event_id: message.event_id });
      }
      if (pending.size >= 8) return Promise.reject(new Error("等待确认的事件过多。"));
      const retryMilliseconds = options.retryMilliseconds || 1000;
      const timeoutMilliseconds = options.timeoutMilliseconds || 30000;
      return new Promise((resolve, reject) => {
        const entry = { message, resolve, reject, retry: null, timeout: null };
        pending.set(message.event_id, entry);
        entry.retry = global.setInterval(() => post(message), retryMilliseconds);
        entry.timeout = global.setTimeout(() => {
          global.clearInterval(entry.retry);
          pending.delete(message.event_id);
          reject(new Error("结果暂未保存，请退出后重新进入。"));
        }, timeoutMilliseconds);
        post(message);
      });
    }

    function receive(raw) {
      let message;
      try { message = typeof raw === "string" ? JSON.parse(raw) : cloneJson(raw); }
      catch (_) { return; }
      if (!message || message.protocol !== PROTOCOL || typeof message.type !== "string") return;
      if (message.type === "init") {
        if (initialized || typeof message.session_id !== "string" || !message.session_id) return;
        const payload = message.payload || {};
        if (payload.game_id && payload.game_id !== options.gameId) return;
        sessionId = message.session_id;
        initialized = true;
        phase = "ready";
        audioVolume = Math.max(0, Math.min(1, Number(payload.settings && payload.settings.audio_volume) || 0));
        if (!payload.settings || payload.settings.audio_volume === undefined) audioVolume = 1;
        emit("init", { ...payload, inputs: payload.inputs || {},
          snapshot: payload.snapshot || payload.resume_state || null });
        return;
      }
      if (!initialized || message.session_id !== sessionId) return;
      if (message.type === "ack") {
        const payload = message.payload || {};
        const requestId = payload.event_id || payload.request_id || message.event_id;
        const entry = pending.get(requestId);
        if (!entry) return;
        global.clearInterval(entry.retry);
        global.clearTimeout(entry.timeout);
        pending.delete(requestId);
        if (payload.accepted === false) entry.reject(new Error(payload.message || "这次操作暂时无法完成。"));
        else entry.resolve(payload);
        return;
      }
      if (message.type === "start" && phase === "ready") {
        phase = "running";
        emit("start", message.payload || {});
      } else if (message.type === "pause" && phase === "running") {
        phase = "paused";
        emit("pause", message.payload || {});
      } else if (message.type === "resume" && phase === "paused") {
        phase = "running";
        emit("resume", message.payload || {});
      }
    }

    function reportError(code, detail) {
      const message = envelope("error", { code: String(code).slice(0, 80),
        message: String(detail).slice(0, 1000) });
      post(message);
      emit("error", message.payload);
    }

    const api = {
      on(name, callback) {
        if (!handlers.has(name)) handlers.set(name, []);
        handlers.get(name).push(callback);
        return api;
      },
      checkpoint(state, checkpointId) {
        if (phase !== "running" && phase !== "paused") return Promise.reject(new Error("当前不能保存检查点。"));
        return acknowledged("checkpoint", { checkpoint_id: checkpointId || "step_" + sequence,
          snapshot_version: "1.0", state: cloneJson(state) });
      },
      finish(outcome, results) {
        if (!(options.outcomes || ["win", "lose"]).includes(outcome)) {
          return Promise.reject(new Error("结果不在声明的枚举内。"));
        }
        const payload = cloneJson({ outcome, results: results || {} });
        const signature = JSON.stringify(payload);
        if (finished) {
          if (signature !== finished.signature) return Promise.reject(new Error("完成后不能改变结果。"));
          return finished.promise;
        }
        if (phase !== "running") return Promise.reject(new Error("小游戏尚未运行，或已经暂停。"));
        phase = "finishing";
        const promise = acknowledged("finish", payload).then(ack => {
          phase = "confirmed";
          emit("confirmed", { outcome, results: payload.results, ack });
          return ack;
        });
        finished = { signature, promise };
        return promise;
      },
      reportError,
      get standalone() { return standalone; },
      get phase() { return phase; },
      get audioVolume() { return audioVolume; }
    };

    if (webview) webview.addEventListener("message", event => receive(event.data));
    global.addEventListener("error", event => reportError("PAGE_ERROR", event.message || "页面脚本错误"));
    global.addEventListener("unhandledrejection", event => reportError("UNHANDLED_REJECTION",
      event.reason && event.reason.message || String(event.reason)));
    post(envelope("ready", { game_id: options.gameId, package_version: options.packageVersion || "1.0.0",
      capabilities: { pause: true, checkpoint: options.checkpoint === true } }));
    if (standalone) global.setTimeout(() => {
      receive({ protocol: PROTOCOL, session_id: "standalone-preview", type: "init",
        payload: { game_id: options.gameId, inputs: {}, snapshot: null } });
      receive({ protocol: PROTOCOL, session_id: "standalone-preview", type: "start", payload: {} });
    }, 0);
    return api;
  }

  global.GalgameBridge = Object.freeze({ protocol: PROTOCOL, create });
})(window);

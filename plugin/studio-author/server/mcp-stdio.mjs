// 由 npm run build:plugin 生成：请勿直接编辑；源码在 server/src/**。

// server/src/mcp-stdio.ts
import { createInterface } from "node:readline";
import { existsSync as existsSync2 } from "node:fs";
import path2 from "node:path";
import { fileURLToPath as fileURLToPath2 } from "node:url";

// server/src/core/errors.ts
var StudioError = class extends Error {
  code;
  issues;
  details;
  constructor(code, message, options) {
    super(message);
    this.name = "StudioError";
    this.code = code;
    this.issues = options?.issues ?? [];
    this.details = options?.details ?? {};
  }
  toBody() {
    return { error: { code: this.code, message: this.message, issues: this.issues, details: this.details } };
  }
};

// server/src/lifecycle.ts
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

// server/src/version.ts
var SERVICE_VERSION = "0.1.1";
var MCP_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"];

// server/src/store/paths.ts
import path from "node:path";
function workspacePaths(root) {
  const serviceDir = path.join(root, ".studio-service");
  const credentialsDir = path.join(serviceDir, "credentials");
  return {
    root,
    projectsJson: path.join(root, "projects.json"),
    serviceDir,
    runtimeJson: path.join(serviceDir, "runtime.json"),
    credentialsDir,
    pairingKeyFile: path.join(credentialsDir, "pairing.key"),
    pairingMetaFile: path.join(credentialsDir, "pairing.json"),
    sessionsFile: path.join(credentialsDir, "sessions.json"),
    lockFile: path.join(serviceDir, "service.lock"),
    logsDir: path.join(serviceDir, "logs")
  };
}

// server/src/store/fsread.ts
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
function readJson(abs) {
  return JSON.parse(readFileSync(abs, "utf8"));
}
function readJsonIfExists(abs) {
  if (!existsSync(abs)) return null;
  try {
    return readJson(abs);
  } catch {
    return null;
  }
}

// server/src/lifecycle.ts
function readDiscovery(workspaceRoot) {
  return readJsonIfExists(workspacePaths(workspaceRoot).runtimeJson);
}
function isProcessAlive(pid) {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}
async function probeHealth(url, timeoutMs = 2e3) {
  try {
    const response = await fetch(`${url}/v1/health`, { signal: AbortSignal.timeout(timeoutMs) });
    const body = await response.json();
    return { ok: response.ok, body };
  } catch {
    return { ok: false, body: null };
  }
}
async function ensureService(options) {
  const waitMs = options.waitMs ?? 2e4;
  const existing = readDiscovery(options.workspaceRoot);
  if (existing) {
    const healthy = isProcessAlive(existing.pid) ? await probeHealth(existing.url) : { ok: false, body: null };
    if (healthy.ok && healthy.body?.instanceId === existing.instanceId) return { record: existing, startedByClient: false };
  }
  if (!options.autostart) {
    throw new StudioError("BACKEND_UNAVAILABLE", `\u540E\u53F0\u672A\u8FD0\u884C\u4E14\u672A\u542F\u7528\u81EA\u52A8\u542F\u52A8\uFF08\u5DE5\u4F5C\u533A ${options.workspaceRoot}\uFF09`, {
      details: { workspace: options.workspaceRoot, hint: "\u5148\u8FD0\u884C npm run studio:server \u542F\u52A8\u540E\u53F0\uFF0C\u6216\u8BBE\u7F6E STUDIO_AUTOSTART=1" }
    });
  }
  const entry = options.serviceEntry ?? fileURLToPath(new URL("./main.ts", import.meta.url));
  const child = spawn(process.execPath, [entry, "--workspace", options.workspaceRoot], {
    detached: true,
    stdio: "ignore",
    windowsHide: true
  });
  child.unref();
  const deadline = Date.now() + waitMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 200));
    const record = readDiscovery(options.workspaceRoot);
    if (!record) continue;
    const healthy = await probeHealth(record.url);
    if (healthy.ok && healthy.body?.instanceId === record.instanceId) return { record, startedByClient: true };
  }
  throw new StudioError("BACKEND_UNAVAILABLE", `\u5C1D\u8BD5\u542F\u52A8\u540E\u53F0\u5931\u8D25\uFF08${waitMs} \u6BEB\u79D2\u5185\u6CA1\u6709\u5065\u5EB7\u54CD\u5E94\uFF09`, {
    details: { workspace: options.workspaceRoot, entry }
  });
}
function defaultWorkspaceRoot() {
  const fromEnv = process.env.STUDIO_WORKSPACE;
  if (fromEnv && fromEnv.trim()) return fromEnv.trim();
  return fileURLToPath(new URL("../../.studio-workspace", import.meta.url));
}

// server/src/tools/http-client.ts
var HttpApiClient = class {
  baseUrl;
  token;
  onUnauthenticated;
  constructor(baseUrl, token, onUnauthenticated) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.token = token;
    this.onUnauthenticated = onUnauthenticated;
  }
  setToken(token) {
    this.token = token;
  }
  async request(method, path3, body) {
    const send = async () => fetch(`${this.baseUrl}${path3}`, {
      method,
      headers: {
        authorization: `Bearer ${this.token}`,
        "content-type": "application/json"
      },
      body: body === void 0 ? void 0 : JSON.stringify(body),
      signal: AbortSignal.timeout(3e4)
    });
    let response = await send();
    if (response.status === 401 && this.onUnauthenticated) {
      this.token = await this.onUnauthenticated();
      response = await send();
    }
    const text2 = await response.text();
    let parsed = null;
    try {
      parsed = text2 ? JSON.parse(text2) : null;
    } catch {
      parsed = { error: { code: "INVALID_RESPONSE", message: text2.slice(0, 200) } };
    }
    return { status: response.status, body: parsed };
  }
};

// server/src/core/canonical.ts
import { createHash } from "node:crypto";
function sha256Hex(input) {
  return createHash("sha256").update(input).digest("hex");
}
function canonicalize(value) {
  return JSON.stringify(normalize(value));
}
function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      const v = value[key];
      if (v === void 0) continue;
      out[key] = normalize(v);
    }
    return out;
  }
  return value;
}
function contentHash(value) {
  return sha256Hex(canonicalize(value));
}

// src/runtime/events.ts
var text = (key, label, extra = {}) => ({ key, label, type: "text", ...extra });
var str = (v) => typeof v === "string" ? v : "";
var EVENT_SPECS = {
  background: {
    type: "background",
    label: "\u80CC\u666F",
    since: 1,
    runtimeSupport: "implemented",
    summary: (p) => `\u80CC\u666F \u2192 ${str(p.assetId)}`,
    params: [{ key: "assetId", label: "\u80CC\u666F\u7D20\u6750", type: "assetRef", required: true, assetKinds: ["background", "cg"] }]
  },
  character: {
    type: "character",
    label: "\u4EBA\u7269\u6F14\u51FA",
    since: 1,
    runtimeSupport: "implemented",
    summary: (p) => `\u4EBA\u7269 ${str(p.assetId)} ${str(p.action) === "exit" ? "\u9000\u573A" : str(p.action) === "move" ? "\u79FB\u52A8" : "\u5165\u573A"}\uFF08${str(p.position)}\uFF09`,
    params: [
      { key: "assetId", label: "\u4EBA\u7269\u7ACB\u7ED8", type: "assetRef", required: true, assetKinds: ["character"] },
      { key: "action", label: "\u52A8\u4F5C", type: "enum", required: true, enumValues: ["enter", "exit", "move"], default: "enter" },
      { key: "position", label: "\u4F4D\u7F6E", type: "enum", required: true, enumValues: ["left", "center", "right"], default: "center" },
      { key: "durationMs", label: "\u65F6\u957F(\u6BEB\u79D2)", type: "number", min: 0, max: 1e4, default: 300, optional: true }
    ]
  },
  dialogue: {
    type: "dialogue",
    label: "\u5BF9\u767D",
    since: 1,
    runtimeSupport: "implemented",
    waits: true,
    summary: (p) => `${str(p.speaker) || "\u65C1\u767D"}\uFF1A${str(p.text).slice(0, 40)}`,
    params: [
      text("speaker", "\u8BF4\u8BDD\u4EBA", { maxLength: 40, optional: true }),
      { key: "text", label: "\u53F0\u8BCD", type: "longText", required: true, maxLength: 1200 },
      { key: "characterId", label: "\u8BF4\u8BDD\u89D2\u8272\u7ACB\u7ED8", type: "assetRef", assetKinds: ["character"], optional: true }
    ]
  },
  choice: {
    type: "choice",
    label: "\u9009\u62E9",
    since: 1,
    runtimeSupport: "implemented",
    waits: true,
    summary: (p) => `\u9009\u62E9\uFF08${Array.isArray(p.options) ? p.options.length : 0} \u9879\uFF09`,
    params: [
      text("prompt", "\u63D0\u793A", { maxLength: 200, optional: true }),
      {
        key: "options",
        label: "\u9009\u9879",
        type: "list",
        required: true,
        itemFields: [
          text("label", "\u9009\u9879\u6587\u5B57", { required: true, maxLength: 120 }),
          text("targetEventId", "\u76EE\u6807\u4E8B\u4EF6 ID", { optional: true }),
          text("targetSceneId", "\u76EE\u6807\u573A\u666F ID", { optional: true })
        ]
      }
    ]
  },
  variable: {
    type: "variable",
    label: "\u53D8\u91CF\u66F4\u65B0",
    since: 1,
    runtimeSupport: "implemented",
    summary: (p) => `\u53D8\u91CF ${str(p.variableId)} ${str(p.op) === "add" ? "+=" : "="} ${JSON.stringify(p.value ?? null)}`,
    params: [
      { key: "variableId", label: "\u53D8\u91CF", type: "variableRef", required: true },
      { key: "op", label: "\u64CD\u4F5C", type: "enum", required: true, enumValues: ["set", "add"], default: "set" },
      { key: "value", label: "\u503C", type: "text", required: true, maxLength: 200 }
    ]
  },
  audio: {
    type: "audio",
    label: "\u97F3\u9891",
    since: 1,
    runtimeSupport: "host_required",
    hostCapability: "audio",
    summary: (p) => `${str(p.action) === "stop" ? "\u505C\u6B62" : str(p.action) === "fade" ? "\u6DE1\u51FA" : "\u64AD\u653E"} ${str(p.channel)} ${str(p.assetId)}`,
    params: [
      { key: "assetId", label: "\u97F3\u9891\u7D20\u6750", type: "assetRef", required: true, assetKinds: ["audio"] },
      { key: "channel", label: "\u58F0\u9053", type: "enum", required: true, enumValues: ["bgm", "ambience", "sefx", "voice"], default: "bgm" },
      { key: "action", label: "\u52A8\u4F5C", type: "enum", required: true, enumValues: ["play", "stop", "fade"], default: "play" },
      { key: "loop", label: "\u5FAA\u73AF", type: "boolean", default: true, optional: true },
      { key: "volume", label: "\u97F3\u91CF", type: "number", min: 0, max: 1, default: 1, optional: true },
      { key: "waitForEnd", label: "\u7B49\u5F85\u64AD\u5B8C", type: "boolean", default: false, optional: true }
    ]
  },
  video: {
    type: "video",
    label: "\u89C6\u9891",
    since: 1,
    runtimeSupport: "host_required",
    hostCapability: "video",
    waits: true,
    summary: (p) => `\u89C6\u9891 ${str(p.assetId)}\uFF08${str(p.onEnd) === "stop" ? "\u7ED3\u675F\u5267\u60C5" : "\u7EE7\u7EED"}${p.skippable === false ? "\uFF0C\u4E0D\u53EF\u8DF3\u8FC7" : "\uFF0C\u53EF\u8DF3\u8FC7"}\uFF09`,
    params: [
      { key: "assetId", label: "\u89C6\u9891\u7D20\u6750", type: "assetRef", required: true, assetKinds: ["video"] },
      { key: "skippable", label: "\u5141\u8BB8\u8DF3\u8FC7", type: "boolean", default: true, optional: true },
      { key: "onEnd", label: "\u64AD\u653E\u7ED3\u675F\u540E", type: "enum", required: true, enumValues: ["continue", "stop"], default: "continue" },
      { key: "onError", label: "\u5931\u8D25\u540E", type: "enum", required: true, enumValues: ["continue", "stop", "replay"], default: "continue" }
    ]
  },
  wait: {
    type: "wait",
    label: "\u7B49\u5F85",
    since: 1,
    runtimeSupport: "implemented",
    waits: true,
    summary: (p) => `\u7B49\u5F85 ${Number(p.ms ?? 0)} \u6BEB\u79D2`,
    params: [{ key: "ms", label: "\u6BEB\u79D2", type: "number", required: true, min: 0, max: 6e5, default: 500 }]
  },
  level: {
    type: "level",
    label: "HTML \u5173\u5361",
    since: 1,
    runtimeSupport: "host_required",
    hostCapability: "level",
    waits: true,
    summary: (p) => `\u5173\u5361 ${str(p.levelId)}\uFF08${p.supportsSnapshot === false ? "\u8BFB\u6863\u91CD\u5F00" : "\u652F\u6301\u5FEB\u7167"}\uFF09`,
    params: [
      { key: "levelId", label: "\u5173\u5361", type: "levelRef", required: true },
      { key: "supportsSnapshot", label: "\u652F\u6301\u5FEB\u7167", type: "boolean", default: true, optional: true },
      text("initPayload", "\u521D\u59CB\u53C2\u6570", { optional: true, maxLength: 2e3 })
    ]
  },
  jump: {
    type: "jump",
    label: "\u8DF3\u8F6C",
    since: 1,
    runtimeSupport: "implemented",
    summary: (p) => `\u8DF3\u8F6C \u2192 ${str(p.targetSceneId) || str(p.targetEventId) || "(\u672A\u8BBE\u7F6E)"}`,
    params: [
      text("targetSceneId", "\u76EE\u6807\u573A\u666F", { optional: true }),
      text("targetEventId", "\u76EE\u6807\u4E8B\u4EF6", { optional: true })
    ]
  },
  ending: {
    type: "ending",
    label: "\u7ED3\u675F",
    since: 1,
    runtimeSupport: "implemented",
    waits: true,
    summary: (p) => `\u7ED3\u675F\uFF1A${str(p.title) || str(p.endingId)}`,
    params: [
      { key: "endingId", label: "\u7ED3\u5C40 ID", type: "text", required: true, maxLength: 64 },
      text("title", "\u7ED3\u5C40\u6807\u9898", { maxLength: 120, optional: true })
    ]
  }
};
var EVENT_TYPES = Object.keys(EVENT_SPECS);

// server/src/tools/registry.ts
var str2 = (value) => typeof value === "string" ? value : "";
var obj = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
function schema(properties, required = []) {
  return { type: "object", properties, required, additionalProperties: false };
}
var S = {
  projectId: { type: "string", description: "\u5DE5\u7A0B ID\uFF08prj_xxx\uFF09\uFF0C\u7531 studio_list_projects \u6216 studio_open_project \u83B7\u5F97" },
  baseRevision: { type: "string", description: "\u4E8B\u52A1\u57FA\u7840\u4FEE\u8BA2\u53F7\uFF1B\u7701\u7565\u65F6\u5DE5\u5177\u4F1A\u5148\u8BFB\u53D6\u5F53\u524D\u4FEE\u8BA2" },
  requestId: { type: "string", description: "\u8BF7\u6C42 ID\uFF1B\u91CD\u8BD5\u65F6\u590D\u7528\u540C\u4E00 ID \u4E0D\u4F1A\u91CD\u590D\u521B\u5EFA\uFF0C\u53EF\u7701\u7565\uFF08\u7701\u7565\u65F6\u6309\u5DE5\u5177\u540D+\u53C2\u6570\u751F\u6210\u7A33\u5B9A ID\uFF09" },
  sceneId: { type: "string", description: "\u573A\u666F ID\uFF08sc_xxx\uFF09" },
  eventId: { type: "string", description: "\u4E8B\u4EF6 ID\uFF08ev_xxx\uFF09" },
  title: { type: "string", description: "\u6807\u9898\uFF081\u2013120 \u5B57\uFF09" }
};
function errorResult(tool, error) {
  return { ok: false, tool, error };
}
function fromHttp(tool, response) {
  const body = obj(response.body);
  if (response.status >= 200 && response.status < 300) return { ok: true, tool, data: body };
  const error = obj(body.error);
  return errorResult(tool, {
    code: str2(error.code) || `HTTP_${response.status}`,
    message: str2(error.message) || `\u8BF7\u6C42\u5931\u8D25\uFF08HTTP ${response.status}\uFF09`,
    issues: Array.isArray(error.issues) ? error.issues : [],
    details: obj(error.details)
  });
}
function transactionResult(tool, body) {
  const status = str2(body.status);
  if (status === "applied") {
    return {
      ok: true,
      tool,
      status,
      data: {
        requestId: body.requestId,
        projectId: body.projectId,
        revision: body.revision,
        persistedRevision: body.persistedRevision,
        changedIds: body.changedIds,
        undoId: body.undoId,
        transactionId: body.transactionId,
        sequence: body.sequence,
        summary: body.summary,
        persistence: body.persistence,
        replayed: body.replayed === true
      },
      notes: body.replayed === true ? ["\u8BE5 requestId \u5DF2\u63D0\u4EA4\u8FC7\uFF0C\u8FD4\u56DE\u65E2\u6709\u7ED3\u679C\uFF08\u672A\u91CD\u590D\u521B\u5EFA\uFF09"] : void 0
    };
  }
  return {
    ok: false,
    tool,
    status,
    data: { revision: body.revision, changedIds: body.changedIds ?? [] },
    error: {
      code: str2(body.errorCode) || (status === "conflict" ? "REVISION_CONFLICT" : "VALIDATION_FAILED"),
      message: status === "conflict" ? "\u57FA\u7840\u7248\u672C\u5DF2\u8FC7\u671F\u6216\u5BF9\u8C61\u7248\u672C\u51B2\u7A81\uFF0C\u8BF7\u91CD\u65B0\u8BFB\u53D6\u540E\u91CD\u8BD5" : "\u4E8B\u52A1\u88AB\u62D2\u7EDD\uFF0C\u672A\u5199\u5165\u4EFB\u4F55\u5185\u5BB9",
      issues: Array.isArray(body.issues) ? body.issues : []
    }
  };
}
async function currentRevision(ctx, projectId) {
  const response = await ctx.client.request("GET", `/v1/projects/${projectId}`);
  const body = obj(response.body);
  if (response.status !== 200) throw new Error(str2(obj(body.error).message) || `\u8BFB\u53D6\u5DE5\u7A0B\u5931\u8D25\uFF08HTTP ${response.status}\uFF09`);
  return str2(body.revision);
}
async function submit(ctx, tool, input) {
  const baseRevision = input.baseRevision || await currentRevision(ctx, input.projectId);
  const requestId = input.requestId || `${tool}-${contentHash({ projectId: input.projectId, operations: input.operations }).slice(0, 20)}`;
  const response = await ctx.client.request("POST", `/v1/projects/${input.projectId}/transactions`, {
    apiVersion: "1",
    requestId,
    projectId: input.projectId,
    baseRevision,
    operations: input.operations,
    summary: input.summary
  });
  if (response.status >= 400) return fromHttp(tool, response);
  return transactionResult(tool, obj(response.body));
}
var TOOL_DEFINITIONS = [
  {
    name: "studio_get_capabilities",
    title: "\u67E5\u8BE2\u540E\u53F0\u80FD\u529B",
    description: "\u8FD4\u56DE\u540E\u53F0\u7248\u672C\u3001\u534F\u8BAE\u7248\u672C\u3001\u5DF2\u5B9E\u73B0\u4E8B\u4EF6\u7C7B\u578B\u4E0E\u5DE5\u5177\u6E05\u5355\u3002\u5236\u4F5C\u524D\u5E94\u5148\u8C03\u7528\uFF0C\u786E\u8BA4\u53EF\u7528\u80FD\u529B\u3002",
    scopes: ["read"],
    inputSchema: schema({}),
    handler: async (ctx) => fromHttp("studio_get_capabilities", await ctx.client.request("GET", "/v1/capabilities"))
  },
  {
    name: "studio_list_projects",
    title: "\u5217\u51FA\u5DE5\u7A0B",
    description: "\u5217\u51FA\u5F53\u524D\u5DE5\u4F5C\u533A\u4E2D\u5DF2\u6709\u5DE5\u7A0B\u53CA\u5176\u4FEE\u8BA2\u53F7\u3001\u7AE0\u8282/\u573A\u666F/\u4E8B\u4EF6\u6570\u91CF\u3002",
    scopes: ["read"],
    inputSchema: schema({}),
    handler: async (ctx) => fromHttp("studio_list_projects", await ctx.client.request("GET", "/v1/projects"))
  },
  {
    name: "studio_open_project",
    title: "\u6253\u5F00\u5DE5\u7A0B",
    description: "\u8BFB\u53D6\u5DE5\u7A0B\u4FE1\u606F\u4E0E\u5F53\u524D\u4FEE\u8BA2\u53F7\uFF0C\u4F5C\u4E3A\u540E\u7EED\u4E8B\u52A1\u7684 baseRevision \u8D77\u70B9\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_open_project", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}`))
  },
  {
    name: "studio_get_project",
    title: "\u8BFB\u53D6\u5DE5\u7A0B\u7ED3\u6784",
    description: "\u8FD4\u56DE\u7AE0\u8282/\u573A\u666F\u5927\u7EB2\u3001\u53D8\u91CF\u3001\u89D2\u8272\u4E0E\u5173\u5361\u5B9A\u4E49\uFF08\u4E0D\u542B\u6BCF\u4E2A\u4E8B\u4EF6\u7684\u5B8C\u6574\u53C2\u6570\uFF09\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_get_project", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/outline`))
  },
  {
    name: "studio_read_scene",
    title: "\u8BFB\u53D6\u573A\u666F\u4E8B\u4EF6",
    description: "\u8BFB\u53D6\u6307\u5B9A\u573A\u666F\u7684\u5168\u90E8\u4E8B\u4EF6\uFF08\u7A33\u5B9A ID\u3001\u7C7B\u578B\u3001\u53C2\u6570\u4E0E\u4E2D\u6587\u6458\u8981\uFF09\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId, sceneId: S.sceneId }, ["projectId", "sceneId"]),
    handler: async (ctx, args) => fromHttp("studio_read_scene", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/scenes/${str2(args.sceneId)}`))
  },
  {
    name: "studio_search",
    title: "\u641C\u7D22\u5DE5\u7A0B\u5185\u5BB9",
    description: "\u5728\u7AE0\u8282\u3001\u573A\u666F\u3001\u4E8B\u4EF6\u53C2\u6570\u3001\u7D20\u6750\u4E0E\u89D2\u8272\u4E2D\u6309\u5173\u952E\u5B57\u641C\u7D22\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId, query: { type: "string", description: "\u5173\u952E\u5B57" } }, ["projectId", "query"]),
    handler: async (ctx, args) => fromHttp("studio_search", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/search?q=${encodeURIComponent(str2(args.query))}`))
  },
  {
    name: "studio_list_assets",
    title: "\u5217\u51FA\u7D20\u6750",
    description: "\u5217\u51FA\u5DE5\u7A0B\u7D20\u6750\u5E93\u4E2D\u7684\u7D20\u6750\uFF08ID\u3001\u7C7B\u578B\u3001\u8DEF\u5F84\u3001\u6563\u5217\u3001\u6765\u6E90\uFF09\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_list_assets", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/assets`))
  },
  {
    name: "studio_create_chapter",
    title: "\u521B\u5EFA\u7AE0\u8282",
    description: "\u5728\u5DE5\u7A0B\u4E2D\u521B\u5EFA\u7AE0\u8282\u3002\u53EF\u6307\u5B9A chapterId \u4EE5\u4FBF\u91CD\u8BD5\u4FDD\u6301\u5E42\u7B49\u3002",
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      title: S.title,
      chapterId: { type: "string", description: "\u53EF\u9009\uFF0C\u7A33\u5B9A\u7AE0\u8282 ID\uFF08ch_xxx\uFF09\uFF1B\u7701\u7565\u65F6\u7531\u540E\u53F0\u751F\u6210" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "title"]),
    handler: async (ctx, args) => submit(ctx, "studio_create_chapter", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u521B\u5EFA\u7AE0\u8282\u300A${str2(args.title)}\u300B`,
      operations: [{ action: "chapter.create", payload: { title: str2(args.title), ...str2(args.chapterId) ? { chapterId: str2(args.chapterId) } : {} } }]
    })
  },
  {
    name: "studio_create_scene",
    title: "\u521B\u5EFA\u573A\u666F",
    description: "\u5728\u6307\u5B9A\u7AE0\u8282\u4E0B\u521B\u5EFA\u573A\u666F\u3002",
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      chapterId: { type: "string", description: "\u7AE0\u8282 ID\uFF08ch_xxx\uFF09" },
      title: S.title,
      sceneId: { type: "string", description: "\u53EF\u9009\uFF0C\u7A33\u5B9A\u573A\u666F ID\uFF08sc_xxx\uFF09" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "chapterId", "title"]),
    handler: async (ctx, args) => submit(ctx, "studio_create_scene", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u521B\u5EFA\u573A\u666F\u300A${str2(args.title)}\u300B`,
      operations: [{ action: "scene.create", payload: { chapterId: str2(args.chapterId), title: str2(args.title), ...str2(args.sceneId) ? { sceneId: str2(args.sceneId) } : {} } }]
    })
  },
  {
    name: "studio_insert_event",
    title: "\u63D2\u5165\u4E8B\u4EF6",
    description: `\u5728\u573A\u666F\u4E2D\u63D2\u5165\u4E8B\u4EF6\u3002\u4E8B\u4EF6\u7C7B\u578B\uFF1A${EVENT_TYPES.join("/")}\u3002\u53C2\u6570\u89C4\u8303\u4EE5 studio_get_capabilities \u7684 events \u5B57\u6BB5\u4E3A\u51C6\u3002`,
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      sceneId: S.sceneId,
      type: { type: "string", enum: EVENT_TYPES },
      params: { type: "object", description: "\u4E8B\u4EF6\u53C2\u6570\uFF0C\u6309\u4E8B\u4EF6\u7C7B\u578B\u6821\u9A8C" },
      index: { type: "number", description: "\u63D2\u5165\u4F4D\u7F6E\uFF080 \u8D77\uFF0C\u7701\u7565\u5219\u8FFD\u52A0\uFF09" },
      eventId: { type: "string", description: "\u53EF\u9009\uFF0C\u7A33\u5B9A\u4E8B\u4EF6 ID\uFF08ev_xxx\uFF09\uFF1B\u91CD\u8BD5\u65F6\u590D\u7528\u53EF\u907F\u514D\u91CD\u590D\u63D2\u5165" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "sceneId", "type", "params"]),
    handler: async (ctx, args) => submit(ctx, "studio_insert_event", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u63D2\u5165 ${str2(args.type)} \u4E8B\u4EF6`,
      operations: [{
        action: "event.insert",
        payload: {
          sceneId: str2(args.sceneId),
          type: str2(args.type),
          params: obj(args.params),
          ...typeof args.index === "number" ? { index: args.index } : {},
          ...str2(args.eventId) ? { eventId: str2(args.eventId) } : {}
        }
      }]
    })
  },
  {
    name: "studio_update_event",
    title: "\u4FEE\u6539\u4E8B\u4EF6",
    description: "\u8986\u76D6\u6307\u5B9A\u4E8B\u4EF6\u7684\u53C2\u6570\u3002\u53EF\u5E26 expectedObjectRevision \u505A\u5BF9\u8C61\u7EA7\u7248\u672C\u68C0\u67E5\u3002",
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      sceneId: S.sceneId,
      eventId: S.eventId,
      params: { type: "object", description: "\u65B0\u7684\u5B8C\u6574\u53C2\u6570\u5BF9\u8C61" },
      expectedObjectRevision: { type: "string", description: "\u53EF\u9009\uFF0C\u5BF9\u8C61\u7EA7\u671F\u671B\u7248\u672C\uFF08\u63D0\u4EA4\u5E8F\u53F7\uFF09" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "sceneId", "eventId", "params"]),
    handler: async (ctx, args) => submit(ctx, "studio_update_event", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u4FEE\u6539\u4E8B\u4EF6 ${str2(args.eventId)}`,
      operations: [{
        action: "event.update",
        targetIds: [str2(args.eventId)],
        ...str2(args.expectedObjectRevision) ? { expectedObjectRevision: str2(args.expectedObjectRevision) } : {},
        payload: { sceneId: str2(args.sceneId), eventId: str2(args.eventId), params: obj(args.params) }
      }]
    })
  },
  {
    name: "studio_delete_event",
    title: "\u5220\u9664\u4E8B\u4EF6",
    description: "\u5220\u9664\u573A\u666F\u4E2D\u7684\u4E8B\u4EF6\u3002",
    scopes: ["edit"],
    inputSchema: schema({ projectId: S.projectId, sceneId: S.sceneId, eventId: S.eventId, baseRevision: S.baseRevision, requestId: S.requestId }, ["projectId", "sceneId", "eventId"]),
    handler: async (ctx, args) => submit(ctx, "studio_delete_event", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u5220\u9664\u4E8B\u4EF6 ${str2(args.eventId)}`,
      operations: [{ action: "event.delete", targetIds: [str2(args.eventId)], payload: { sceneId: str2(args.sceneId), eventId: str2(args.eventId) } }]
    })
  },
  {
    name: "studio_apply_changes",
    title: "\u6279\u91CF\u5E94\u7528\u4FEE\u6539",
    description: `\u5728\u4E00\u4E2A\u4E8B\u52A1\u4E2D\u5E94\u7528\u591A\u4E2A\u64CD\u4F5C\uFF08\u6574\u4F53\u6210\u529F\u6216\u6574\u4F53\u4E0D\u63D0\u4EA4\uFF09\u3002\u652F\u6301\u7684\u64CD\u4F5C\uFF1A${Object.keys(EVENT_SPECS).length ? "" : ""}\u89C1 studio_get_capabilities \u7684 actions \u5B57\u6BB5\u3002`,
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      baseRevision: S.baseRevision,
      requestId: S.requestId,
      summary: { type: "string", description: "\u53D8\u66F4\u6458\u8981" },
      operations: {
        type: "array",
        description: "\u64CD\u4F5C\u5217\u8868\uFF0C\u6BCF\u9879 {action, payload, targetIds?, expectedObjectRevision?}",
        items: {
          type: "object",
          properties: {
            action: { type: "string" },
            payload: { type: "object" },
            targetIds: { type: "array", items: { type: "string" } },
            expectedObjectRevision: { type: "string" }
          },
          required: ["action"],
          additionalProperties: false
        }
      }
    }, ["projectId", "operations"]),
    handler: async (ctx, args) => {
      const operations = Array.isArray(args.operations) ? args.operations.map((item) => {
        const record = obj(item);
        return {
          action: str2(record.action),
          payload: obj(record.payload),
          ...Array.isArray(record.targetIds) ? { targetIds: record.targetIds.map((value) => String(value)) } : {},
          ...str2(record.expectedObjectRevision) ? { expectedObjectRevision: str2(record.expectedObjectRevision) } : {}
        };
      }) : [];
      if (!operations.length) return errorResult("studio_apply_changes", { code: "BAD_REQUEST", message: "operations \u4E0D\u80FD\u4E3A\u7A7A" });
      return submit(ctx, "studio_apply_changes", {
        projectId: str2(args.projectId),
        baseRevision: str2(args.baseRevision) || void 0,
        requestId: str2(args.requestId) || void 0,
        summary: str2(args.summary) || void 0,
        operations
      });
    }
  },
  {
    name: "studio_import_asset",
    title: "\u5BFC\u5165\u7D20\u6750",
    description: "\u4ECE\u540E\u53F0\u5DF2\u6388\u6743\u7684\u5BFC\u5165\u76EE\u5F55\u767B\u8BB0\u7D20\u6750\u6587\u4EF6\uFF1B\u6587\u4EF6\u4F1A\u590D\u5236\u5230\u5DE5\u7A0B assets/originals \u5E76\u6821\u9A8C\u53EF\u89E3\u7801\u3002",
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      sourcePath: { type: "string", description: "\u540E\u53F0\u5DF2\u6388\u6743\u5BFC\u5165\u6839\u76EE\u5F55\u4E0B\u7684\u6587\u4EF6\u8DEF\u5F84" },
      name: { type: "string", description: "\u7D20\u6750\u540D\u79F0" },
      kind: { type: "string", enum: ["background", "character", "cg", "audio", "video"] },
      assetId: { type: "string", description: "\u53EF\u9009\uFF0C\u7A33\u5B9A\u7D20\u6750 ID\uFF08as_xxx\uFF09" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "sourcePath", "name", "kind"]),
    handler: async (ctx, args) => submit(ctx, "studio_import_asset", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u5BFC\u5165\u7D20\u6750 ${str2(args.name)}`,
      operations: [{
        action: "asset.registerFromFile",
        payload: {
          sourcePath: str2(args.sourcePath),
          name: str2(args.name),
          kind: str2(args.kind),
          templateId: args.templateId === void 0 ? void 0 : str2(args.templateId),
          ...str2(args.assetId) ? { assetId: str2(args.assetId) } : {}
        }
      }]
    })
  },
  {
    name: "studio_bind_asset",
    title: "\u7ED1\u5B9A\u7D20\u6750\u5230\u4E8B\u4EF6",
    description: "\u628A\u5DF2\u767B\u8BB0\u7D20\u6750\u7ED1\u5B9A\u5230\u4E8B\u4EF6\u7684\u7D20\u6750\u5F15\u7528\u5B57\u6BB5\uFF08\u5982 background.assetId\u3001character.assetId\uFF09\u3002",
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      sceneId: S.sceneId,
      eventId: S.eventId,
      field: { type: "string", description: "\u4E8B\u4EF6\u53C2\u6570\u4E2D\u7684\u7D20\u6750\u5B57\u6BB5\u540D\uFF0C\u4F8B\u5982 assetId" },
      assetId: { type: "string", description: "\u7D20\u6750 ID\uFF08as_xxx\uFF09" },
      expectedObjectRevision: { type: "string", description: "\u53EF\u9009\uFF0C\u5BF9\u8C61\u7EA7\u671F\u671B\u7248\u672C" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "sceneId", "eventId", "field", "assetId"]),
    handler: async (ctx, args) => submit(ctx, "studio_bind_asset", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u7ED1\u5B9A\u7D20\u6750 ${str2(args.assetId)} \u5230 ${str2(args.eventId)}.${str2(args.field)}`,
      operations: [{
        action: "asset.bind",
        targetIds: [str2(args.eventId)],
        ...str2(args.expectedObjectRevision) ? { expectedObjectRevision: str2(args.expectedObjectRevision) } : {},
        payload: { sceneId: str2(args.sceneId), eventId: str2(args.eventId), field: str2(args.field), assetId: str2(args.assetId) }
      }]
    })
  },
  {
    name: "studio_validate_project",
    title: "\u6821\u9A8C\u5DE5\u7A0B",
    description: "\u6267\u884C\u7ED3\u6784\u4E0E\u5F15\u7528\u6821\u9A8C\uFF0C\u8FD4\u56DE\u95EE\u9898\u5217\u8868\uFF08\u5F15\u7528\u7F3A\u5931\u3001\u53C2\u6570\u4E0D\u5408\u6CD5\u7B49\uFF09\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_validate_project", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/validate`))
  },
  {
    name: "studio_get_changes",
    title: "\u67E5\u8BE2\u53D8\u66F4",
    description: "\u6309\u53D8\u66F4\u5E8F\u53F7\u589E\u91CF\u67E5\u8BE2\u53D8\u66F4\u8BB0\u5F55\uFF1B\u8FD4\u56DE gap \u4E3A true \u65F6\u9700\u8981\u91CD\u65B0\u8BFB\u53D6\u5B8C\u6574\u5FEB\u7167\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId, after: { type: "number", description: "\u53EA\u8FD4\u56DE\u5E8F\u53F7\u5927\u4E8E\u8BE5\u503C\u7684\u53D8\u66F4" } }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_get_changes", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/changes?after=${Number(args.after ?? 0) || 0}`))
  },
  {
    name: "studio_get_history",
    title: "\u67E5\u8BE2\u63D0\u4EA4\u5386\u53F2",
    description: "\u8FD4\u56DE\u5DF2\u63D0\u4EA4\u4E8B\u52A1\u5217\u8868\uFF08\u542B\u64CD\u4F5C\u3001\u5F71\u54CD\u5BF9\u8C61\u3001\u64A4\u9500\u5173\u7CFB\uFF09\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_get_history", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/history`))
  },
  {
    name: "studio_undo",
    title: "\u64A4\u9500\u4E8B\u52A1",
    description: "\u5BF9\u6307\u5B9A\u4E8B\u52A1\u751F\u6210\u8865\u507F\u4FEE\u6539\uFF1B\u82E5\u5176\u540E\u6709\u4E8B\u52A1\u4FEE\u6539\u4E86\u540C\u4E00\u5BF9\u8C61\uFF0C\u8FD4\u56DE\u4F9D\u8D56\u51B2\u7A81\u800C\u4E0D\u8986\u76D6\u3002",
    scopes: ["edit"],
    inputSchema: schema({ projectId: S.projectId, undoId: { type: "string", description: "\u4E8B\u52A1 ID\uFF08\u63D0\u4EA4\u7ED3\u679C\u4E2D\u7684 undoId\uFF09" }, requestId: S.requestId, baseRevision: S.baseRevision }, ["projectId", "undoId"]),
    handler: async (ctx, args) => {
      const projectId = str2(args.projectId);
      const requestId = str2(args.requestId) || `studio_undo-${contentHash({ projectId, undoId: str2(args.undoId) }).slice(0, 20)}`;
      const response = await ctx.client.request("POST", `/v1/projects/${projectId}/undo`, {
        requestId,
        undoId: str2(args.undoId),
        ...str2(args.baseRevision) ? { baseRevision: str2(args.baseRevision) } : {}
      });
      if (response.status >= 400) return fromHttp("studio_undo", response);
      return transactionResult("studio_undo", obj(response.body));
    }
  },
  {
    name: "studio_save_project",
    title: "\u4FDD\u5B58\u68C0\u67E5\u70B9",
    description: "\u5199\u5165\u68C0\u67E5\u70B9\u5E76\u56DE\u8BFB .studio/state.json \u786E\u8BA4\u4FEE\u8BA2\u5DF2\u843D\u76D8\uFF0C\u8FD4\u56DE\u6301\u4E45\u5316\u7ED3\u679C\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_save_project", await ctx.client.request("POST", `/v1/projects/${str2(args.projectId)}/checkpoints`, {}))
  },
  {
    name: "studio_start_preview",
    title: "\u5F00\u59CB\u65E0\u754C\u9762\u9884\u89C8",
    description: "\u57FA\u4E8E\u6307\u5B9A\u4FEE\u8BA2\u4E0E\u8D77\u59CB\u573A\u666F\u521B\u5EFA\u9884\u89C8\u4F1A\u8BDD\uFF0C\u8FD4\u56DE\u72B6\u6001\u3001\u5F53\u524D\u5BF9\u767D\u4E0E\u53EF\u9009\u5206\u652F\u3002\u4F7F\u7528\u4E0E\u5C55\u793A\u5668\u76F8\u540C\u7684\u8FD0\u884C\u5185\u6838\u3002",
    scopes: ["preview"],
    inputSchema: schema({
      projectId: S.projectId,
      sceneId: { type: "string", description: "\u8D77\u59CB\u573A\u666F\uFF08\u7701\u7565\u5219\u4F7F\u7528\u5DE5\u7A0B\u5165\u53E3\u573A\u666F\uFF09" },
      variables: { type: "object", description: "\u521D\u59CB\u53D8\u91CF\u8986\u76D6" }
    }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_start_preview", await ctx.client.request("POST", `/v1/projects/${str2(args.projectId)}/previews`, {
      ...str2(args.sceneId) ? { sceneId: str2(args.sceneId) } : {},
      ...args.variables && typeof args.variables === "object" ? { variables: args.variables } : {}
    }))
  },
  {
    name: "studio_preview_action",
    title: "\u63A8\u8FDB\u9884\u89C8",
    description: "\u63D0\u4EA4\u4E00\u6B21\u9884\u89C8\u52A8\u4F5C\uFF1Aadvance\uFF08\u63A8\u8FDB\u5BF9\u767D\uFF09\u3001choose\uFF08\u9009\u62E9\u5206\u652F\uFF09\u3001tick\uFF08\u7B49\u5F85\u63A8\u8FDB\uFF09\u3001effectComplete/effectFailed\uFF08\u5BBF\u4E3B\u80FD\u529B\u7ED3\u679C\uFF09\u3001levelMessage\uFF08\u5173\u5361\u6D88\u606F\uFF09\u3002",
    scopes: ["preview"],
    inputSchema: schema({
      projectId: S.projectId,
      previewId: { type: "string", description: "\u9884\u89C8\u4F1A\u8BDD ID\uFF08pvw_xxx\uFF09" },
      type: { type: "string", enum: ["advance", "choose", "tick", "effectComplete", "effectFailed", "levelMessage"] },
      option: { type: "number", description: "choose \u7684\u9009\u9879\u5E8F\u53F7\uFF080 \u8D77\uFF09" },
      elapsedMs: { type: "number", description: "tick \u7684\u6BEB\u79D2\u6570" },
      payload: { type: "object", description: "\u5BBF\u4E3B\u80FD\u529B\u7ED3\u679C" },
      error: { type: "object", description: "\u5BBF\u4E3B\u5931\u8D25\u4FE1\u606F {code,message}" },
      message: { type: "object", description: "\u5173\u5361\u6D88\u606F {kind,levelInstanceId,requestId,messageSeq,payload}" }
    }, ["projectId", "previewId", "type"]),
    handler: async (ctx, args) => {
      const body = { type: str2(args.type) };
      if (args.option !== void 0) body.option = Number(args.option);
      if (args.elapsedMs !== void 0) body.elapsedMs = Number(args.elapsedMs);
      if (args.payload !== void 0) body.payload = args.payload;
      if (args.error !== void 0) body.error = args.error;
      if (args.message !== void 0) body.message = args.message;
      return fromHttp("studio_preview_action", await ctx.client.request("POST", `/v1/projects/${str2(args.projectId)}/previews/${str2(args.previewId)}/actions`, body));
    }
  },
  {
    name: "studio_get_preview",
    title: "\u67E5\u8BE2\u9884\u89C8\u72B6\u6001",
    description: "\u8BFB\u53D6\u9884\u89C8\u4F1A\u8BDD\u72B6\u6001\uFF08\u72B6\u6001\u3001\u573A\u666F\u3001\u5F53\u524D\u5BF9\u767D\u6570\u91CF\u3001\u5F85\u9009\u5206\u652F\u3001\u9519\u8BEF\uFF09\u3002",
    scopes: ["preview"],
    inputSchema: schema({ projectId: S.projectId, previewId: { type: "string" } }, ["projectId", "previewId"]),
    handler: async (ctx, args) => fromHttp("studio_get_preview", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/previews/${str2(args.previewId)}`))
  },
  {
    name: "studio_render_preview",
    title: "\u751F\u6210\u9884\u89C8\u4EA7\u7269",
    description: "\u628A\u5F53\u524D\u9884\u89C8\u72B6\u6001\u6E32\u67D3\u4E3A\u4EA7\u7269\u5E76\u5199\u5165 .studio/\u3002\u9ED8\u8BA4 format=snapshot \u751F\u6210\u7ED3\u6784\u5316\u821E\u53F0\u5FEB\u7167\uFF08HTML + JSON\uFF09\uFF1Bformat=pixel \u989D\u5916\u7528\u65E0\u5934\u6D4F\u89C8\u5668\u622A\u56FE\u751F\u6210 PNG\uFF08\u53EF\u9009\u80FD\u529B\uFF0C\u9700\u8981\u672C\u673A\u5B89\u88C5 Playwright \u6D4F\u89C8\u5668\uFF0C\u4E0D\u53EF\u7528\u65F6\u8FD4\u56DE RENDER_UNAVAILABLE\uFF09\u3002",
    scopes: ["preview"],
    inputSchema: schema({
      projectId: S.projectId,
      previewId: { type: "string" },
      format: { type: "string", enum: ["snapshot", "pixel"], description: "snapshot\uFF08\u9ED8\u8BA4\uFF0C\u7ED3\u6784\u5316\u5FEB\u7167\uFF09\u6216 pixel\uFF08\u989D\u5916\u751F\u6210 PNG \u622A\u56FE\uFF09" },
      width: { type: "number", description: "pixel \u683C\u5F0F\u7684\u753B\u5E03\u5BBD\u5EA6\uFF08160\u20132560\uFF0C\u9ED8\u8BA4 1280\uFF09" },
      height: { type: "number", description: "pixel \u683C\u5F0F\u7684\u753B\u5E03\u9AD8\u5EA6\uFF08160\u20132560\uFF0C\u9ED8\u8BA4 720\uFF09" }
    }, ["projectId", "previewId"]),
    handler: async (ctx, args) => fromHttp("studio_render_preview", await ctx.client.request("POST", `/v1/projects/${str2(args.projectId)}/previews/${str2(args.previewId)}/render`, {
      format: args.format === "pixel" ? "pixel" : "snapshot",
      ...typeof args.width === "number" ? { width: args.width } : {},
      ...typeof args.height === "number" ? { height: args.height } : {}
    }))
  },
  {
    name: "studio_export_project",
    title: "\u5BFC\u51FA\u53D1\u5E03\u5305",
    description: "\u628A\u5F53\u524D\u4FEE\u8BA2\u7684\u5185\u5BB9\u4E0E\u5C55\u793A\u5668\u6253\u5305\u4E3A\u53EF\u72EC\u7ACB\u8FD0\u884C\u7684\u76EE\u5F55\uFF08\u542B manifest.json \u4E0E serve.mjs\uFF09\uFF0C\u5E76\u62A5\u544A\u654F\u611F\u4FE1\u606F\u4E0E\u7D20\u6750\u7F3A\u5931\u68C0\u67E5\u7ED3\u679C\u3002\u9700\u8981\u5148\u8FD0\u884C npm run build:player\u3002",
    scopes: ["export"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_export_project", await ctx.client.request("POST", `/v1/projects/${str2(args.projectId)}/exports`, {}))
  },
  {
    name: "studio_list_exports",
    title: "\u5217\u51FA\u53D1\u5E03\u5305",
    description: "\u5217\u51FA\u5DF2\u751F\u6210\u7684\u53D1\u5E03\u5305\u53CA\u5176\u4FEE\u8BA2\u3001\u6587\u4EF6\u6570\u4E0E\u654F\u611F\u4FE1\u606F\u68C0\u67E5\u7ED3\u679C\u3002",
    scopes: ["read"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_list_exports", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/exports`))
  },
  {
    name: "studio_install_level",
    title: "\u5B89\u88C5\u5173\u5361\u7A0B\u5E8F",
    description: "\u4ECE\u540E\u53F0\u5DF2\u6388\u6743\u7684\u5BFC\u5165\u76EE\u5F55\u5B89\u88C5 HTML \u5173\u5361\u7A0B\u5E8F\u5230 levels/\uFF0C\u5E76\u628A\u5173\u5361\u5165\u53E3\u6307\u5411\u8BE5\u6587\u4EF6\u3002",
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      levelId: { type: "string", description: "\u5DF2\u767B\u8BB0\u7684\u5173\u5361 ID\uFF08lv_xxx\uFF09" },
      sourcePath: { type: "string", description: "\u5DF2\u6388\u6743\u5BFC\u5165\u76EE\u5F55\u4E0B\u7684 .html \u6587\u4EF6" },
      entry: { type: "string", description: "\u53EF\u9009\uFF0C\u5DE5\u7A0B\u5185\u5165\u53E3\u8DEF\u5F84\uFF08\u9ED8\u8BA4 levels/<levelId>.html\uFF09" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "levelId", "sourcePath"]),
    handler: async (ctx, args) => submit(ctx, "studio_install_level", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u5B89\u88C5\u5173\u5361\u7A0B\u5E8F ${str2(args.levelId)}`,
      operations: [{
        action: "level.installProgram",
        targetIds: [str2(args.levelId)],
        payload: { levelId: str2(args.levelId), sourcePath: str2(args.sourcePath), ...str2(args.entry) ? { entry: str2(args.entry) } : {} }
      }]
    })
  },
  {
    name: "studio_register_level",
    title: "\u767B\u8BB0\u5173\u5361",
    description: "\u5728\u5DE5\u7A0B\u4E2D\u767B\u8BB0\u5173\u5361\u5B9A\u4E49\uFF08ID\u3001\u6807\u9898\u3001\u5165\u53E3\u3001\u662F\u5426\u652F\u6301\u5FEB\u7167\uFF09\uFF0C\u767B\u8BB0\u540E\u624D\u80FD\u5B89\u88C5\u7A0B\u5E8F\u5E76\u5728\u4E8B\u4EF6\u4E2D\u5F15\u7528\u3002",
    scopes: ["edit"],
    inputSchema: schema({
      projectId: S.projectId,
      title: S.title,
      levelId: { type: "string", description: "\u53EF\u9009\uFF0C\u7A33\u5B9A\u5173\u5361 ID\uFF08lv_xxx\uFF09" },
      entry: { type: "string", description: "\u5173\u5361\u5165\u53E3\u76F8\u5BF9\u8DEF\u5F84\uFF08\u9ED8\u8BA4 levels/<levelId>.html\uFF09" },
      supportsSnapshot: { type: "boolean", description: "\u662F\u5426\u652F\u6301\u5FEB\u7167\u6062\u590D\uFF08\u9ED8\u8BA4 true\uFF09" },
      baseRevision: S.baseRevision,
      requestId: S.requestId
    }, ["projectId", "title"]),
    handler: async (ctx, args) => submit(ctx, "studio_register_level", {
      projectId: str2(args.projectId),
      baseRevision: str2(args.baseRevision) || void 0,
      requestId: str2(args.requestId) || void 0,
      summary: `\u767B\u8BB0\u5173\u5361\u300A${str2(args.title)}\u300B`,
      operations: [{
        action: "level.register",
        payload: {
          title: str2(args.title),
          ...str2(args.levelId) ? { levelId: str2(args.levelId) } : {},
          ...str2(args.entry) ? { entry: str2(args.entry) } : {},
          ...typeof args.supportsSnapshot === "boolean" ? { supportsSnapshot: args.supportsSnapshot } : {}
        }
      }]
    })
  },
  {
    name: "studio_list_media_providers",
    title: "\u5217\u51FA\u751F\u6210\u670D\u52A1",
    description: "\u8FD4\u56DE\u53EF\u7528\u7684\u56FE\u7247/\u89C6\u9891\u751F\u6210\u670D\u52A1\u4E0E\u6A21\u578B\u80FD\u529B\uFF08\u7C7B\u578B\u3001\u53C2\u8003\u7D20\u6750\u89D2\u8272\u3001\u6BD4\u4F8B\u3001\u65F6\u957F\u3001\u8D39\u7528\u72B6\u6001\uFF09\u3002\u5F53\u524D\u53EA\u6709\u660E\u786E\u6807\u6CE8\u7684\u6D4B\u8BD5\u9002\u914D\u5668 isTestAdapter=true\uFF1B\u771F\u5B9E\u670D\u52A1\u5C1A\u672A\u63A5\u5165\u3002",
    scopes: ["generate"],
    inputSchema: schema({}),
    handler: async (ctx) => fromHttp("studio_list_media_providers", await ctx.client.request("GET", "/v1/media/providers"))
  },
  {
    name: "studio_estimate_media",
    title: "\u4F30\u7B97\u751F\u6210\u8D39\u7528",
    description: "\u5728\u63D0\u4EA4\u524D\u6309\u670D\u52A1\u80FD\u529B\u6821\u9A8C\u53C2\u6570\u5E76\u8FD4\u56DE\u8D39\u7528\u4F30\u7B97\u4E0E\u9884\u7B97\u72B6\u6001\uFF08\u4E0D\u4EA7\u751F\u4EFB\u52A1\uFF09\u3002",
    scopes: ["generate"],
    inputSchema: schema({
      projectId: S.projectId,
      providerId: { type: "string", description: "\u670D\u52A1 ID\uFF08\u5982 fixture-local\uFF09" },
      modelId: { type: "string", description: "\u6A21\u578B ID" },
      kind: { type: "string", enum: ["image.text", "image.edit", "video.text", "video.image"] },
      templateId: { type: "string", description: "\u751F\u6210\u6A21\u677F ID\uFF08recipes/<id>.json\uFF09\uFF1A\u670D\u52A1\u7AEF\u7528\u6A21\u677F\u7684\u63D0\u793A\u8BCD\u4E0E\u53C2\u6570\u8865\u9F50\uFF0C\u8BF7\u6C42\u91CC\u7684\u663E\u5F0F\u503C\u4F18\u5148" },
      prompt: { type: "string" },
      output: { type: "object", description: "{ count, aspectRatio, width, height, durationSeconds, fps, transparent, audio }" },
      references: { type: "array", description: "[{ assetId, version, role }]", items: { type: "object" } }
    }, ["projectId", "providerId", "modelId", "kind", "prompt", "output"]),
    handler: async (ctx, args) => {
      const projectId = str2(args.projectId);
      const response = await ctx.client.request("POST", `/v1/projects/${projectId}/media-jobs/estimate`, {
        providerId: str2(args.providerId),
        modelId: str2(args.modelId),
        kind: str2(args.kind),
        prompt: str2(args.prompt),
        output: obj(args.output),
        references: Array.isArray(args.references) ? args.references : [],
        requestId: `estimate-${contentHash({ args }).slice(0, 16)}`
      });
      return fromHttp("studio_estimate_media", response);
    }
  },
  {
    name: "studio_create_media_job",
    title: "\u63D0\u4EA4\u751F\u6210\u4EFB\u52A1",
    description: "\u63D0\u4EA4\u56FE\u7247/\u89C6\u9891\u751F\u6210\u4EFB\u52A1\uFF08\u540E\u53F0\u961F\u5217\uFF09\u3002\u8FD4\u56DE jobId\uFF1B\u7528 studio_get_job \u67E5\u8BE2\u72B6\u6001\u4E0E\u5019\u9009\u3002\u6D4B\u8BD5\u9002\u914D\u5668\u53EF\u5E26 providerOptions.fixtureMode \u9A8C\u8BC1\u5931\u8D25\u8DEF\u5F84\u3002",
    scopes: ["generate"],
    inputSchema: schema({
      projectId: S.projectId,
      providerId: { type: "string" },
      modelId: { type: "string" },
      kind: { type: "string", enum: ["image.text", "image.edit", "video.text", "video.image"] },
      prompt: { type: "string" },
      negativePrompt: { type: "string" },
      output: { type: "object", description: "{ count, aspectRatio, width, height, durationSeconds, fps, transparent, audio }" },
      references: { type: "array", items: { type: "object" }, description: "[{ assetId, version, role }]" },
      seed: { type: "number" },
      providerOptions: { type: "object", description: "\u7531\u9002\u914D\u5668\u6821\u9A8C\uFF1B\u6D4B\u8BD5\u9002\u914D\u5668\u652F\u6301 fixtureMode: ok/submit-timeout/poll-unknown/provider-error/download-failure/slow" },
      binding: { type: "object", description: "{ objectId, field, expectedRevision, mode }\uFF0C\u4EC5\u8BB0\u5F55\u610F\u56FE\uFF1B\u5B9E\u9645\u7ED1\u5B9A\u7528 studio_accept_media_output" },
      requestId: S.requestId
    }, ["projectId", "providerId", "modelId", "kind", "prompt", "output"]),
    handler: async (ctx, args) => fromHttp("studio_create_media_job", await ctx.client.request("POST", `/v1/projects/${str2(args.projectId)}/media-jobs`, {
      requestId: str2(args.requestId) || `job-${contentHash({ args }).slice(0, 20)}`,
      providerId: str2(args.providerId),
      modelId: str2(args.modelId),
      kind: str2(args.kind),
      prompt: str2(args.prompt),
      negativePrompt: str2(args.negativePrompt) || void 0,
      output: obj(args.output),
      references: Array.isArray(args.references) ? args.references : [],
      seed: args.seed === void 0 ? void 0 : Number(args.seed),
      providerOptions: args.providerOptions === void 0 ? void 0 : obj(args.providerOptions),
      binding: args.binding === void 0 ? void 0 : obj(args.binding)
    }))
  },
  {
    name: "studio_get_job",
    title: "\u67E5\u8BE2\u751F\u6210\u4EFB\u52A1",
    description: "\u6309 jobId \u67E5\u8BE2\u4EFB\u52A1\u72B6\u6001\u3001\u9636\u6BB5\u3001\u5019\u9009\u4EA7\u7269\uFF08\u542B\u662F\u5426\u53EF\u64AD\u653E\u4E0E\u7ED1\u5B9A\u72B6\u6001\uFF09\u4E0E\u9519\u8BEF\u3002",
    scopes: ["generate"],
    inputSchema: schema({ jobId: { type: "string", description: "\u4EFB\u52A1 ID\uFF08job_xxx\uFF09" } }, ["jobId"]),
    handler: async (ctx, args) => fromHttp("studio_get_job", await ctx.client.request("GET", `/v1/jobs/${str2(args.jobId)}`))
  },
  {
    name: "studio_list_jobs",
    title: "\u5217\u51FA\u751F\u6210\u4EFB\u52A1",
    description: "\u5217\u51FA\u5DE5\u7A0B\u7684\u751F\u6210\u4EFB\u52A1\u4E0E\u9884\u7B97\u72B6\u6001\u3002",
    scopes: ["generate"],
    inputSchema: schema({ projectId: S.projectId }, ["projectId"]),
    handler: async (ctx, args) => fromHttp("studio_list_jobs", await ctx.client.request("GET", `/v1/projects/${str2(args.projectId)}/media-jobs`))
  },
  {
    name: "studio_cancel_job",
    title: "\u53D6\u6D88\u751F\u6210\u4EFB\u52A1",
    description: "\u8BF7\u6C42\u53D6\u6D88\u4EFB\u52A1\uFF1B\u53EA\u6709\u670D\u52A1\u786E\u8BA4\u6216\u672C\u5730\u4EFB\u52A1\u786E\u5B9E\u505C\u6B62\u540E\u624D\u53D8\u4E3A\u5DF2\u53D6\u6D88\uFF08\u8BF7\u6C42\u4E2D\u72B6\u6001\u4E3A cancelling\uFF09\u3002",
    scopes: ["generate"],
    inputSchema: schema({ jobId: { type: "string" } }, ["jobId"]),
    handler: async (ctx, args) => fromHttp("studio_cancel_job", await ctx.client.request("POST", `/v1/jobs/${str2(args.jobId)}/cancel`, {}))
  },
  {
    name: "studio_accept_media_output",
    title: "\u91C7\u7528\u5019\u9009\u4EA7\u7269",
    description: "\u628A\u5019\u9009\u4EA7\u7269\u767B\u8BB0\u4E3A\u7D20\u6750\uFF0C\u5E76\u53EF\u5728\u540C\u4E00\u4E8B\u52A1\u4E2D\u7ED1\u5B9A\u5230\u5267\u60C5\u4E8B\u4EF6\u3002\u7ED1\u5B9A\u5E26\u5BF9\u8C61\u7EA7\u671F\u671B\u7248\u672C\uFF1B\u76EE\u6807\u5DF2\u88AB\u6539\u8FC7\u65F6\u4E8B\u52A1\u88AB\u62D2\u7EDD\uFF0C\u5019\u9009\u4ECD\u4FDD\u7559\u5728\u5019\u9009\u533A\u3002",
    scopes: ["generate", "edit"],
    inputSchema: schema({
      projectId: S.projectId,
      jobId: { type: "string" },
      outputId: { type: "string", description: "\u5019\u9009 ID\uFF08out_1 \u7B49\uFF09" },
      name: { type: "string", description: "\u7D20\u6750\u540D\u79F0" },
      kind: { type: "string", enum: ["background", "character", "cg", "audio", "video"] },
      binding: { type: "object", description: "{ sceneId, eventId, field, expectedObjectRevision }" }
    }, ["projectId", "jobId", "outputId"]),
    handler: async (ctx, args) => {
      const response = await ctx.client.request("POST", `/v1/projects/${str2(args.projectId)}/media-jobs/${str2(args.jobId)}/accept`, {
        outputId: str2(args.outputId),
        name: str2(args.name) || void 0,
        kind: str2(args.kind) || void 0,
        binding: args.binding === void 0 ? void 0 : obj(args.binding)
      });
      const body = obj(response.body);
      if (str2(body.status) === "applied") {
        return {
          ok: true,
          tool: "studio_accept_media_output",
          status: "applied",
          data: {
            assetId: body.assetId,
            transactionId: body.transactionId,
            revision: body.revision,
            bindingApplied: body.bindingApplied === true,
            job: body.job
          }
        };
      }
      return {
        ok: false,
        tool: "studio_accept_media_output",
        status: "rejected",
        data: { job: body.job, bindingApplied: false },
        error: {
          code: str2(body.errorCode) || "REJECTED",
          message: "\u5019\u9009\u672A\u80FD\u767B\u8BB0\u6216\u7ED1\u5B9A\uFF1B\u5019\u9009\u4ECD\u4FDD\u7559\u5728\u5019\u9009\u533A\uFF0C\u672A\u88AB\u5220\u9664",
          issues: Array.isArray(body.issues) ? body.issues : []
        }
      };
    }
  },
  {
    name: "studio_get_event_catalog",
    title: "\u67E5\u8BE2\u4E8B\u4EF6\u76EE\u5F55",
    description: "\u8FD4\u56DE\u4E8B\u4EF6\u7C7B\u578B\u53CA\u5176\u53C2\u6570\u89C4\u8303\uFF08\u4F9B\u5236\u4F5C\u65F6\u6784\u9020 params\uFF09\u3002",
    scopes: ["read"],
    inputSchema: schema({}),
    handler: async (ctx) => {
      const response = await ctx.client.request("GET", "/v1/capabilities");
      const result = fromHttp("studio_get_event_catalog", response);
      if (!result.ok) return result;
      const data = obj(result.data);
      const events = Array.isArray(data.events) ? data.events : [];
      const details = events.map((event) => {
        const record = obj(event);
        const spec = EVENT_SPECS[record.type];
        return spec ? { type: spec.type, label: spec.label, runtimeSupport: spec.runtimeSupport, params: spec.params } : record;
      });
      return { ok: true, tool: "studio_get_event_catalog", data: { apiVersion: data.apiVersion, events: details } };
    }
  }
];
var TOOL_NAMES = TOOL_DEFINITIONS.map((tool) => tool.name);
function toolByName(name) {
  return TOOL_DEFINITIONS.find((tool) => tool.name === name);
}

// server/src/mcp-stdio.ts
var SERVER_INFO = { name: "shiguang-studio-author", version: SERVICE_VERSION };
function log(...parts) {
  process.stderr.write(`[studio-mcp] ${parts.map((part) => typeof part === "string" ? part : JSON.stringify(part)).join(" ")}
`);
}
function write(message) {
  process.stdout.write(`${JSON.stringify(message)}
`);
}
function reply(id, result) {
  write({ jsonrpc: "2.0", id: id ?? null, result });
}
function replyError(id, code, message, data) {
  write({ jsonrpc: "2.0", id: id ?? null, error: { code, message, ...data === void 0 ? {} : { data } } });
}
async function createSessionClient(options) {
  const ensured = await ensureService({ workspaceRoot: options.workspaceRoot, autostart: options.autostart });
  const paths = workspacePaths(options.workspaceRoot);
  const pairingFile = readJsonIfExists(paths.pairingKeyFile);
  const pairingKey = pairingFile?.pairingKey;
  const direct = process.env.STUDIO_SESSION_TOKEN;
  let token = direct ?? "";
  const pair = async () => {
    const key = readJsonIfExists(paths.pairingKeyFile)?.pairingKey ?? pairingKey;
    if (!key) {
      throw new StudioError("UNAUTHENTICATED", `\u7F3A\u5C11\u914D\u5BF9\u5BC6\u94A5\u6587\u4EF6\uFF1A${paths.pairingKeyFile}\uFF08\u5148\u542F\u52A8\u4E00\u6B21\u540E\u53F0\u4EE5\u751F\u6210\uFF09`);
    }
    const response = await fetch(`${ensured.record.url}/v1/sessions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ pairingKey: key, clientName: options.clientName, scopes: options.scopes, channel: "mcp" }),
      signal: AbortSignal.timeout(1e4)
    });
    const body = await response.json();
    if (!response.ok || !body.token) throw new StudioError("UNAUTHENTICATED", body.error?.message ?? "\u914D\u5BF9\u5931\u8D25");
    return body.token;
  };
  if (!token) token = await pair();
  return new HttpApiClient(ensured.record.url, token, pair);
}
function toolResultToMcp(result) {
  const text2 = JSON.stringify(result, null, 2);
  return {
    content: [{ type: "text", text: text2 }],
    structuredContent: result,
    isError: result.ok === false
  };
}
function createToolCaller(client, clientName) {
  return async (name, args) => {
    const tool = toolByName(name);
    if (!tool) {
      return toolResultToMcp({ ok: false, tool: name, error: { code: "UNKNOWN_TOOL", message: `\u672A\u6CE8\u518C\u7684\u5DE5\u5177 ${name}` } });
    }
    try {
      const result = await tool.handler({ client, clientName }, args ?? {});
      return toolResultToMcp(result);
    } catch (error) {
      const studioError = error instanceof StudioError ? error : new StudioError("INTERNAL_ERROR", error.message);
      return toolResultToMcp({ ok: false, tool: name, error: { code: studioError.code, message: studioError.message, issues: studioError.issues, details: studioError.details } });
    }
  };
}
async function main() {
  const argv = process.argv.slice(2);
  const argValue = (flag, fallback) => {
    const index = argv.indexOf(flag);
    return index >= 0 && index + 1 < argv.length ? argv[index + 1] : fallback;
  };
  const options = {
    workspaceRoot: argValue("--workspace", defaultWorkspaceRoot()),
    autostart: process.env.STUDIO_AUTOSTART !== "0",
    clientName: argValue("--client-name", process.env.STUDIO_CLIENT_NAME ?? "mcp-client"),
    scopes: (process.env.STUDIO_SCOPES ?? "read,edit,generate,preview,export").split(",").map((scope) => scope.trim()).filter(Boolean)
  };
  log(`\u5DE5\u4F5C\u533A ${options.workspaceRoot}\uFF0C\u81EA\u52A8\u542F\u52A8=${options.autostart}\uFF0C\u5BA2\u6237\u7AEF ${options.clientName}`);
  let clientPromise = null;
  const getClient = async () => {
    if (!clientPromise) clientPromise = createSessionClient(options);
    try {
      return await clientPromise;
    } catch (error) {
      clientPromise = null;
      throw error;
    }
  };
  const callTool = async (name, args) => {
    let client;
    try {
      client = await getClient();
    } catch (error) {
      const studioError = error instanceof StudioError ? error : new StudioError("BACKEND_UNAVAILABLE", error.message);
      return toolResultToMcp({ ok: false, tool: name, error: { code: studioError.code, message: studioError.message, details: studioError.details } });
    }
    return createToolCaller(client, options.clientName)(name, args);
  };
  const handle = async (message) => {
    const { id, method, params } = message;
    switch (method) {
      case "initialize": {
        const requested = typeof params?.protocolVersion === "string" ? params.protocolVersion : "";
        const protocolVersion = MCP_PROTOCOL_VERSIONS.includes(requested) ? requested : MCP_PROTOCOL_VERSIONS[0];
        reply(id, {
          protocolVersion,
          capabilities: { tools: { listChanged: false } },
          serverInfo: SERVER_INFO,
          instructions: "\u62FE\u5149 Studio \u5236\u4F5C\u5DE5\u5177\uFF1A\u6240\u6709\u5236\u4F5C\u52A8\u4F5C\u901A\u8FC7\u540E\u53F0 API \u5B8C\u6210\u3002\u5148\u7528 studio_get_capabilities \u4E0E studio_open_project \u786E\u8BA4\u80FD\u529B\u4E0E\u4FEE\u8BA2\uFF0C\u518D\u7528\u4E8B\u52A1\u63D0\u4EA4\u4FEE\u6539\uFF1B\u91CD\u8BD5\u65F6\u590D\u7528\u540C\u4E00 requestId\u3002"
        });
        return;
      }
      case "notifications/initialized":
      case "notifications/cancelled":
        return;
      case "ping":
        reply(id, {});
        return;
      case "tools/list":
        reply(id, {
          tools: TOOL_DEFINITIONS.map((tool) => ({
            name: tool.name,
            title: tool.title,
            description: tool.description,
            inputSchema: tool.inputSchema,
            annotations: { readOnlyHint: tool.scopes.length === 1 && tool.scopes[0] === "read" }
          }))
        });
        return;
      case "tools/call": {
        const name = String(params?.name ?? "");
        const args = params?.arguments && typeof params.arguments === "object" ? params.arguments : {};
        reply(id, await callTool(name, args));
        return;
      }
      case "resources/list":
        reply(id, { resources: [] });
        return;
      case "prompts/list":
        reply(id, { prompts: [] });
        return;
      default:
        replyError(id, -32601, `\u4E0D\u652F\u6301\u7684\u65B9\u6CD5\uFF1A${method}`);
    }
  };
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on("line", (line) => {
    const text2 = line.trim();
    if (!text2) return;
    let message;
    try {
      message = JSON.parse(text2);
    } catch {
      replyError(null, -32700, "\u6D88\u606F\u4E0D\u662F\u6709\u6548 JSON");
      return;
    }
    void handle(message).catch((error) => {
      const studioError = error instanceof StudioError ? error : new StudioError("INTERNAL_ERROR", error.message);
      replyError(message.id ?? null, -32603, studioError.message, { code: studioError.code });
    });
  });
  rl.on("close", () => process.exit(0));
  try {
    const client = await getClient();
    log(`\u5DF2\u8FDE\u63A5\u540E\u53F0 ${client.baseUrl}\uFF08\u53D1\u73B0\u8BB0\u5F55\u5B58\u5728=${existsSync2(workspacePaths(options.workspaceRoot).runtimeJson)}\uFF09`);
  } catch (error) {
    log(`\u540E\u53F0\u6682\u4E0D\u53EF\u7528\uFF1A${error.message}\uFF08\u5DE5\u5177\u8C03\u7528\u5C06\u8FD4\u56DE BACKEND_UNAVAILABLE\uFF09`);
  }
  const discovery = readDiscovery(options.workspaceRoot);
  if (discovery) log(`\u53D1\u73B0\u8BB0\u5F55\uFF1A${discovery.url} pid=${discovery.pid}`);
}
var isMainModule = process.argv[1] ? path2.resolve(process.argv[1]) === path2.resolve(fileURLToPath2(import.meta.url)) : false;
if (isMainModule) {
  void main();
}
export {
  createSessionClient,
  createToolCaller,
  main as runMcpStdio
};

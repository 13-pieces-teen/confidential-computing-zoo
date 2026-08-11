#!/usr/bin/env node

import { createHash, randomBytes } from "node:crypto";
import { execFile, execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const mono = () => process.hrtime.bigint();
const ms = (start, end = mono()) => Number(end - start) / 1_000_000;
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

class TaskError extends Error {
  constructor(stage, errorClass, message, timeoutLimitMs = null) {
    super(message);
    this.stage = stage;
    this.errorClass = errorClass;
    this.timeoutLimitMs = timeoutLimitMs;
  }
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error(`invalid argument: ${key ?? ""}`);
    values[key.slice(2)] = value;
  }
  const required = (key) => {
    if (!values[key]) throw new Error(`--${key} is required`);
    return values[key];
  };
  const integer = (key, fallback, allowZero = false) => {
    const value = Number(values[key] ?? fallback);
    if (!Number.isInteger(value) || value < (allowZero ? 0 : 1)) throw new Error(`--${key} is invalid`);
    return value;
  };
  return {
    runId: required("run-id"),
    caseName: required("case"),
    unitId: required("unit-id"),
    promptsPath: required("prompts"),
    baseUrl: required("base-url").replace(/\/+$/, ""),
    configPath: values["openclaw-config"] ?? "/home/node/.openclaw/openclaw.json",
    warmupTasks: integer("warmup-tasks", 0, true),
    measuredTasks: integer("measured-tasks", 1),
    agentTimeoutMs: integer("agent-timeout-ms", 180_000),
    captureTimeoutMs: integer("capture-timeout-ms", 60_000),
    commitTimeoutMs: integer("commit-timeout-ms", 300_000),
    archiveTimeoutMs: integer("archive-timeout-ms", 300_000),
    capturePollMs: integer("capture-poll-ms", 1_000),
    archivePollMs: integer("archive-poll-ms", 2_000),
    startAtUnixMs: integer("start-at-unix-ms", 0, true),
  };
}

function safeId(value, name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value)) throw new Error(`${name} is invalid`);
  return value.toLowerCase();
}

export function taskIdentity(runId, caseName, unitId, sequence, nonce = randomBytes(16).toString("hex")) {
  if (!/^[0-9a-f]{32}$/.test(nonce)) throw new Error("nonce must be 128-bit lowercase hex");
  const suffix = [safeId(runId, "run-id"), safeId(caseName, "case"), safeId(unitId, "unit-id"), sequence].join("-");
  return {
    taskId: `e8-${suffix}`,
    nonce,
    marker: `ARGUS-E8-RESULT-${nonce}`,
    sessionKey: `argus-e8-${suffix}`,
  };
}

export function taskPrompt(sourceText, nonce) {
  return [
    "你正在完成一个受控评估任务。",
    "",
    "请阅读下面的材料，生成 150–250 字的中文摘要，并严格使用 `1.`、`2.`、`3.` 三行编号列出三条关键结论。",
    "不要调用额外工具，不要省略材料中的关键约束。",
    "请在回答最后单独一行输出由固定前缀 `ARGUS-E8-RESULT-` 与下面 nonce 无空格拼接得到的字符串。",
    "不要在正文中输出该字符串。",
    "",
    `nonce：${nonce}`,
    "",
    "材料：",
    sourceText,
  ].join("\n");
}

function messageText(message) {
  if (typeof message?.content === "string") return message.content;
  return (Array.isArray(message?.parts) ? message.parts : [])
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n");
}

export function validateAssistant(message, marker) {
  if (message?.role !== "assistant") throw new TaskError("session_isolation", "marker_in_input", "marker is not in an assistant message");
  const text = messageText(message).trimEnd();
  const lines = text.split(/\r?\n/u);
  if (lines.at(-1)?.trim() !== marker || text.split(marker).length !== 2) {
    throw new TaskError("response_validation", "invalid_marker", "marker must appear once on the final line");
  }
  const body = lines.slice(0, -1).join("\n").trim();
  const bodyChars = Array.from(body.replace(/\s/gu, "")).length;
  const conclusions = body.split(/\r?\n/u).map((line) => line.match(/^\s*([123])\.\s+\S/u)?.[1]).filter(Boolean);
  if (bodyChars < 150 || bodyChars > 250) throw new TaskError("response_validation", "invalid_length", `body length is ${bodyChars}`);
  if (conclusions.join(",") !== "1,2,3") throw new TaskError("response_validation", "invalid_conclusions", "expected 1./2./3. conclusion lines");
  return { text, bodyChars, conclusionCount: conclusions.length };
}

function unwrap(payload, label, stage) {
  if (payload?.status !== "ok" || payload.result === undefined || payload.result === null) {
    throw new TaskError(stage, "invalid_response", `${label} did not return status=ok`);
  }
  return payload.result;
}

export function findAssistantMarker(contextEntries, marker) {
  const matches = [];
  for (const entry of contextEntries) {
    const context = unwrap(entry.payload, "session context", "openviking_capture");
    for (const message of context.messages ?? []) {
      const text = messageText(message);
      if (!text.includes(marker)) continue;
      if (message.role !== "assistant") throw new TaskError("session_isolation", "marker_in_input", "complete marker appeared outside assistant output");
      matches.push({ entry, message });
    }
  }
  if (matches.length > 1) throw new TaskError("session_isolation", "duplicate_marker", "marker matched multiple assistant messages");
  if (matches.length === 0) return null;
  const match = matches[0];
  const validated = validateAssistant(match.message, marker);
  return {
    sessionId: match.entry.sessionId,
    messageId: match.message.id ?? null,
    messageDigest: createHash("sha256").update(JSON.stringify(match.message)).digest("hex"),
    ...validated,
  };
}

function pluginConfig(configPath) {
  const get = (path) => JSON.parse(execFileSync("openclaw", ["config", "get", path, "--json"], {
    encoding: "utf8",
    env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
  }).trim());
  const plugin = get("plugins.entries.openviking.config");
  if (get("plugins.slots.contextEngine") !== "openviking" || plugin?.mode !== "remote") {
    throw new Error("OpenViking remote context-engine plugin is not active");
  }
  return plugin;
}

function api(baseUrl, apiKey, actorPeer) {
  return async (path, { method = "GET", body, headers = {}, timeoutMs = 30_000, stage = "openviking_capture" } = {}) => {
    let response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method,
        body,
        headers: { "X-API-Key": apiKey, "X-OpenViking-Actor-Peer": actorPeer, ...headers },
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      const timeout = error?.name === "TimeoutError" || error?.name === "AbortError";
      throw new TaskError(stage, timeout ? "timeout" : "transport_error", String(error?.message ?? error), timeout ? timeoutMs : null);
    }
    const text = await response.text();
    if (!response.ok) {
      const errorClass = response.status === 429 ? "http_429" : response.status >= 500 ? "http_5xx" : "http_4xx";
      throw new TaskError(stage, errorClass, `${path} returned HTTP ${response.status}: ${text.slice(0, 256)}`);
    }
    return text ? JSON.parse(text) : {};
  };
}

async function listSessions(request) {
  const sessions = unwrap(await request("/api/v1/sessions"), "sessions", "openviking_capture");
  if (!Array.isArray(sessions)) throw new TaskError("openviking_capture", "invalid_response", "session list is not an array");
  return sessions;
}

async function capture(request, baseline, marker, state, pollMs) {
  while (!state.cancelled && mono() <= state.deadline) {
    const sessions = await listSessions(request);
    const contexts = [];
    for (const session of sessions) {
      const sessionId = String(session?.session_id ?? "");
      if (!sessionId || baseline.has(sessionId) || sessionId.startsWith("memory-store-")) continue;
      try {
        const payload = await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=128000`);
        contexts.push({ sessionId, payload });
      } catch (error) {
        if (String(error.message).includes("HTTP 404")) continue;
        throw error;
      }
    }
    const match = findAssistantMarker(contexts, marker);
    if (match) return { ...match, observedNs: mono(), observedUnixMs: Date.now() };
    await sleep(pollMs);
  }
  if (state.cancelled) throw new TaskError("openclaw_agent", "cancelled", "capture stopped after Agent failure");
  throw new TaskError("openviking_capture", "timeout", "assistant marker was not captured", state.captureTimeoutMs);
}

async function runAgent(config, identity, prompt) {
  let output;
  try {
    output = await execFileAsync("openclaw", [
      "agent", "--agent", "main", "--session-key", identity.sessionKey,
      "--message", prompt, "--timeout", String(Math.ceil(config.agentTimeoutMs / 1000)), "--json",
    ], {
      encoding: "utf8",
      env: { ...process.env, OPENCLAW_CONFIG_PATH: config.configPath },
      maxBuffer: 16 * 1024 * 1024,
      timeout: config.agentTimeoutMs + 5_000,
    });
  } catch (error) {
    const timeout = error?.killed || error?.code === "ETIMEDOUT";
    const message = String(error?.stderr || error?.message || error).slice(0, 256);
    const providerError = /\b429\b|rate.?limit|model provider|provider.*\b5\d\d\b/iu.test(message);
    throw new TaskError(providerError ? "openclaw_generation_provider" : "openclaw_agent", timeout ? "timeout" : "agent_error", message, timeout ? config.agentTimeoutMs : null);
  }
  let envelope;
  try { envelope = JSON.parse(output.stdout); } catch { throw new TaskError("openclaw_agent", "invalid_json", "Agent returned invalid JSON"); }
  if (envelope?.status !== "ok" || !envelope.runId || typeof envelope.result !== "object" || envelope.result === null || ![null, undefined, "", false].includes(envelope.error)) {
    throw new TaskError("openclaw_agent", "invalid_envelope", "Agent status/runId/result/error contract failed");
  }
  return envelope;
}

function archiveDigest(payload) {
  const overview = String(unwrap(payload, "session context", "openviking_archive").latest_archive_overview ?? "").trim();
  return overview ? createHash("sha256").update(overview).digest("hex") : null;
}

export function archiveIsReady(task, detail, contextPayload, baselineCount, baselineDigest) {
  const digest = archiveDigest(contextPayload);
  return task?.status === "completed"
    && Number(detail?.commit_count ?? 0) > baselineCount
    && digest
    && digest !== baselineDigest;
}

async function waitArchive(request, sessionId, taskId, baselineCount, baselineDigest, config) {
  const deadline = mono() + BigInt(config.archiveTimeoutMs) * 1_000_000n;
  while (mono() <= deadline) {
    const task = unwrap(await request(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { stage: "openviking_archive" }), "commit task", "openviking_archive");
    if (task.status === "failed") throw new TaskError("openviking_archive", "task_failed", "commit task failed");
    if (task.status === "completed") {
      const detail = unwrap(await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { stage: "openviking_archive" }), "session detail", "openviking_archive");
      const context = await request(`/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=128000`, { stage: "openviking_archive" });
      if (archiveIsReady(task, detail, context, baselineCount, baselineDigest)) return { readyNs: mono(), readyUnixMs: Date.now() };
    }
    await sleep(config.archivePollMs);
  }
  throw new TaskError("openviking_archive", "timeout", "archive did not complete", config.archiveTimeoutMs);
}

function emptyReceipt(config, identity, sequence, measured) {
  return {
    schema_version: "argus-e8-agent-task-v1",
    run_id: config.runId, case: config.caseName, unit_id: config.unitId, sequence, measured,
    task_id: identity.taskId, task_nonce: identity.nonce, response_marker: identity.marker, session_key: identity.sessionKey,
    status: "failed", failure_stage: null, error_class: null, error_message: null,
    openclaw_run_id: null, openviking_session_id: null, openviking_message_id: null,
    openviking_message_digest: null, openviking_task_id: null, openviking_archive_uri: null,
    clock_source: "monotonic", t0_monotonic_ns: null, t1_monotonic_ns: null,
    t2_monotonic_ns: null, t3_monotonic_ns: null, t4_monotonic_ns: null,
    started_unix_ms: null, agent_returned_unix_ms: null, capture_observed_unix_ms: null,
    commit_created_unix_ms: null, archive_ready_unix_ms: null, finished_unix_ms: null,
    agent_turn_ms: null, capture_first_observed_ms: null, commit_to_archive_ms: null,
    agent_task_e2e_ms: null, response_body_chars: null, response_conclusion_count: null,
    input_tokens: null, output_tokens: null, guard_evidence_mode: "case_level", guard_requests: [],
    timeout_limit_ms: null, elapsed_ms: null, transport_success_count: null,
  };
}

function usage(envelope) {
  const value = envelope?.result?.usage ?? envelope?.result?.meta?.usage ?? envelope?.usage ?? {};
  const number = (...keys) => keys.map((key) => Number(value[key])).find((item) => Number.isFinite(item) && item >= 0) ?? null;
  return { input: number("input_tokens", "inputTokens", "prompt_tokens"), output: number("output_tokens", "outputTokens", "completion_tokens") };
}

async function runTask(config, request, promptEntry, sequence, measured) {
  const identity = taskIdentity(config.runId, config.caseName, config.unitId, sequence);
  const receipt = emptyReceipt(config, identity, sequence, measured);
  let t0 = null;
  try {
    const prompt = taskPrompt(promptEntry.source_text, identity.nonce);
    if (prompt.includes(identity.marker)) throw new TaskError("setup", "marker_in_input", "complete marker leaked into prompt");
    const baseline = new Set((await listSessions(request)).map((session) => String(session.session_id ?? "")));
    t0 = mono();
    receipt.t0_monotonic_ns = t0.toString();
    receipt.started_unix_ms = Date.now();
    const state = {
      cancelled: false,
      captureTimeoutMs: config.captureTimeoutMs,
      deadline: t0 + BigInt(config.agentTimeoutMs + config.captureTimeoutMs) * 1_000_000n,
    };
    const capturePromise = capture(request, baseline, identity.marker, state, config.capturePollMs).then(
      (value) => ({ value, error: null }),
      (error) => ({ value: null, error }),
    );
    let agent;
    try { agent = await runAgent(config, identity, prompt); }
    catch (error) { state.cancelled = true; await capturePromise; throw error; }
    const t1 = mono();
    receipt.t1_monotonic_ns = t1.toString();
    receipt.agent_returned_unix_ms = Date.now();
    receipt.agent_turn_ms = ms(t0, t1);
    receipt.openclaw_run_id = agent.runId;
    state.deadline = t1 + BigInt(config.captureTimeoutMs) * 1_000_000n;
    const captureResult = await capturePromise;
    if (captureResult.error) throw captureResult.error;
    const captured = captureResult.value;
    const t2 = captured.observedNs;
    receipt.t2_monotonic_ns = t2.toString();
    receipt.capture_observed_unix_ms = captured.observedUnixMs;
    receipt.capture_first_observed_ms = ms(t0, t2);
    receipt.openviking_session_id = captured.sessionId;
    receipt.openviking_message_id = captured.messageId;
    receipt.openviking_message_digest = captured.messageDigest;
    receipt.response_body_chars = captured.bodyChars;
    receipt.response_conclusion_count = captured.conclusionCount;
    const tokenUsage = usage(agent);
    receipt.input_tokens = tokenUsage.input;
    receipt.output_tokens = tokenUsage.output;

    const detailBefore = unwrap(await request(`/api/v1/sessions/${encodeURIComponent(captured.sessionId)}`, { stage: "openviking_commit" }), "session detail", "openviking_commit");
    const contextBefore = await request(`/api/v1/sessions/${encodeURIComponent(captured.sessionId)}/context?token_budget=128000`, { stage: "openviking_commit" });
    const baselineCount = Number(detailBefore.commit_count ?? 0);
    const baselineDigest = archiveDigest(contextBefore);
    const commit = unwrap(await request(`/api/v1/sessions/${encodeURIComponent(captured.sessionId)}/commit`, {
      method: "POST", body: "{}", timeoutMs: config.commitTimeoutMs, stage: "openviking_commit",
      headers: { "Content-Type": "application/json", "X-Argus-Request-ID": `${identity.taskId}-commit` },
    }), "commit", "openviking_commit");
    if (commit.status !== "accepted" || !commit.archived || !commit.task_id) throw new TaskError("openviking_commit", "invalid_commit", "commit was skipped or returned no task ID");
    const t3 = mono();
    receipt.t3_monotonic_ns = t3.toString();
    receipt.commit_created_unix_ms = Date.now();
    receipt.openviking_task_id = String(commit.task_id);
    receipt.openviking_archive_uri = commit.archive_uri ?? null;
    const archived = await waitArchive(request, captured.sessionId, String(commit.task_id), baselineCount, baselineDigest, config);
    const t4 = archived.readyNs;
    receipt.t4_monotonic_ns = t4.toString();
    receipt.archive_ready_unix_ms = archived.readyUnixMs;
    receipt.finished_unix_ms = archived.readyUnixMs;
    receipt.commit_to_archive_ms = ms(t3, t4);
    receipt.agent_task_e2e_ms = ms(t0, t4);
    receipt.elapsed_ms = receipt.agent_task_e2e_ms;
    receipt.status = "completed";
  } catch (rawError) {
    const error = rawError instanceof TaskError ? rawError : new TaskError("unknown", "error", String(rawError?.message ?? rawError));
    receipt.failure_stage = error.stage;
    receipt.error_class = error.errorClass;
    receipt.error_message = error.message.replace(/[\r\n\t]+/g, " ").slice(0, 256);
    receipt.timeout_limit_ms = error.timeoutLimitMs;
    receipt.finished_unix_ms = Date.now();
    if (t0) receipt.elapsed_ms = ms(t0);
  }
  return receipt;
}

async function main() {
  const config = parseArgs(process.argv.slice(2));
  const promptsValue = JSON.parse(await readFile(config.promptsPath, "utf8"));
  const prompts = Array.isArray(promptsValue) ? promptsValue : promptsValue.prompts;
  if (!Array.isArray(prompts) || prompts.length === 0) throw new Error("prompt suite is empty");
  const plugin = pluginConfig(config.configPath);
  const pluginBase = String(plugin.baseUrl ?? "").replace(/\/+$/, "");
  if (pluginBase !== config.baseUrl) throw new Error(`OpenViking plugin targets ${pluginBase}, expected ${config.baseUrl}`);
  const apiKey = process.env.OPENVIKING_API_KEY || plugin.apiKey;
  if (!apiKey) throw new Error("OPENVIKING_API_KEY is required");
  const request = api(pluginBase, apiKey, plugin.peer_prefix ?? "main");
  if (config.startAtUnixMs > Date.now()) await sleep(config.startAtUnixMs - Date.now());
  const items = [
    ...Array.from({ length: config.warmupTasks }, (_, index) => ({ measured: false, sequence: `w${String(index + 1).padStart(3, "0")}` })),
    ...Array.from({ length: config.measuredTasks }, (_, index) => ({ measured: true, sequence: String(index + 1).padStart(3, "0") })),
  ];
  for (const [index, item] of items.entries()) {
    const receipt = await runTask(config, request, prompts[index % prompts.length], item.sequence, item.measured);
    process.stdout.write(`${JSON.stringify(receipt)}\n`);
    if (!item.measured && receipt.status !== "completed") break;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    process.stderr.write(`task-worker: ${String(error?.message ?? error).replace(/[\r\n]+/g, " ").slice(0, 256)}\n`);
    process.exitCode = 1;
  });
}

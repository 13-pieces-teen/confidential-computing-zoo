#!/usr/bin/env node

import { randomUUID, X509Certificate } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Agent as HTTPSAgent, request as httpsRequest } from "node:https";

const VALID_MODES = new Set([
  "guard",
  "guarded",
  "guarded-new-connection",
  "diagnostic-mtls-only",
]);

function parseArguments(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith("--")) throw new Error(`unexpected argument: ${argument}`);
    const name = argument.slice(2);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`missing value for --${name}`);
    values[name] = value;
    index += 1;
  }
  const positiveInteger = (name, fallback, allowZero = false) => {
    const raw = values[name] ?? `${fallback}`;
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isInteger(parsed) || (allowZero ? parsed < 0 : parsed <= 0)) {
      throw new Error(`--${name} must be ${allowZero ? "non-negative" : "positive"}`);
    }
    return parsed;
  };
  const positiveNumber = (name, fallback, allowZero = false) => {
    const raw = values[name] ?? `${fallback}`;
    const parsed = Number.parseFloat(raw);
    if (!Number.isFinite(parsed) || (allowZero ? parsed < 0 : parsed <= 0)) {
      throw new Error(`--${name} must be ${allowZero ? "non-negative" : "positive"}`);
    }
    return parsed;
  };
  const mode = values.mode ?? "guarded";
  if (!VALID_MODES.has(mode)) throw new Error(`unsupported --mode: ${mode}`);
  const failOnErrorRaw = values["fail-on-error"] ?? "0";
  if (!new Set(["0", "1"]).has(failOnErrorRaw)) {
    throw new Error("--fail-on-error must be 0 or 1");
  }
  let requests = positiveInteger("requests", 0, true);
  const durationSeconds = positiveNumber("duration-seconds", 0, true);
  if (requests === 0 && durationSeconds === 0) requests = 1000;
  return {
    mode,
    url: values.url ?? `${process.env.ARGUS_OPENVIKING_ORIGIN ?? ""}/health`,
    guardURL: values["guard-url"] ?? process.env.ARGUS_GUARD_URL ?? "",
    requests,
    durationSeconds,
    concurrency: positiveInteger("concurrency", 1),
    recordEvery: positiveInteger("record-every", 1),
    warmupRequests: positiveInteger("warmup-requests", 0, true),
    qps: positiveNumber("qps", 0, true),
    timeoutMs: positiveInteger("timeout-ms", 10000),
    method: (values.method ?? "GET").toUpperCase(),
    profile: values.profile ?? mode,
    failOnError: failOnErrorRaw === "1",
  };
}

function emit(record) {
  process.stdout.write(`${JSON.stringify(record)}\n`);
}

function sleep(milliseconds) {
  if (milliseconds <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function exactSPIFFEIdentity(expectedID, _hostname, peerCertificate) {
  const certificate = new X509Certificate(peerCertificate.raw);
  const identities = (certificate.subjectAltName ?? "")
    .split(/,\s*/u)
    .filter((entry) => entry.startsWith("URI:"))
    .map((entry) => entry.slice(4));
  if (identities.length !== 1 || identities[0] !== expectedID) {
    return new Error(
      `peer SPIFFE ID mismatch: expected ${expectedID}, got ${identities.join(",") || "none"}`,
    );
  }
  return undefined;
}

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function loadCredentials() {
  const directory = process.env.ARGUS_SPIFFE_CREDENTIAL_DIR ?? "/run/argus-svid";
  const [cert, key, ca] = await Promise.all([
    readFile(`${directory}/svid.pem`),
    readFile(`${directory}/svid-key.pem`),
    readFile(`${directory}/bundle.pem`),
  ]);
  return { cert, key, ca };
}

function guardRequestBody(targetURL) {
  const targetOrigin = new URL(requiredEnvironment("ARGUS_OPENVIKING_ORIGIN")).origin;
  if (targetURL.origin !== targetOrigin) {
    throw new Error(`benchmark target origin ${targetURL.origin} does not match ${targetOrigin}`);
  }
  return {
    request_id: randomUUID(),
    caller_spiffe_id: requiredEnvironment("ARGUS_CALLER_SPIFFE_ID"),
    target_spiffe_id: requiredEnvironment("ARGUS_TARGET_SPIFFE_ID"),
    target_service: requiredEnvironment("ARGUS_TARGET_SERVICE"),
    target_origin: targetOrigin,
    operation: process.env.ARGUS_BENCHMARK_OPERATION ?? "memory.read",
    data_class: process.env.ARGUS_GUARD_DATA_CLASS ?? "sensitive",
  };
}

async function authorize(guardURL, targetURL, timeoutMs) {
  const request = guardRequestBody(targetURL);
  const response = await fetch(guardURL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
    redirect: "error",
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) throw new Error(`Guard returned HTTP ${response.status}`);
  const decision = await response.json();
  if (
    decision.request_id !== request.request_id
    || decision.decision !== "ALLOW"
    || typeof decision.decision_id !== "string"
    || !decision.decision_id
    || typeof decision.rule_id !== "string"
    || !decision.rule_id
    || !Number.isInteger(decision.expires_at_unix)
    || decision.expires_at_unix <= Math.floor(Date.now() / 1000)
  ) {
    throw new Error(`Guard denied or returned an invalid decision: ${decision.reason ?? "unknown"}`);
  }
  return decision;
}

function directMTLSRequest(targetURL, options) {
  return new Promise((resolve, reject) => {
    const started = process.hrtime.bigint();
    let handshakeMs = null;
    const request = httpsRequest(targetURL, {
      method: options.method,
      cert: options.credentials.cert,
      key: options.credentials.key,
      ca: options.credentials.ca,
      servername: targetURL.hostname,
      rejectUnauthorized: true,
      checkServerIdentity: (hostname, certificate) =>
        exactSPIFFEIdentity(options.expectedSPIFFEID, hostname, certificate),
      agent: options.agent,
      headers: options.agent === false ? { connection: "close" } : undefined,
    }, (response) => {
      let responseBytes = 0;
      response.on("data", (chunk) => { responseBytes += chunk.length; });
      response.on("end", () => resolve({
        status: response.statusCode ?? 0,
        responseBytes,
        handshakeMs,
        reusedConnection: request.reusedSocket ?? false,
      }));
      response.on("error", reject);
    });
    request.on("socket", (socket) => {
      socket.once("secureConnect", () => {
        handshakeMs = Number(process.hrtime.bigint() - started) / 1_000_000;
      });
    });
    request.setTimeout(options.timeoutMs, () => {
      request.destroy(new Error(`mTLS request timed out after ${options.timeoutMs}ms`));
    });
    request.on("error", reject);
    request.end();
  });
}

async function createExecutor(configuration) {
  const targetURL = new URL(configuration.url);
  const guardURL = new URL(configuration.guardURL);
  if (targetURL.protocol !== "https:") throw new Error("--url must use HTTPS");
  if (!new Set(["http:", "https:"]).has(guardURL.protocol)) {
    throw new Error("--guard-url must use HTTP or HTTPS");
  }

  if (configuration.mode === "guard") {
    return async () => {
      const decision = await authorize(guardURL, targetURL, configuration.timeoutMs);
      return { status: 200, responseBytes: 0, decisionID: decision.decision_id };
    };
  }

  if (configuration.mode === "guarded") {
    return async () => {
      const response = await fetch(targetURL, {
        method: configuration.method,
        redirect: "error",
        signal: AbortSignal.timeout(configuration.timeoutMs),
      });
      const responseBytes = (await response.arrayBuffer()).byteLength;
      if (!response.ok) throw new Error(`OpenViking returned HTTP ${response.status}`);
      return { status: response.status, responseBytes };
    };
  }

  const expectedSPIFFEID = requiredEnvironment("ARGUS_TARGET_SPIFFE_ID");
  const keepAliveAgent = configuration.mode === "diagnostic-mtls-only"
    ? new HTTPSAgent({ keepAlive: true, maxSockets: configuration.concurrency })
    : undefined;
  return async () => {
    let decisionID;
    if (configuration.mode === "guarded-new-connection") {
      const decision = await authorize(guardURL, targetURL, configuration.timeoutMs);
      decisionID = decision.decision_id;
    }
    // Re-read the SVID before every new mTLS connection. The workload SVID
    // materializer rotates /run/argus-svid on a ~TTL/2 cadence; credentials
    // read once at executor start expire after a single lifetime and every
    // subsequent fresh handshake then fails (the peer rejects the expired
    // client certificate). Refreshing here keeps rotation-spanning runs valid.
    const credentials = await loadCredentials();
    const result = await directMTLSRequest(targetURL, {
      method: configuration.method,
      credentials,
      expectedSPIFFEID,
      timeoutMs: configuration.timeoutMs,
      agent: configuration.mode === "guarded-new-connection" ? false : keepAliveAgent,
    });
    if (result.status < 200 || result.status >= 300) {
      throw new Error(`OpenViking returned HTTP ${result.status}`);
    }
    return { ...result, decisionID };
  };
}

async function runRequest(index, configuration, execute) {
  const startedUnixMs = Date.now();
  const started = process.hrtime.bigint();
  try {
    const result = await execute();
    if (index % configuration.recordEvery === 0) emit({
      schema_version: "argus-benchmark-request-v1",
      type: "request",
      experiment_profile: configuration.profile,
      request_index: index,
      started_unix_ms: startedUnixMs,
      duration_ms: Number(process.hrtime.bigint() - started) / 1_000_000,
      ok: true,
      status: result.status,
      response_bytes: result.responseBytes,
      handshake_ms: result.handshakeMs ?? null,
      reused_connection: result.reusedConnection ?? null,
      decision_id: result.decisionID ?? null,
      error: null,
    });
    return true;
  } catch (error) {
    emit({
      schema_version: "argus-benchmark-request-v1",
      type: "request",
      experiment_profile: configuration.profile,
      request_index: index,
      started_unix_ms: startedUnixMs,
      duration_ms: Number(process.hrtime.bigint() - started) / 1_000_000,
      ok: false,
      status: null,
      response_bytes: 0,
      handshake_ms: null,
      reused_connection: null,
      decision_id: null,
      error: error?.message ?? String(error),
    });
    return false;
  }
}

export async function run(configuration) {
  const execute = await createExecutor(configuration);
  for (let index = 0; index < configuration.warmupRequests; index += 1) {
    await execute();
  }
  const startedUnixMs = Date.now();
  const started = process.hrtime.bigint();
  const deadline = configuration.durationSeconds > 0
    ? started + BigInt(Math.round(configuration.durationSeconds * 1_000_000_000))
    : null;
  const active = new Set();
  let launched = 0;
  let succeeded = 0;

  const launch = (index) => {
    const task = runRequest(index, configuration, execute)
      .then((ok) => { if (ok) succeeded += 1; })
      .finally(() => active.delete(task));
    active.add(task);
  };

  while (
    (configuration.requests === 0 || launched < configuration.requests)
    && (deadline === null || process.hrtime.bigint() < deadline)
  ) {
    if (configuration.qps > 0) {
      const due = started + BigInt(Math.round((launched / configuration.qps) * 1_000_000_000));
      const waitMs = Number(due - process.hrtime.bigint()) / 1_000_000;
      await sleep(waitMs);
    }
    while (active.size >= configuration.concurrency) await Promise.race(active);
    if (deadline !== null && process.hrtime.bigint() >= deadline) break;
    launch(launched);
    launched += 1;
  }
  await Promise.all(active);
  const durationSeconds = Number(process.hrtime.bigint() - started) / 1_000_000_000;
  const summary = {
    schema_version: "argus-benchmark-summary-v1",
    type: "summary",
    experiment_profile: configuration.profile,
    started_unix_ms: startedUnixMs,
    duration_seconds: durationSeconds,
    requested_qps: configuration.qps,
    achieved_qps: durationSeconds > 0 ? launched / durationSeconds : 0,
    concurrency: configuration.concurrency,
    warmup_requests: configuration.warmupRequests,
    requests: launched,
    succeeded,
    failed: launched - succeeded,
  };
  emit(summary);
  return summary;
}

if (process.env.ARGUS_SPIFFE_TELEMETRY === "1") {
  process.on("argus:spiffe-telemetry", (record) => emit(record));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const configuration = parseArguments(process.argv.slice(2));
    const summary = await run(configuration);
    if (configuration.failOnError && summary.failed > 0) process.exitCode = 1;
  } catch (error) {
    process.stderr.write(`Argus benchmark load generator: ${error?.stack ?? error}\n`);
    process.exitCode = 2;
  }
}

export { parseArguments };

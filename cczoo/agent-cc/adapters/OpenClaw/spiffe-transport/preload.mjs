import { randomUUID, X509Certificate } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { Agent, fetch as undiciFetch } from "undici";

const originalFetch = globalThis.fetch.bind(globalThis);
let dispatcherCache;

function elapsedMilliseconds(started, completed) {
  return Number(completed - started) / 1_000_000;
}

function emitTelemetry(event) {
  if (process.env.ARGUS_SPIFFE_TELEMETRY !== "1") return;
  process.emit("argus:spiffe-telemetry", Object.freeze({
    schema_version: "argus-spiffe-transport-v1",
    ...event,
  }));
}

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required when ARGUS_SPIFFE_ENABLED=1`);
  }
  return value;
}

export function canonicalOrigin(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new Error("OpenViking origin must be an HTTPS origin");
  }
  if (parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("OpenViking origin must not contain path, query, or fragment");
  }
  return parsed.origin;
}

export function uriSANs(certificate) {
  const x509 = certificate instanceof X509Certificate
    ? certificate
    : new X509Certificate(certificate.raw);
  return (x509.subjectAltName ?? "")
    .split(/,\s*/u)
    .filter((entry) => entry.startsWith("URI:"))
    .map((entry) => entry.slice(4));
}

export function checkExactSPIFFEID(expectedID, _hostname, certificate) {
  const identities = uriSANs(certificate);
  if (identities.length !== 1 || identities[0] !== expectedID) {
    return new Error(
      `OpenViking peer SPIFFE ID mismatch: expected ${expectedID}, got ${identities.join(",") || "none"}`,
    );
  }
  return undefined;
}

function loadOperationMap(raw) {
  if (!raw) return [];
  const parsed = JSON.parse(raw);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("ARGUS_GUARD_OPERATION_MAP_JSON must be a JSON object");
  }
  return Object.entries(parsed)
    .map(([prefix, operation]) => {
      if (!prefix.startsWith("/") || typeof operation !== "string" || !operation) {
        throw new Error("Guard operation map entries must be path-prefix to operation strings");
      }
      return [prefix, operation];
    })
    .sort((left, right) => right[0].length - left[0].length);
}

export function operationFor(method, pathname, operationMap = []) {
  const mapped = operationMap.find(([prefix]) => pathname.startsWith(prefix));
  if (mapped) return mapped[1];
  switch (method.toUpperCase()) {
    case "GET":
    case "HEAD":
      return "memory.read";
    case "DELETE":
      return "memory.delete";
    default:
      return "memory.write";
  }
}

async function credentialFingerprint(directory) {
  const names = ["svid.pem", "svid-key.pem", "bundle.pem"];
  const metadata = await Promise.all(names.map((name) => stat(`${directory}/${name}`)));
  return metadata.map((item) => `${item.ino}:${item.size}:${item.mtimeMs}`).join("|");
}

async function dispatcherFor(configuration) {
  const fingerprint = await credentialFingerprint(configuration.credentialDirectory);
  if (dispatcherCache?.fingerprint === fingerprint) return dispatcherCache.dispatcher;

  const [certificate, privateKey, bundle] = await Promise.all([
    readFile(`${configuration.credentialDirectory}/svid.pem`),
    readFile(`${configuration.credentialDirectory}/svid-key.pem`),
    readFile(`${configuration.credentialDirectory}/bundle.pem`),
  ]);
  const dispatcher = new Agent({
    connect: {
      cert: certificate,
      key: privateKey,
      ca: bundle,
      servername: new URL(configuration.targetOrigin).hostname,
      rejectUnauthorized: true,
      checkServerIdentity: (hostname, peerCertificate) =>
        checkExactSPIFFEID(configuration.targetSPIFFEID, hostname, peerCertificate),
    },
    connections: 2,
    pipelining: 1,
    keepAliveTimeout: configuration.keepAliveTimeoutMs,
    keepAliveMaxTimeout: configuration.keepAliveMaxTimeoutMs,
  });
  const previous = dispatcherCache?.dispatcher;
  dispatcherCache = { fingerprint, dispatcher };
  if (previous) await previous.close();
  return dispatcher;
}

async function authorize(configuration, requestURL, method) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), configuration.guardTimeoutMs);
  timeout.unref?.();
  const requestID = randomUUID();
  let response;
  try {
    const guardAPIToken = (await readFile(configuration.guardAPITokenFile, "utf8"))
      .replace(/[\r\n]+$/u, "");
    if (!guardAPIToken || /\s/u.test(guardAPIToken)) {
      throw new Error("Argus Guard API token is empty or contains whitespace");
    }
    try {
      response = await originalFetch(configuration.guardURL, {
        method: "POST",
        headers: {
          authorization: `Bearer ${guardAPIToken}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          request_id: requestID,
          caller_spiffe_id: configuration.callerSPIFFEID,
          target_spiffe_id: configuration.targetSPIFFEID,
          target_service: configuration.targetService,
          target_origin: configuration.targetOrigin,
          operation: operationFor(method, requestURL.pathname, configuration.operationMap),
          data_class: configuration.dataClass,
        }),
        redirect: "error",
        signal: controller.signal,
      });
    } catch (error) {
      throw new Error(`Argus Guard request failed: ${error?.message ?? error}`, { cause: error });
    }
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    throw new Error(`Argus Guard unavailable: HTTP ${response.status}`);
  }
  let decision;
  try {
    decision = await response.json();
  } catch (error) {
    throw new Error(`Argus Guard returned malformed JSON: ${error?.message ?? error}`, { cause: error });
  }
  if (
    decision?.request_id !== requestID
    || decision?.decision !== "ALLOW"
    || typeof decision?.decision_id !== "string"
    || !decision.decision_id
    || typeof decision?.policy_id !== "string"
    || !decision.policy_id
    || typeof decision?.rule_id !== "string"
    || !decision.rule_id
    || !Number.isInteger(decision?.expires_at_unix)
    || decision.expires_at_unix <= Math.floor(Date.now() / 1000)
    || decision.expires_at_unix > Math.floor(Date.now() / 1000) + 300
  ) {
    throw new Error(`Argus Guard denied or returned an invalid decision: ${decision?.reason ?? "unknown"}`);
  }
  return decision;
}

export function configurationFromEnvironment() {
  const targetOrigin = canonicalOrigin(requiredEnvironment("ARGUS_OPENVIKING_ORIGIN"));
  const guardURL = new URL(requiredEnvironment("ARGUS_GUARD_URL"));
  if (!new Set(["http:", "https:"]).has(guardURL.protocol)) {
    throw new Error("ARGUS_GUARD_URL must use HTTP or HTTPS");
  }
  const integer = (name, fallback) => {
    const value = Number.parseInt(process.env[name] ?? `${fallback}`, 10);
    if (!Number.isInteger(value) || value <= 0) throw new Error(`${name} must be positive`);
    return value;
  };
  return {
    targetOrigin,
    guardURL: guardURL.toString(),
    guardAPITokenFile: requiredEnvironment("ARGUS_GUARD_API_TOKEN_FILE"),
    callerSPIFFEID: requiredEnvironment("ARGUS_CALLER_SPIFFE_ID"),
    targetSPIFFEID: requiredEnvironment("ARGUS_TARGET_SPIFFE_ID"),
    targetService: requiredEnvironment("ARGUS_TARGET_SERVICE"),
    credentialDirectory: process.env.ARGUS_SPIFFE_CREDENTIAL_DIR ?? "/run/argus-svid",
    dataClass: process.env.ARGUS_GUARD_DATA_CLASS ?? "sensitive",
    operationMap: loadOperationMap(process.env.ARGUS_GUARD_OPERATION_MAP_JSON),
    guardTimeoutMs: integer("ARGUS_GUARD_TIMEOUT_MS", 2000),
    keepAliveTimeoutMs: integer("ARGUS_SPIFFE_KEEPALIVE_TIMEOUT_MS", 10000),
    keepAliveMaxTimeoutMs: integer("ARGUS_SPIFFE_KEEPALIVE_MAX_TIMEOUT_MS", 30000),
  };
}

export function installSPIFFETransport(configuration = configurationFromEnvironment()) {
  globalThis.fetch = async function argusSPIFFEFetch(input, init = {}) {
    const requestURL = new URL(
      typeof input === "string" || input instanceof URL ? input : input.url,
    );
    if (requestURL.origin !== configuration.targetOrigin) {
      return originalFetch(input, init);
    }
    const method = init.method
      ?? (typeof input === "object" && input.method ? input.method : undefined)
      ?? "GET";
    const g0 = process.hrtime.bigint();
    let stage = "guard";
    let decision;
    try {
      decision = await authorize(configuration, requestURL, method);
      const g1 = process.hrtime.bigint();
      stage = "credentials";
      const dispatcher = await dispatcherFor(configuration);
      const g2 = process.hrtime.bigint();
      stage = "transport";
      const response = await undiciFetch(input, {
        ...init,
        dispatcher,
        redirect: "manual",
      });
      const g3 = process.hrtime.bigint();
      if (response.status >= 300 && response.status < 400) {
        await response.body?.cancel();
        throw new Error("OpenViking redirect rejected: SPIFFE target origin is fixed");
      }
      emitTelemetry({
        type: "spiffe_transport",
        outcome: "response_headers",
        request_id: decision.request_id,
        decision_id: decision.decision_id,
        method: method.toUpperCase(),
        target_path: requestURL.pathname,
        status: response.status,
        guard_decision_ms: elapsedMilliseconds(g0, g1),
        guard_to_send_ms: elapsedMilliseconds(g1, g2),
        transport_headers_ms: elapsedMilliseconds(g2, g3),
        guarded_request_headers_ms: elapsedMilliseconds(g0, g3),
      });
      return response;
    } catch (error) {
      emitTelemetry({
        type: "spiffe_transport",
        outcome: "error",
        stage,
        request_id: decision?.request_id ?? null,
        decision_id: decision?.decision_id ?? null,
        method: method.toUpperCase(),
        target_path: requestURL.pathname,
        elapsed_ms: elapsedMilliseconds(g0, process.hrtime.bigint()),
        error: error?.message ?? String(error),
      });
      throw error;
    }
  };
}

if (process.env.ARGUS_SPIFFE_ENABLED === "1") {
  installSPIFFETransport();
}

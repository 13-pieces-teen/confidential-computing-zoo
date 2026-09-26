import { readFileSync, lstatSync, constants, openSync, closeSync, fstatSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { Agent, request as httpsRequest } from 'node:https';
import { Readable } from 'node:stream';
import { randomUUID } from 'node:crypto';
import { recordRecallRequest } from './recall-audit.mjs';
import { validateMaterial, validateSVID, spiffeID } from './svid.mjs';

const receipts = new WeakMap();
const failures = new WeakMap();
const fail = message => new Error(`OpenViking SPIFFE: ${message}`);

function protectedFile(path, limit) {
  const flags = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0);
  const fd = openSync(path, flags);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.size > limit || (process.platform !== 'win32' && (stat.mode & 0o022))) {
      throw fail('credential/config file is not a protected regular file');
    }
    return readFileSync(fd);
  } finally { closeSync(fd); }
}

export function loadConfig(path = process.env.OPENVIKING_SPIFFE_CONFIG || '/etc/argus-openclaw/client.json') {
  if (!isAbsolute(path)) throw fail('config path must be absolute');
  return validateConfig(JSON.parse(protectedFile(path, 16384)));
}

function validateConfig(value) {
  const allowed = new Set(['origin', 'clientSpiffeId', 'serverSpiffeId', 'credentialsDir', 'requestTimeoutMs']);
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !allowed.has(key))) throw fail('unknown configuration');
  const url = new URL(value.origin);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw fail('origin must be a single HTTPS origin');
  if (typeof value.credentialsDir !== 'string' || !isAbsolute(value.credentialsDir)) throw fail('credentialsDir must be absolute');
  spiffeID(value.clientSpiffeId);
  spiffeID(value.serverSpiffeId);
  if (value.clientSpiffeId === value.serverSpiffeId) throw fail('client and server identities must differ');
  const requestTimeoutMs = value.requestTimeoutMs ?? 120000;
  if (!Number.isInteger(requestTimeoutMs) || requestTimeoutMs < 100 || requestTimeoutMs > 300000) throw fail('invalid requestTimeoutMs');
  return Object.freeze({ ...value, origin: url.origin, requestTimeoutMs });
}

export function requestIdentity(response) { return receipts.get(response); }
// Narrow observation for availability experiments. A failed request is not
// evidence that the peer received no bytes, and timeouts remain inconclusive.
export function requestFailure(error, response) {
  const failure = failures.get(error);
  if (failure) return failure;
  const receipt = receipts.get(response);
  if (receipt && error?.code === 'ECONNRESET') return {
    request_id: receipt.request_id, network_error: error.code, phase: 'response_body',
  };
}

// No global dispatcher or global fetch changes: only this plugin's fixed origin
// can use the credentials. Every TLS handshake still uses OpenSSL path validation.
export function createSpiffeTransport(configuration = loadConfig()) {
  const config = validateConfig(configuration);
  let state;
  let closed = false;
  const generations = new Set();
  function retire(generation, immediate) {
    if (immediate) {
      clearTimeout(generation.drainTimer);
      generation.agent.destroy();
      for (const socket of generation.sockets) socket.destroy(fail('identity unavailable or retired'));
      generations.delete(generation);
    } else {
      for (const sockets of Object.values(generation.agent.freeSockets)) for (const socket of sockets) socket.destroy();
      generation.drainTimer = setTimeout(() => retire(generation, true), 5000);
      generation.drainTimer.unref();
    }
  }
  function clear() {
    state = undefined;
    for (const generation of [...generations]) retire(generation, true);
  }
  function refresh() {
    if (closed) throw fail('transport is closed');
    try {
      const dirStat = lstatSync(config.credentialsDir);
      if (!dirStat.isDirectory() || dirStat.isSymbolicLink() || (process.platform !== 'win32' && (dirStat.mode & 0o022))) throw fail('unsafe credentials directory');
      const lease = JSON.parse(protectedFile(join(config.credentialsDir, 'ready.json'), 4096));
      const now = Date.now();
      if (lease.version !== 1 || !/^generation-[a-z0-9-]+$/.test(lease.generation ?? '')
          || !/^[a-f0-9]+$/.test(lease.serial ?? '') || !Number.isSafeInteger(lease.lease_until)
          || lease.lease_until <= now || lease.lease_until > now + 5000
          || !Number.isSafeInteger(lease.expires_at) || lease.expires_at <= now) throw fail('credential lease missing, stale, or expired');
      if (state?.generation === lease.generation) {
        if (BigInt(`0x${lease.serial}`) !== BigInt(`0x${state.serial}`) || lease.expires_at !== state.expires) throw fail('credential generation changed in place');
        if (now >= state.expires) throw fail('credentials expired');
        return state;
      }
      const directory = join(config.credentialsDir, lease.generation);
      const stat = lstatSync(directory);
      if (!stat.isDirectory() || stat.isSymbolicLink() || (process.platform !== 'win32' && (stat.mode & 0o022))) throw fail('unsafe credential generation');
      const cert = protectedFile(join(directory, 'svid.pem'), 1 << 20);
      const key = protectedFile(join(directory, 'key.pem'), 65536);
      const ca = protectedFile(join(directory, 'bundle.pem'), 1 << 20);
      const material = validateMaterial(cert, key, ca, config.clientSpiffeId);
      const after = JSON.parse(protectedFile(join(config.credentialsDir, 'ready.json'), 4096));
      if (after.generation !== lease.generation || after.serial !== lease.serial || after.expires_at !== lease.expires_at
          || BigInt(`0x${lease.serial}`) !== BigInt(`0x${material.serial}`) || lease.expires_at !== material.expires) throw fail('credential snapshot changed during read');
      const current = {
        ...material, generation: lease.generation, sockets: new Set(),
        agent: new Agent({
          keepAlive: true, maxSockets: 16, maxFreeSockets: 4, maxCachedSessions: 0,
          cert, key, ca, minVersion: 'TLSv1.2', rejectUnauthorized: true,
          checkServerIdentity: (_hostname, peer) => {
            try { validateSVID(peer.raw, config.serverSpiffeId); } catch (error) { return error; }
          },
        }),
      };
      if (state) retire(state, false);
      state = current;
      generations.add(current);
      return current;
    } catch (error) { clear(); throw error; }
  }
  const monitor = setInterval(() => { try { refresh(); } catch { /* requests surface the error; old sockets are already closed */ } }, 250);
  monitor.unref();

  const transport = async (input, init = {}) => {
    const url = new URL(input);
    if (url.origin !== config.origin || url.protocol !== 'https:' || url.username || url.password || url.hash) throw fail('request is outside the configured HTTPS origin');
    const current = refresh();
    const message = new Request(url, init);
    const headers = Object.fromEntries(message.headers);
    // Preserve fetch's body representation while avoiding implicit compression or
    // forwarding host/connection headers supplied by business callers.
    delete headers.host;
    delete headers.connection;
    headers['accept-encoding'] = 'identity';
    const requestID = randomUUID();
    headers['x-argus-request-id'] = requestID;
    const signal = AbortSignal.any([message.signal, AbortSignal.timeout(config.requestTimeoutMs)]);
    return new Promise((resolve, reject) => {
      const request = httpsRequest(url, { method: message.method, headers, agent: current.agent, signal }, response => {
        const status = response.statusCode;
        if (status >= 300 && status < 400) {
          response.destroy();
          reject(fail('HTTP redirects are forbidden'));
          return;
        }
        const peer = response.socket.getPeerCertificate();
        let server;
        try { server = validateSVID(peer.raw, config.serverSpiffeId); }
        catch (error) { response.destroy(); reject(error); return; }
        const responseHeaders = new Headers();
        for (let index = 0; index < response.rawHeaders.length; index += 2) responseHeaders.append(response.rawHeaders[index], response.rawHeaders[index + 1]);
        if (responseHeaders.has('content-encoding') && responseHeaders.get('content-encoding') !== 'identity') {
          response.destroy(); reject(fail('unexpected compressed response')); return;
        }
        const empty = message.method === 'HEAD' || [204, 205, 304].includes(status);
        const result = new Response(empty ? null : Readable.toWeb(response), { status, statusText: response.statusMessage, headers: responseHeaders });
        if (empty) response.resume();
        const receipt = { component: 'argus-openclaw-spiffe', request_id: requestID, method: message.method, path: url.pathname,
          client_spiffe_id: config.clientSpiffeId, server_spiffe_id: config.serverSpiffeId,
          client_serial: current.serial, server_serial: server.serial, generation: current.generation,
          http_status: status, checked_at: new Date().toISOString() };
        recordRecallRequest(receipt);
        receipts.set(result, receipt);
        // No key, PEM, API key, request body, query string or response body.
        console.error(JSON.stringify(receipt));
        resolve(result);
      });
      request.on('socket', socket => {
        if (!current.sockets.has(socket)) {
          current.sockets.add(socket);
          socket.once('close', () => current.sockets.delete(socket));
          const boundLifetime = () => {
            try {
              const server = validateSVID(socket.getPeerCertificate().raw, config.serverSpiffeId);
              const timer = setTimeout(() => socket.destroy(fail('TLS identity expired')), Math.max(1, Math.min(2147483647, Math.min(current.expires, server.expires) - Date.now())));
              timer.unref();
              socket.once('close', () => clearTimeout(timer));
            } catch (error) { socket.destroy(error); }
          };
          if (socket.connecting) socket.once('secureConnect', boundLifetime); else boundLifetime();
        }
      });
      request.on('error', error => {
        if (error.code === 'ECONNREFUSED' || error.code === 'ECONNRESET') failures.set(error, {
          request_id: requestID, network_error: error.code, phase: 'https_request',
        });
        reject(error);
      });
      if (message.body) {
        const body = Readable.fromWeb(message.body);
        body.on('error', error => request.destroy(error));
        request.once('close', () => body.destroy());
        body.pipe(request);
      } else request.end();
    });
  };
  transport.close = () => { closed = true; clearInterval(monitor); clear(); };
  transport.check = () => {
    const value = refresh();
    return { client_spiffe_id: value.id, client_serial: value.serial, generation: value.generation, expires_at: value.expires, origin: config.origin };
  };
  transport.config = config;
  return transport;
}

let shared;
export function spiffeFetch(url, init) {
  shared ??= createSpiffeTransport();
  return shared(url, init);
}

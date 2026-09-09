import { createSpiffeTransport, requestIdentity } from './transport.mjs';

const transport = createSpiffeTransport();
try {
  const [command = 'health', path = '/health'] = process.argv.slice(2);
  if (command === 'watch') {
    const seconds = Number(process.argv[4] ?? '360');
    const expected = process.argv[5] ?? '';
    if (!Number.isFinite(seconds) || seconds < 1 || seconds > 3600) throw new Error('watch duration must be 1..3600 seconds');
    const end = Date.now() + seconds * 1000;
    while (Date.now() < end) {
      const record = {started_at_ms: Date.now(), ready: false, ok: false};
      try {
        Object.assign(record, transport.check());
        record.ready = true;
        const response = await transport(new URL(path, transport.config.origin), {
          headers: process.env.OPENVIKING_API_KEY ? {'X-API-Key': process.env.OPENVIKING_API_KEY} : {},
          signal: AbortSignal.timeout(1000),
        });
        const body = await response.text();
        Object.assign(record, requestIdentity(response));
        record.ok = response.ok && (!expected || body.includes(expected));
        if (!record.ok) record.error = 'HTTP failure or expected readback marker missing';
      } catch (error) { record.error = error.message; }
      // Record readiness again after I/O; failure of business TCP alone may keep it true.
      try { transport.check(); record.ready = true; } catch { record.ready = false; }
      record.completed_at_ms = Date.now();
      console.log(JSON.stringify(record));
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  } else if (command === 'check') {
    console.log(JSON.stringify(transport.check()));
  } else if (command === 'health' || command === 'request') {
    const response = await transport(new URL(path, transport.config.origin), {
      headers: process.env.OPENVIKING_API_KEY ? { 'X-API-Key': process.env.OPENVIKING_API_KEY } : {},
    });
    const body = await response.text();
    if (!response.ok) throw new Error(`OpenViking HTTP ${response.status}`);
    console.log(JSON.stringify({ result: 'PASS', ...requestIdentity(response), ...(command === 'request' ? { body } : {}) }));
  } else throw new Error('Expected check, health/request [path], or watch [path] [seconds] [expected-text]');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally { transport.close(); }

import { X509Certificate, createPrivateKey } from 'node:crypto';

export function spiffeID(value) {
  if (typeof value !== 'string' || !/^spiffe:\/\/[a-z0-9._-]+(?:\/[a-zA-Z0-9._-]+)+$/.test(value)
      || value.slice(9).split('/').some(part => part === '.' || part === '..')) {
    throw new Error('Expected a canonical workload SPIFFE ID');
  }
  return value;
}

// Only decode the DER extensions needed for the SPIFFE leaf profile. Certificate
// signatures, path building, constraints and trust are verified by Node/OpenSSL
// during TLS with rejectUnauthorized=true; this is not a path verifier.
function node(data, offset = 0) {
  const start = offset;
  if (offset + 2 > data.length) throw new Error('Truncated DER');
  const tag = data[offset++];
  if ((tag & 31) === 31) throw new Error('Unsupported DER tag');
  let length = data[offset++];
  if (length & 128) {
    const count = length & 127;
    if (!count || count > 4 || offset + count > data.length || !data[offset]) throw new Error('Invalid DER length');
    length = 0;
    for (let i = 0; i < count; i++) length = length * 256 + data[offset++];
    if (length < 128) throw new Error('Noncanonical DER length');
  }
  if (offset + length > data.length) throw new Error('Truncated DER value');
  return { tag, start, end: offset + length, value: data.subarray(offset, offset + length) };
}
function elements(data) {
  const values = [];
  for (let offset = 0; offset < data.length;) {
    const value = node(data, offset);
    values.push(value);
    offset = value.end;
  }
  return values;
}
function sequence(data, tag = 48) {
  const value = node(data);
  if (value.tag !== tag || value.end !== data.length) throw new Error('Invalid DER sequence');
  return elements(value.value);
}
function extensions(raw) {
  const certificate = sequence(raw);
  if (certificate.length !== 3 || certificate[0].tag !== 48) throw new Error('Invalid certificate');
  const fields = elements(certificate[0].value).filter(field => field.tag === 163);
  if (fields.length !== 1) throw new Error('Missing certificate extensions');
  const result = new Map();
  for (const ext of sequence(fields[0].value)) {
    if (ext.tag !== 48) throw new Error('Invalid extension');
    const parts = elements(ext.value);
    if (parts[0]?.tag !== 6 || ![2, 3].includes(parts.length) || parts.at(-1).tag !== 4) throw new Error('Invalid extension fields');
    const oid = parts[0].value.toString('hex');
    let critical = false;
    if (parts.length === 3) {
      if (parts[1].tag !== 1 || parts[1].value.length !== 1 || ![0, 255].includes(parts[1].value[0])) throw new Error('Invalid extension critical flag');
      critical = parts[1].value[0] === 255;
    }
    if (result.has(oid)) throw new Error('Duplicate certificate extension');
    result.set(oid, { critical, value: parts.at(-1).value });
  }
  return result;
}

export function validateSVID(input, expected, now = Date.now()) {
  spiffeID(expected);
  const cert = new X509Certificate(input);
  const expires = Date.parse(cert.validTo);
  if (cert.ca || !Number.isFinite(expires) || now < Date.parse(cert.validFrom) || now >= expires) {
    throw new Error('SVID is a CA, not yet valid, or expired');
  }
  const exts = extensions(cert.raw);
  const san = exts.get('551d11');
  const names = san ? sequence(san.value).filter(name => name.tag === 134) : [];
  if (names.length !== 1 || names[0].value.some(byte => byte > 127)
      || names[0].value.toString('ascii') !== expected || (!cert.subject && !san.critical)) {
    throw new Error('SVID must contain exactly the expected SPIFFE URI SAN');
  }
  const usage = exts.get('551d0f');
  const bits = usage && node(usage.value);
  if (!usage?.critical || bits.tag !== 3 || bits.end !== usage.value.length || bits.value.length < 2
      || bits.value[0] > 7 || !(bits.value[1] & 128) || (bits.value[1] & 6)) {
    throw new Error('SVID requires critical digitalSignature and forbids certificate/CRL signing');
  }
  const extended = exts.get('551d25');
  if (extended) {
    const purposes = sequence(extended.value).map(item => {
      if (item.tag !== 6) throw new Error('Invalid extended key usage');
      return item.value.toString('hex');
    });
    if (!purposes.includes('2b06010505070301') || !purposes.includes('2b06010505070302')) {
      throw new Error('SVID extended key usage must permit client and server authentication');
    }
  }
  return { certificate: cert, id: expected, serial: cert.serialNumber, expires };
}

export function certificates(pem) {
  const text = Buffer.isBuffer(pem) ? pem.toString('utf8') : pem;
  const blocks = text.match(/-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----/g) ?? [];
  if (!blocks.length || text.replace(/-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----/g, '').trim()) {
    throw new Error('Expected only PEM certificates');
  }
  return blocks.map(block => new X509Certificate(block));
}

export function validateMaterial(certPEM, keyPEM, bundlePEM, expected) {
  const chain = certificates(certPEM);
  const leaf = validateSVID(chain[0].raw, expected);
  if (!leaf.certificate.checkPrivateKey(createPrivateKey(keyPEM))) throw new Error('SVID private key does not match');
  const roots = certificates(bundlePEM);
  if (roots.some(cert => !cert.ca)) throw new Error('Trust bundle must contain CA certificates');
  return { ...leaf, expires: Math.min(...chain.map(cert => Date.parse(cert.validTo))) };
}

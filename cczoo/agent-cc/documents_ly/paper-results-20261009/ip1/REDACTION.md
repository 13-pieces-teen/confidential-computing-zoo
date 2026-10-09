# Redaction rules (applied to export copies only)

Server originals (`/secure/…`, `/root/argus-ip1-handoff/…`,
`/root/argus-e2-ip1/…`) are **never modified**. Redactions are applied only
while copying into this export tree.

- **R1 — secret_seed**: every `secret_seed` value in the six frozen E4
  configs is replaced by `REDACTED:R1:sha256:<sha256(original)>`. The full
  sha256 is kept so local readers can still verify the delivered
  configuration_sha256 / seed references without knowing the seed value.
- **R2 — excluded files (not redacted, simply not exported)**, by name or
  directory:
  - `credentials/` and `ready.json` (SPIRE credential cache, serials),
    `generation-*/` dirs, `payloads/` (kept off-export by review decision)
  - OIDC tokens (`.token`, `oidc`), Sigstore material
  - API-key files delivered by IP2 (`paper02-*-api-key`)
  - `e2-config*.json` (contains probe cert/key/bundle path and api-key env
    references — no credential values, but held back by export review)
  - `*-FIELDS.json` IP2 server-field files (including IP2's ssh host public
    key record)
  - `receiver.jsonl` (receiver runtime record, held back by export review)
  - `*.tar.gz`, `*.pid.txt`
  - E4 `private-fixture.json` (task input ground truth incl. expected
    answers — held on server; fact_id/fact_sha256/constraint_key remain in
    `result.json` and the CSVs for correlation)
- **R3 — allowlisted verbatim files** (public material by nature, copied
  unchanged, never edited): `rekor.json` (public transparency-log record),
  `trust-*.bin`, `bundle*.pem` (public trust bundles/certificates),
  `bundle-now-*.pem`.
- **Identity mapping**: synthetic experiment identities are kept verbatim
  (`e4p1-*` run ids, `e4c1` client, `e4p1-s<seed>-<cond>` users,
  `argus-e4:<run_id>:e4c1:<step>:<n>` session keys, request/attempt ids,
  fact ids, serials). No real personal accounts appear.
- Defense-in-depth: every exported file was scanned for JWT-shaped strings
  (`eyJ…`), PEM blocks and `PRIVATE KEY` markers; zero un-allowlisted hits.

If a field needed for correlation is missing from this export, its original
server path is given in `MANIFEST.tsv` / `MISSING.md`; protected-channel
transfer can be arranged via the established IP1↔IP2 handoff.

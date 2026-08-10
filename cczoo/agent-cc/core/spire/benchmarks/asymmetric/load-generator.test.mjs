import assert from "node:assert/strict";
import { test } from "node:test";

import { parseArguments } from "./load-generator.mjs";

test("load generator accepts a fixed-QPS guarded profile", () => {
  const configuration = parseArguments([
    "--mode", "guarded",
    "--url", "https://openviking.argus.local:1943/health",
    "--guard-url", "http://guard:8007/guard/v1/authorize",
    "--duration-seconds", "30",
    "--qps", "25",
    "--concurrency", "8",
    "--warmup-requests", "20",
    "--profile", "e5-qps-25",
  ]);

  assert.equal(configuration.mode, "guarded");
  assert.equal(configuration.requests, 0);
  assert.equal(configuration.durationSeconds, 30);
  assert.equal(configuration.qps, 25);
  assert.equal(configuration.concurrency, 8);
  assert.equal(configuration.warmupRequests, 20);
  assert.equal(configuration.profile, "e5-qps-25");
});

test("load generator rejects an unknown transport profile", () => {
  assert.throws(
    () => parseArguments(["--mode", "proxy"]),
    /unsupported --mode/u,
  );
});

test("load generator validates fail-on-error as an explicit switch", () => {
  const configuration = parseArguments([
    "--mode", "guard",
    "--fail-on-error", "1",
  ]);
  assert.equal(configuration.failOnError, true);
  assert.throws(
    () => parseArguments(["--mode", "guard", "--fail-on-error", "yes"]),
    /must be 0 or 1/u,
  );
});

test("load generator defaults to a bounded request count", () => {
  const configuration = parseArguments([
    "--mode", "guard",
    "--url", "https://openviking.argus.local:1943/health",
    "--guard-url", "http://guard:8007/guard/v1/authorize",
  ]);

  assert.equal(configuration.requests, 1000);
  assert.equal(configuration.durationSeconds, 0);
});

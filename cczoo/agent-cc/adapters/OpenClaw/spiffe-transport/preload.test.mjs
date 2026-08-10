import assert from "node:assert/strict";
import { test } from "node:test";
import { canonicalOrigin, operationFor } from "./preload.mjs";

test("canonicalOrigin accepts only an origin", () => {
  assert.equal(canonicalOrigin("https://openviking:1943"), "https://openviking:1943");
  assert.throws(() => canonicalOrigin("http://openviking:1933"));
  assert.throws(() => canonicalOrigin("https://openviking:1943/api"));
});

test("operation map uses longest matching prefix", () => {
  const operationMap = [["/api/v1/memory", "memory.write"], ["/api", "generic"]];
  assert.equal(operationFor("POST", "/api/v1/memory/store", operationMap), "memory.write");
  assert.equal(operationFor("GET", "/health", operationMap), "memory.read");
});

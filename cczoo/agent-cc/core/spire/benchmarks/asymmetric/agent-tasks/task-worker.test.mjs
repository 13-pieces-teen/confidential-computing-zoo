import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  archiveIsReady,
  findAssistantMarker,
  taskIdentity,
  taskPrompt,
  validateAssistant,
} from "./task-worker.mjs";

function validAssistant(marker) {
  const longText = "本段说明系统在真实任务中保持固定配置并记录完整证据，同时区分生成、捕获、提交和归档阶段，避免把中间状态误写成最终成功。";
  return {
    id: "message-target",
    role: "assistant",
    parts: [{
      type: "text",
      text: `1. ${longText}\n2. ${longText}\n3. ${longText}\n${marker}`,
    }],
  };
}

function context(sessionId, messages, overview = "") {
  return {
    sessionId,
    payload: {
      status: "ok",
      result: { messages, latest_archive_overview: overview },
    },
  };
}

test("task prompt never contains the complete response marker", () => {
  const identity = taskIdentity("run01", "C1", "openclaw-01", "001", "0123456789abcdef0123456789abcdef");
  const prompt = taskPrompt("固定的合成材料", identity.nonce);
  assert.equal(prompt.includes(identity.marker), false);
});

test("assistant marker is found after more than one hundred historical sessions", () => {
  const marker = "ARGUS-E8-RESULT-0123456789abcdef0123456789abcdef";
  const contexts = Array.from({ length: 125 }, (_, index) => context(`old-${index}`, []));
  contexts.push(context("target", [validAssistant(marker)]));
  const match = findAssistantMarker(contexts, marker);
  assert.equal(match.sessionId, "target");
  assert.equal(match.messageId, "message-target");
});

test("a marker in a user message cannot complete a task", () => {
  const marker = "ARGUS-E8-RESULT-0123456789abcdef0123456789abcdef";
  assert.throws(
    () => findAssistantMarker([context("target", [{ role: "user", content: marker }])], marker),
    /outside assistant output/,
  );
});

test("archive completion is bound to task status, commit delta, and a new overview", () => {
  const before = context("target", [], "old overview").payload;
  const after = context("target", [], "new overview").payload;
  const oldDigest = createHash("sha256").update("old overview").digest("hex");
  assert.equal(archiveIsReady({ status: "completed" }, { commit_count: 2 }, after, 1, oldDigest), true);
  assert.equal(archiveIsReady({ status: "running" }, { commit_count: 2 }, after, 1, oldDigest), false);
  assert.equal(archiveIsReady({ status: "completed" }, { commit_count: 1 }, after, 1, oldDigest), false);
  assert.equal(archiveIsReady({ status: "completed" }, { commit_count: 2 }, before, 1, oldDigest), false);
});

test("response validation requires three numbered conclusions and a final marker", () => {
  const marker = "ARGUS-E8-RESULT-0123456789abcdef0123456789abcdef";
  const result = validateAssistant(validAssistant(marker), marker);
  assert.equal(result.conclusionCount, 3);
  assert.ok(result.bodyChars >= 150 && result.bodyChars <= 250);
});

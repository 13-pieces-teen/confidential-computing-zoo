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

function validAssistant(marker, { collapsed = false } = {}) {
  const summary = "某团队计划于周三夜间对内部知识服务进行两小时维护升级，内容包括数据库索引重建、API 镜像替换及缓存预热，升级前须导出配置与索引元数据但禁止复制用户原文，数据库迁移必须保证向后兼容且旧版服务须在十五分钟内重新接管流量，发布分三档推进并逐档观察错误率、九十五分位延迟与队列积压指标，完成后须保存版本时间线与指标快照并注明各阶段实测时长。";
  const conclusions = ["1. 数据库迁移须向后兼容且禁止手工删表。", "2. 错误率超百分之一或等待超五分钟即回滚。", "3. 缓存预热失败只限流不单独回滚。"];
  const text = collapsed
    ? `${summary} ${conclusions.join(" ")} ${marker}`
    : `${summary}\n${conclusions.join("\n")}\n${marker}`;
  return {
    id: "message-target",
    role: "assistant",
    parts: [{ type: "text", text }],
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

test("task prompt uses the same summary length contract as validation", () => {
  const prompt = taskPrompt("固定的合成材料", "0123456789abcdef0123456789abcdef");
  assert.match(prompt, /150–400 字/u);
  assert.doesNotMatch(prompt, /150–250 字/u);
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
  assert.ok(result.summaryChars >= 150 && result.summaryChars <= 400, `summary length ${result.summaryChars}`);
});

test("response validation tolerates the transport collapsing newlines to spaces", () => {
  const marker = "ARGUS-E8-RESULT-0123456789abcdef0123456789abcdef";
  const result = validateAssistant(validAssistant(marker, { collapsed: true }), marker);
  assert.equal(result.conclusionCount, 3);
  assert.ok(result.summaryChars >= 150 && result.summaryChars <= 400, `summary length ${result.summaryChars}`);
});

test("response validation rejects a marker that is not the final token", () => {
  const marker = "ARGUS-E8-RESULT-0123456789abcdef0123456789abcdef";
  const message = {
    role: "assistant",
    parts: [{ type: "text", text: `摘要。 1. 甲。 2. 乙。 3. 丙。 ${marker} 结尾多余` }],
  };
  assert.throws(() => validateAssistant(message, marker), (error) => error?.errorClass === "invalid_marker");
});

test("response validation rejects missing numbered conclusions", () => {
  const marker = "ARGUS-E8-RESULT-0123456789abcdef0123456789abcdef";
  const dropped = validAssistant(marker, { collapsed: true }).parts[0].text.replace(/\s*1\.\s/u, " ");
  const message = { role: "assistant", parts: [{ type: "text", text: dropped }] };
  assert.throws(() => validateAssistant(message, marker), (error) => error?.errorClass === "invalid_conclusions");
});

test("response validation rejects duplicated or reordered conclusions", () => {
  const marker = "ARGUS-E8-RESULT-0123456789abcdef0123456789abcdef";
  const base = validAssistant(marker, { collapsed: true }).parts[0].text;
  const duplicated = { role: "assistant", parts: [{ type: "text", text: base.replace(" 2.", " 1. 重复结论。 2.") }] };
  const reordered = { role: "assistant", parts: [{ type: "text", text: base.replace(/ 1\. (.*?) 2\. (.*?) 3\. (.*?) ARGUS/u, " 3. $3 2. $2 1. $1 ARGUS") }] };
  assert.throws(() => validateAssistant(duplicated, marker), (error) => error?.errorClass === "invalid_conclusions");
  assert.throws(() => validateAssistant(reordered, marker), (error) => error?.errorClass === "invalid_conclusions");
});

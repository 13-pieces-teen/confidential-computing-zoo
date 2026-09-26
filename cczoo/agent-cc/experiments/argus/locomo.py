#!/usr/bin/env python3
"""Convert an operator-supplied LoCoMo dataset into separately labelled tasks.

No dataset download, model calls or official-score claims. The protected fixture
retains reference answers; only the question is sent in the held-out query.
"""
import argparse
import os
import random
from collections import defaultdict
from pathlib import Path

from common import atomic, read, require, sha


def convert(source, output, limit=100, categories=(1, 2, 3, 4), seed=0, samples=None):
    rows = read(source)
    require(isinstance(rows, list), "expected LoCoMo conversation list")
    tasks = []
    for number, row in enumerate(rows):
        sample = str(row.get("sample_id", number))
        if samples is not None and sample not in {str(s) for s in samples}:
            continue
        conversation = row.get("conversation", {})
        sessions = []
        for key in sorted((k for k in conversation if k.startswith("session_") and not k.endswith("_date_time")),
                          key=lambda k: int(k.split("_")[1])):
            messages = conversation[key]
            require(isinstance(messages, list) and all(isinstance(x, dict) and "text" in x and "speaker" in x for x in messages), "invalid LoCoMo session")
            sessions.append({"source_session": key, "date_time": conversation.get(key + "_date_time"), "messages": messages})
        require(sessions, "conversation has no sessions")
        for qn, qa in enumerate(row.get("qa", [])):
            if qa.get("category") not in categories:
                continue
            question, answer = qa.get("question"), qa.get("answer")
            if qa["category"] == 5:
                answer = "UNKNOWN"
            require(isinstance(question, str) and answer is not None, "missing question/reference")
            # Filter trivial answer leakage from positive tasks. Short answers
            # are retained with an explicit manual-review marker.
            if qa['category'] != 5 and len(str(answer)) >= 5 and str(answer).casefold() in question.casefold():
                continue
            tasks.append({"task_id": "%s-q%d" % (sample, qn),
                          "source_sample": sample, "category": qa["category"],
                          "sessions": sessions, "question": question, "reference_answer": answer,
                          "source_evidence": qa.get("evidence", []), "short_answer_review": len(str(answer)) < 5,
                          "evaluation": "NOT_RUN", "requires_new_query_session": True})
    require(tasks, "no derived tasks after filtering")
    require(isinstance(limit, int) and limit > 0, "positive limit required")
    # Freeze a round-robin sample across conversation/category strata. This
    # avoids the former default of taking the first conversation's first 100 QA.
    buckets = defaultdict(list)
    for task in tasks:
        buckets[(task["source_sample"], task["category"])].append(task)
    rng = random.Random(seed)
    keys = sorted(buckets)
    rng.shuffle(keys)
    for values in buckets.values():
        rng.shuffle(values)
    selected = []
    while keys and len(selected) < limit:
        for key in list(keys):
            selected.append(buckets[key].pop())
            if not buckets[key]:
                keys.remove(key)
            if len(selected) >= limit:
                break
    tasks = selected
    result = {"schema": "argus.locomo-derived.v1", "source_sha256": sha(source), "tasks": tasks,
              "selection": {"method": "round_robin_conversation_category", "seed": seed,
                            "limit": limit, "categories": list(categories), "samples": samples},
              "label": "LoCoMo-derived cross-session tasks; not the official benchmark protocol",
              "synthetic_mechanism_results": "report separately", "model_score": "NOT_RUN"}
    atomic(output, result)
    if os.name == "posix": os.chmod(output, 0o600)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True); p.add_argument("--output", required=True)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--categories", default="1,2,3,4")
    p.add_argument("--samples", help="comma-separated source sample IDs; omit for all")
    a = p.parse_args(); require(a.limit > 0, "positive limit required")
    categories = tuple(int(v) for v in a.categories.split(","))
    require(categories and set(categories) <= {1, 2, 3, 4, 5}, "invalid categories")
    convert(a.source, a.output, a.limit, categories, a.seed, a.samples.split(",") if a.samples else None)


if __name__ == "__main__": main()

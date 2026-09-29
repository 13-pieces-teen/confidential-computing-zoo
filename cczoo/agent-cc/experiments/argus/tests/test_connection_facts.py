import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connection_facts import prepare
from fact_protocol import facts_in


def test_each_lane_and_request_has_a_distinct_complete_fact(tmp_path):
    output = tmp_path / "payloads"
    prepare(output, 7, 3)
    plan = json.loads((output / "payload-plan.json").read_text())
    facts = []
    for lane in ("existing", "new", "inflight"):
        for item in plan[lane]:
            body = (output / item["body_file"]).read_text()
            actual = facts_in(json.loads(body)["query"])
            assert len(actual) == 1 and actual[0]["fact_id"] == item["fact_id"]
            facts.append(item["fact_id"])
            if lane == "inflight": assert body.index("ARGUS_FACT_V1") > 8192
    assert len(facts) == len(set(facts)) == 7

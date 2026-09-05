//! Contract-only tests using Trustee v0.21 dependencies; no Quote verifier substitute.
#[cfg(test)]
mod tests {
    use serde_json::{json, Value};
    use sha2::{Digest, Sha384};
    fn vector() -> Value {
        serde_json::from_str(include_str!("../../testdata/runtime-data.json")).unwrap()
    }
    #[test]
    fn trustee_jcs_reportdata_matches_go_and_provider() {
        let v = vector();
        let canonical = serde_json_canonicalizer::to_vec(&v["runtime_data"]).unwrap();
        assert_eq!(
            String::from_utf8(canonical.clone()).unwrap(),
            v["canonical"]
        );
        let mut report = Sha384::digest(canonical).to_vec();
        report.extend([0u8; 16]);
        assert_eq!(hex::encode(report), v["report_data_hex"]);
    }
    fn rendered() -> String {
        let v = vector();
        let mut p = include_str!("../../policy/workload_cpu.rego.tmpl").to_string();
        for (k, val) in v["runtime_data"].as_object().unwrap() {
            p = p.replace(&format!("@{}@", k.to_uppercase()), &val.to_string());
        }
        for (k, n) in [
            ("MR_TD", "1"),
            ("RTMR_0", "2"),
            ("RTMR_1", "3"),
            ("RTMR_2", "4"),
        ] {
            p = p.replace(&format!("@{k}@"), &json!(n.repeat(96)).to_string());
        }
        p
    }
    fn input() -> Value {
        json!({"runtime_data_claims":vector()["runtime_data"],
    "tdx":{"quote":{"header":{"tee_type":"81000000","vendor_id":"939a7233f79c4ca9940a0db3957f0607"},
    "body":{"mr_td":"1".repeat(96),"rtmr_0":"2".repeat(96),"rtmr_1":"3".repeat(96),"rtmr_2":"4".repeat(96)}},
    "tcb_status":"UpToDate","collateral_expiration_status":"0","td_attributes":{"debug":false}}})
    }
    fn claims(i: &Value) -> Value {
        let mut engine = regorus::Engine::new();
        engine
            .add_policy("argus-workload_cpu.rego".into(), rendered())
            .unwrap();
        engine.set_input_json(&i.to_string()).unwrap();
        serde_json::to_value(engine.eval_rule("data.policy.trust_claims".into()).unwrap()).unwrap()
    }
    #[test]
    fn real_regorus_policy_checks_platform_and_workload() {
        assert_eq!(
            claims(&input()),
            json!({"hardware":2,"executables":3,"configuration":2})
        );
        for field in [
            "protocol",
            "agent_id",
            "workload_id",
            "policy_id",
            "image_config_digest",
            "config_digest",
            "config_path",
            "executable",
            "rootfs_read_only",
            "pid",
            "nonce",
            "launch_id",
        ] {
            let mut i = input();
            i["runtime_data_claims"][field] = json!("");
            assert_ne!(
                claims(&i),
                json!({"hardware":2,"executables":3,"configuration":2}),
                "accepted {field}"
            );
        }
        for pointer in [
            "/tdx/tcb_status",
            "/tdx/collateral_expiration_status",
            "/tdx/quote/body/mr_td",
            "/tdx/quote/body/rtmr_1",
        ] {
            let mut i = input();
            *i.pointer_mut(pointer).unwrap() = json!("wrong");
            assert_ne!(
                claims(&i),
                json!({"hardware":2,"executables":3,"configuration":2}),
                "accepted {pointer}"
            );
        }
        let mut i = input();
        i["tdx"]["td_attributes"]["debug"] = json!(true);
        assert_ne!(claims(&i)["hardware"], json!(2));
    }
}

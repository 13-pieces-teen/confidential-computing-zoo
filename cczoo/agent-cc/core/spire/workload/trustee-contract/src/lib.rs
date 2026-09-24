//! Contract-only tests using Trustee v0.21 dependencies; no Quote verifier substitute.
#[cfg(test)]
pub use kbs_types::{HashAlgorithm, Tee};
#[cfg(test)]
#[path = "../../trustee/argus_trucon.rs"]
mod argus_trucon;
#[cfg(test)]
mod tests {
    use serde_json::{json, Value};
    use sha2::{Digest, Sha384};
    #[test]
    fn server_hook_requires_references_and_bound_runtime() {
        let algorithm = serde_json::from_value(serde_json::json!("sha384")).unwrap();
        let runtime = vector()["runtime_data"].clone();
        let evidence = json!({"rekor_entry_ids":["a".repeat(64), "b".repeat(64)]});
        assert!(
            crate::argus_trucon::prepare(&crate::Tee::Tdx, &evidence, &runtime, &algorithm)
                .unwrap()
                .is_some()
        );
        assert!(
            crate::argus_trucon::prepare(&crate::Tee::Tdx, &json!({}), &runtime, &algorithm)
                .is_err()
        );
        assert!(crate::argus_trucon::prepare(
            &crate::Tee::Tdx,
            &evidence,
            &Value::Null,
            &algorithm
        )
        .is_err());
        let wrong = serde_json::from_value(serde_json::json!("sha256")).unwrap();
        assert!(
            crate::argus_trucon::prepare(&crate::Tee::Tdx, &evidence, &runtime, &wrong).is_err()
        );
        let duplicate = json!({"rekor_entry_ids":["a".repeat(64), "a".repeat(64)]});
        assert!(
            crate::argus_trucon::prepare(&crate::Tee::Tdx, &duplicate, &runtime, &algorithm)
                .is_err()
        );
        let mixed = json!({"rekor_entry_ids":["0", "1".repeat(64)]});
        assert!(
            crate::argus_trucon::prepare(&crate::Tee::Tdx, &mixed, &runtime, &algorithm).is_ok()
        );
        for invalid in ["", "-1", "1?x=y", "١٢٣"] {
            let evidence = json!({"rekor_entry_ids":[invalid, "b".repeat(64)]});
            assert!(crate::argus_trucon::prepare(
                &crate::Tee::Tdx,
                &evidence,
                &runtime,
                &algorithm
            )
            .is_err());
        }
    }
    #[tokio::test]
    async fn server_hook_never_preserves_a_preexisting_verdict() {
        let mut claims = json!({"trucon":{"verified":true}});
        crate::argus_trucon::appraise(None, &mut claims, "cpu")
            .await
            .unwrap();
        assert!(claims.get("trucon").is_none());
        assert!(
            crate::argus_trucon::appraise(Some(&json!({})), &mut claims, "gpu")
                .await
                .is_err()
        );
        assert!(
            crate::argus_trucon::appraise(Some(&json!({})), &mut claims, "cpu")
                .await
                .is_err()
        );
    }
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
            ("RTMR2_BASELINE", "4"),
        ] {
            p = p.replace(&format!("@{k}@"), &json!(n.repeat(96)).to_string());
        }
        p
    }
    fn input() -> Value {
        json!({"runtime_data_claims":vector()["runtime_data"],
    "tdx":{"quote":{"header":{"tee_type":"81000000","vendor_id":"939a7233f79c4ca9940a0db3957f0607"},
    "body":{"mr_td":"1".repeat(96),"rtmr_0":"2".repeat(96),"rtmr_1":"3".repeat(96),"rtmr_2":"4".repeat(96)}},
    "trucon":{"verified":true,"baseline_rtmr":"4".repeat(96)},
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
        i["tdx"]["quote"]["body"]["rtmr_2"] = json!("a".repeat(96));
        assert_eq!(claims(&i)["executables"], json!(3));
        for verdict in [
            json!(null),
            json!({"verified":false,"baseline_rtmr":"4".repeat(96)}),
            json!({"verified":true,"baseline_rtmr":"5".repeat(96)}),
        ] {
            i["tdx"]["trucon"] = verdict;
            assert_ne!(claims(&i)["executables"], json!(3));
        }
        let mut i = input();
        i["tdx"]["td_attributes"]["debug"] = json!(true);
        assert_ne!(claims(&i)["hardware"], json!(2));
    }
}

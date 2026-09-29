//! Re-evaluate an archived authenticated policy input, never a Quote substitute.
use anyhow::{ensure, Context, Result};
fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    ensure!(args.len() == 3, "usage: argus-replay-policy POLICY.rego INPUT.json");
    let policy = std::fs::read_to_string(&args[1]).context("read archived policy")?;
    let input = std::fs::read_to_string(&args[2]).context("read archived policy input")?;
    let mut engine = regorus::Engine::new();
    engine.add_policy("captured-policy.rego".into(), policy)?;
    engine.set_input_json(&input)?;
    let claims = engine.eval_rule("data.policy.trust_claims".into())?;
    println!("{}", serde_json::json!({"schema":"argus.policy-replay.v1", "trust_claims":claims,
        "scope":"archived authenticated input; no offline DCAP or fresh admission"}));
    Ok(())
}

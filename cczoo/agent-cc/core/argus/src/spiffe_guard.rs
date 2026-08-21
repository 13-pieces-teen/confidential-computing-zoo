// Copyright (c) 2026 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.

//! Caller-local authorization for the dual-TDVM Broker SPIFFE profile.
//!
//! SPIRE authenticates the remote workload and issues its SVID. This module
//! deliberately does not re-verify attestation evidence or accept certificate
//! material. It evaluates the relying party's explicit identity/service policy
//! before the caller sends a sensitive request.

use anyhow::{anyhow, Context, Result};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs;
use std::path::Path;
use url::Url;

const POLICY_VERSION: &str = "v1";
const MAX_FIELD_LENGTH: usize = 2048;
const MIN_TTL_SECONDS: u64 = 1;
const MAX_TTL_SECONDS: u64 = 300;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpiffeGuardPolicy {
    pub version: String,
    pub policy_id: String,
    pub trust_domain: String,
    #[serde(default = "default_decision_ttl_seconds")]
    pub decision_ttl_seconds: u64,
    pub rules: Vec<SpiffeGuardRule>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpiffeGuardRule {
    pub id: String,
    pub callers: Vec<String>,
    pub target_spiffe_id: String,
    pub target_service: String,
    pub target_origins: Vec<String>,
    #[serde(default)]
    pub operations: Vec<String>,
    #[serde(default)]
    pub data_classes: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SpiffeAuthorizationRequest {
    pub request_id: String,
    pub caller_spiffe_id: String,
    pub target_spiffe_id: String,
    pub target_service: String,
    pub target_origin: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub operation: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data_class: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum SpiffeAuthorizationDecision {
    Allow,
    Deny,
}

#[derive(Clone, Debug, Serialize)]
pub struct SpiffeAuthorizationResponse {
    pub request_id: String,
    pub decision: SpiffeAuthorizationDecision,
    pub reason: String,
    pub decision_id: String,
    pub expires_at_unix: i64,
    pub policy_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rule_id: Option<String>,
}

#[derive(Clone, Debug)]
pub struct SpiffeGuard {
    policy: SpiffeGuardPolicy,
}

impl SpiffeGuardPolicy {
    pub fn from_yaml_file(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let content = fs::read_to_string(path)
            .with_context(|| format!("failed to read SPIFFE Guard policy {}", path.display()))?;
        let policy: Self = serde_yaml::from_str(&content)
            .with_context(|| format!("failed to parse SPIFFE Guard policy {}", path.display()))?;
        policy.validate()?;
        Ok(policy)
    }

    pub fn validate(&self) -> Result<()> {
        validate_field("version", &self.version)?;
        if self.version != POLICY_VERSION {
            return Err(anyhow!(
                "unsupported SPIFFE Guard policy version {:?}; expected {:?}",
                self.version,
                POLICY_VERSION
            ));
        }
        validate_field("policy_id", &self.policy_id)?;
        validate_trust_domain(&self.trust_domain)?;
        if !(MIN_TTL_SECONDS..=MAX_TTL_SECONDS).contains(&self.decision_ttl_seconds) {
            return Err(anyhow!(
                "decision_ttl_seconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}"
            ));
        }
        if self.rules.is_empty() {
            return Err(anyhow!(
                "SPIFFE Guard policy must contain at least one rule"
            ));
        }

        let mut rule_ids = HashSet::new();
        for rule in &self.rules {
            validate_field("rule.id", &rule.id)?;
            if !rule_ids.insert(rule.id.as_str()) {
                return Err(anyhow!("duplicate SPIFFE Guard rule id {:?}", rule.id));
            }
            if rule.callers.is_empty() {
                return Err(anyhow!(
                    "rule {:?} must contain at least one caller",
                    rule.id
                ));
            }
            for caller in &rule.callers {
                validate_spiffe_id(caller, &self.trust_domain, "caller")?;
            }
            validate_spiffe_id(&rule.target_spiffe_id, &self.trust_domain, "target")?;
            validate_field("rule.target_service", &rule.target_service)?;
            if rule.target_origins.is_empty() {
                return Err(anyhow!(
                    "rule {:?} must contain at least one target origin",
                    rule.id
                ));
            }
            for origin in &rule.target_origins {
                canonical_https_origin(origin)?;
            }
            validate_values("rule.operations", &rule.operations)?;
            validate_values("rule.data_classes", &rule.data_classes)?;
        }
        Ok(())
    }
}

impl SpiffeGuard {
    pub fn new(policy: SpiffeGuardPolicy) -> Result<Self> {
        policy.validate()?;
        Ok(Self { policy })
    }

    pub fn from_yaml_file(path: impl AsRef<Path>) -> Result<Self> {
        Self::new(SpiffeGuardPolicy::from_yaml_file(path)?)
    }

    pub fn policy_id(&self) -> &str {
        &self.policy.policy_id
    }

    pub fn decision_ttl_seconds(&self) -> u64 {
        self.policy.decision_ttl_seconds
    }

    pub fn authorize(&self, request: &SpiffeAuthorizationRequest) -> SpiffeAuthorizationResponse {
        let now = Utc::now().timestamp();
        let expires_at_unix = now + self.policy.decision_ttl_seconds as i64;
        let decision_id = decision_id(request, now);

        if let Err(reason) = validate_request(request, &self.policy.trust_domain) {
            return self.deny(request, decision_id, expires_at_unix, reason.to_string());
        }

        let request_origin = match canonical_https_origin(&request.target_origin) {
            Ok(origin) => origin,
            Err(error) => {
                return self.deny(request, decision_id, expires_at_unix, error.to_string());
            }
        };

        for rule in &self.policy.rules {
            if !rule
                .callers
                .iter()
                .any(|value| value == &request.caller_spiffe_id)
                || rule.target_spiffe_id != request.target_spiffe_id
                || rule.target_service != request.target_service
                || !rule.target_origins.iter().any(|value| {
                    canonical_https_origin(value)
                        .map(|origin| origin == request_origin)
                        .unwrap_or(false)
                })
                || !optional_constraint_matches(&rule.operations, request.operation.as_deref())
                || !optional_constraint_matches(&rule.data_classes, request.data_class.as_deref())
            {
                continue;
            }

            return SpiffeAuthorizationResponse {
                request_id: request.request_id.clone(),
                decision: SpiffeAuthorizationDecision::Allow,
                reason: "matched caller-local SPIFFE authorization policy".to_string(),
                decision_id,
                expires_at_unix,
                policy_id: self.policy.policy_id.clone(),
                rule_id: Some(rule.id.clone()),
            };
        }

        self.deny(
            request,
            decision_id,
            expires_at_unix,
            "no caller-local SPIFFE authorization rule matched".to_string(),
        )
    }

    fn deny(
        &self,
        request: &SpiffeAuthorizationRequest,
        decision_id: String,
        expires_at_unix: i64,
        reason: String,
    ) -> SpiffeAuthorizationResponse {
        SpiffeAuthorizationResponse {
            request_id: request.request_id.clone(),
            decision: SpiffeAuthorizationDecision::Deny,
            reason,
            decision_id,
            expires_at_unix,
            policy_id: self.policy.policy_id.clone(),
            rule_id: None,
        }
    }
}

fn default_decision_ttl_seconds() -> u64 {
    30
}

fn validate_request(request: &SpiffeAuthorizationRequest, trust_domain: &str) -> Result<()> {
    validate_field("request_id", &request.request_id)?;
    validate_spiffe_id(&request.caller_spiffe_id, trust_domain, "caller")?;
    validate_spiffe_id(&request.target_spiffe_id, trust_domain, "target")?;
    validate_field("target_service", &request.target_service)?;
    canonical_https_origin(&request.target_origin)?;
    if let Some(operation) = request.operation.as_deref() {
        validate_field("operation", operation)?;
    }
    if let Some(data_class) = request.data_class.as_deref() {
        validate_field("data_class", data_class)?;
    }
    Ok(())
}

fn validate_field(name: &str, value: &str) -> Result<()> {
    if value.is_empty() || value.len() > MAX_FIELD_LENGTH || value.trim() != value {
        return Err(anyhow!("{name} is empty, too long, or not canonical"));
    }
    if value.chars().any(char::is_control) {
        return Err(anyhow!("{name} contains control characters"));
    }
    Ok(())
}

fn validate_values(name: &str, values: &[String]) -> Result<()> {
    let mut unique = HashSet::new();
    for value in values {
        validate_field(name, value)?;
        if !unique.insert(value.as_str()) {
            return Err(anyhow!("{name} contains duplicate value {value:?}"));
        }
    }
    Ok(())
}

fn validate_trust_domain(value: &str) -> Result<()> {
    validate_field("trust_domain", value)?;
    if value.contains('/') || value.contains(':') {
        return Err(anyhow!("trust_domain must be a SPIFFE trust-domain name"));
    }
    Ok(())
}

fn validate_spiffe_id(value: &str, trust_domain: &str, kind: &str) -> Result<()> {
    validate_field(&format!("{kind}_spiffe_id"), value)?;
    let parsed = Url::parse(value).map_err(|_| anyhow!("invalid {kind} SPIFFE ID"))?;
    if parsed.scheme() != "spiffe"
        || parsed.host_str() != Some(trust_domain)
        || parsed.username() != ""
        || parsed.password().is_some()
        || parsed.port().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !parsed.path().starts_with('/')
        || parsed.path() == "/"
    {
        return Err(anyhow!(
            "{kind} SPIFFE ID must be a canonical ID in trust domain {trust_domain:?}"
        ));
    }
    Ok(())
}

fn canonical_https_origin(value: &str) -> Result<String> {
    validate_field("target_origin", value)?;
    let parsed = Url::parse(value).map_err(|_| anyhow!("target_origin is not a valid URL"))?;
    if parsed.scheme() != "https"
        || parsed.username() != ""
        || parsed.password().is_some()
        || parsed.host_str().is_none()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || parsed.path() != "/"
    {
        return Err(anyhow!(
            "target_origin must be a canonical HTTPS origin without path, query, or fragment"
        ));
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| anyhow!("target_origin has no host"))?;
    let port = parsed.port_or_known_default().unwrap_or(443);
    Ok(format!("https://{host}:{port}"))
}

fn optional_constraint_matches(allowed: &[String], actual: Option<&str>) -> bool {
    if allowed.is_empty() {
        return true;
    }
    actual
        .map(|value| allowed.iter().any(|candidate| candidate == value))
        .unwrap_or(false)
}

fn decision_id(request: &SpiffeAuthorizationRequest, now: i64) -> String {
    let mut random = [0_u8; 16];
    let _ = getrandom::getrandom(&mut random);
    let mut digest = Sha256::new();
    digest.update(b"argus-spiffe-guard-decision-v1\0");
    digest.update(request.request_id.as_bytes());
    digest.update(now.to_be_bytes());
    digest.update(random);
    hex::encode(digest.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> SpiffeGuardPolicy {
        SpiffeGuardPolicy {
            version: "v1".to_string(),
            policy_id: "asymmetric-openviking-v1".to_string(),
            trust_domain: "argus.local".to_string(),
            decision_ttl_seconds: 15,
            rules: vec![SpiffeGuardRule {
                id: "openclaw-to-openviking".to_string(),
                callers: vec!["spiffe://argus.local/agent/openclaw".to_string()],
                target_spiffe_id: "spiffe://argus.local/service/openviking-cmem".to_string(),
                target_service: "openviking-cmem".to_string(),
                target_origins: vec!["https://openviking:1943".to_string()],
                operations: vec!["memory.write".to_string()],
                data_classes: vec!["sensitive".to_string()],
            }],
        }
    }

    fn request() -> SpiffeAuthorizationRequest {
        SpiffeAuthorizationRequest {
            request_id: "req-123".to_string(),
            caller_spiffe_id: "spiffe://argus.local/agent/openclaw".to_string(),
            target_spiffe_id: "spiffe://argus.local/service/openviking-cmem".to_string(),
            target_service: "openviking-cmem".to_string(),
            target_origin: "https://openviking:1943".to_string(),
            operation: Some("memory.write".to_string()),
            data_class: Some("sensitive".to_string()),
        }
    }

    #[test]
    fn matching_request_is_allowed() {
        let guard = SpiffeGuard::new(policy()).expect("valid policy");
        let response = guard.authorize(&request());
        assert_eq!(response.decision, SpiffeAuthorizationDecision::Allow);
        assert_eq!(response.rule_id.as_deref(), Some("openclaw-to-openviking"));
    }

    #[test]
    fn wrong_target_identity_is_denied() {
        let guard = SpiffeGuard::new(policy()).expect("valid policy");
        let mut input = request();
        input.target_spiffe_id = "spiffe://argus.local/service/other".to_string();
        assert_eq!(
            guard.authorize(&input).decision,
            SpiffeAuthorizationDecision::Deny
        );
    }

    #[test]
    fn operation_is_required_when_rule_constrains_it() {
        let guard = SpiffeGuard::new(policy()).expect("valid policy");
        let mut input = request();
        input.operation = None;
        assert_eq!(
            guard.authorize(&input).decision,
            SpiffeAuthorizationDecision::Deny
        );
    }

    #[test]
    fn policy_rejects_non_https_origin() {
        let mut input = policy();
        input.rules[0].target_origins = vec!["http://openviking:1933".to_string()];
        assert!(input.validate().is_err());
    }

    #[test]
    fn request_rejects_foreign_trust_domain() {
        let guard = SpiffeGuard::new(policy()).expect("valid policy");
        let mut input = request();
        input.caller_spiffe_id = "spiffe://example.org/agent/openclaw".to_string();
        assert_eq!(
            guard.authorize(&input).decision,
            SpiffeAuthorizationDecision::Deny
        );
    }
}

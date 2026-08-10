// Copyright (c) 2026 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! Guard binary
//!
//! Runs the Argus Guard HTTP server for caller-side verification.
//! Provides REST endpoints for target verification requests.

use argus::{
    types::*,
    engine::{ArgusEngine, EvidenceFetcher, EvidenceFetcherHttp, RaVerifier, PolicyEvaluatorTrait},
    verifier::RaAdapter,
    policy::AllowAllPolicyEvaluator,
    binding::ServiceRuntimeBinding,
    tc_api_client::TcApiClient,
    spiffe_guard::{
        SpiffeAuthorizationDecision, SpiffeAuthorizationRequest, SpiffeAuthorizationResponse,
        SpiffeGuard,
    },
};
use anyhow::Result;
use axum::{
    body::Body,
    extract::State,
    http::{HeaderValue, Method, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use chrono::Utc;
use sha2::{Digest, Sha256};
use std::net::SocketAddr;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use std::time::Instant;
use tower_http::cors::{Any, CorsLayer};

const AUTHORIZATION_CONTEXT_VERSION: &str = "argus-authorization-v2";
const AUTHORIZATION_CONTEXT_DOMAIN: &[u8] = b"argus-business-authorization-v2\0";
const MAX_AUTHORIZATION_FIELD_LENGTH: usize = 8192;
const MAX_AUTHORIZATION_CLOCK_SKEW_SECONDS: i64 = 60;

/// Application state for the Guard HTTP server
#[derive(Clone)]
struct GuardAppState {
    engine: Arc<ArgusEngine>,
    evidence_fetcher: Arc<EvidenceFetcherHttp>,
    ra_adapter: Arc<RaAdapter>,
    policy_evaluator: Arc<dyn PolicyEvaluatorTrait>,
    mode: GuardMode,
    require_authorization_context: bool,
    decision_ttl_seconds: i64,
    /// Optional TC-API client for Agent-side metadata (when Agent is also a TDX workload)
    tc_api_client: Option<Arc<TcApiClient>>,
    /// Caller-local identity policy used only by the asymmetric SPIFFE profile.
    spiffe_guard: Option<Arc<SpiffeGuard>>,
    /// Dependency-free Prometheus metrics for the caller-local Guard path.
    spiffe_metrics: Arc<SpiffeGuardMetrics>,
}

const GUARD_DURATION_BUCKETS_SECONDS: [f64; 13] = [
    0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1,
    0.25, 0.5, 1.0,
];

#[derive(Default)]
struct SpiffeGuardMetrics {
    requests: AtomicU64,
    allow: AtomicU64,
    deny: AtomicU64,
    in_flight: AtomicU64,
    duration_micros: AtomicU64,
    duration_buckets: [AtomicU64; GUARD_DURATION_BUCKETS_SECONDS.len()],
}

impl SpiffeGuardMetrics {
    fn begin(&self) -> Instant {
        self.requests.fetch_add(1, Ordering::Relaxed);
        self.in_flight.fetch_add(1, Ordering::Relaxed);
        Instant::now()
    }

    fn finish(&self, decision: SpiffeAuthorizationDecision, started: Instant) {
        self.in_flight.fetch_sub(1, Ordering::Relaxed);
        match decision {
            SpiffeAuthorizationDecision::Allow => {
                self.allow.fetch_add(1, Ordering::Relaxed);
            }
            SpiffeAuthorizationDecision::Deny => {
                self.deny.fetch_add(1, Ordering::Relaxed);
            }
        }
        let elapsed = started.elapsed();
        let micros = u64::try_from(elapsed.as_micros()).unwrap_or(u64::MAX);
        self.duration_micros.fetch_add(micros, Ordering::Relaxed);
        let seconds = elapsed.as_secs_f64();
        for (index, upper_bound) in GUARD_DURATION_BUCKETS_SECONDS.iter().enumerate() {
            if seconds <= *upper_bound {
                self.duration_buckets[index].fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    fn render(&self) -> String {
        let requests = self.requests.load(Ordering::Relaxed);
        let allow = self.allow.load(Ordering::Relaxed);
        let deny = self.deny.load(Ordering::Relaxed);
        let in_flight = self.in_flight.load(Ordering::Relaxed);
        let duration_sum_seconds =
            self.duration_micros.load(Ordering::Relaxed) as f64 / 1_000_000.0;
        let mut output = String::from(
            "# HELP argus_guard_requests_total Caller-local SPIFFE Guard requests.\n\
# TYPE argus_guard_requests_total counter\n",
        );
        output.push_str(&format!(
            "argus_guard_requests_total{{decision=\"allow\"}} {allow}\n\
argus_guard_requests_total{{decision=\"deny\"}} {deny}\n"
        ));
        output.push_str(
            "# HELP argus_guard_in_flight Caller-local SPIFFE Guard requests currently executing.\n\
# TYPE argus_guard_in_flight gauge\n",
        );
        output.push_str(&format!("argus_guard_in_flight {in_flight}\n"));
        output.push_str(
            "# HELP argus_guard_decision_duration_seconds Caller-local SPIFFE Guard decision latency.\n\
# TYPE argus_guard_decision_duration_seconds histogram\n",
        );
        for (index, upper_bound) in GUARD_DURATION_BUCKETS_SECONDS.iter().enumerate() {
            let count = self.duration_buckets[index].load(Ordering::Relaxed);
            output.push_str(&format!(
                "argus_guard_decision_duration_seconds_bucket{{le=\"{upper_bound}\"}} {count}\n"
            ));
        }
        output.push_str(&format!(
            "argus_guard_decision_duration_seconds_bucket{{le=\"+Inf\"}} {requests}\n\
argus_guard_decision_duration_seconds_sum {duration_sum_seconds}\n\
argus_guard_decision_duration_seconds_count {requests}\n"
        ));
        output
    }
}

/// Health check response
#[derive(serde::Serialize)]
struct HealthResponse {
    status: String,
    version: String,
    mode: &'static str,
    authorization_context_required: bool,
    authorization_context_version: &'static str,
    decision_ttl_seconds: i64,
}

/// Caller-side business request context bound to a Guard decision.
#[derive(Clone, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorizationContext {
    pub version: String,
    pub request_id: String,
    pub request_digest: String,
    pub method: String,
    pub path_and_query: String,
    pub body_sha256: String,
    pub caller_spiffe_id: String,
    pub target_spiffe_id: String,
    pub target_service: String,
    pub target_uri: String,
    pub operation: String,
    pub data_class: String,
    pub issued_at_unix: i64,
    pub nonce: String,
}

/// Verification request from caller
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VerifyRequest {
    pub target: TargetService,
    pub caller_id: String,
    pub requested_claims: Option<Vec<RequestedClaim>>,
    pub verification_options: Option<VerificationOptions>,
    pub profile_digest: Option<String>,
    pub authorization_context: Option<AuthorizationContext>,
}

/// Verification response
#[derive(serde::Serialize)]
pub struct VerifyResponse {
    pub decision: String,
    pub reason: Option<String>,
    pub claims: Option<VerifiedClaims>,
    pub verification_mode: &'static str,
    pub decision_id: Option<String>,
    pub request_digest: Option<String>,
    pub expires_at_unix: Option<i64>,
}

struct DecisionReceipt {
    decision_id: String,
    request_digest: String,
    expires_at_unix: i64,
}

/// Guard context from request
impl From<&VerifyRequest> for GuardContext {
    fn from(req: &VerifyRequest) -> Self {
        GuardContext {
            caller_id: req.caller_id.clone(),
            requested_claims: req.requested_claims.clone().unwrap_or_default(),
            verification_options: req.verification_options.clone().unwrap_or_default(),
        }
    }
}

/// Health check handler
async fn health_handler(State(state): State<GuardAppState>) -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "OK".to_string(),
        version: "v1".to_string(),
        mode: state.mode.as_str(),
        authorization_context_required: state.require_authorization_context,
        authorization_context_version: AUTHORIZATION_CONTEXT_VERSION,
        decision_ttl_seconds: state.decision_ttl_seconds,
    })
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum GuardMode {
    Evidence,
    MockAllow,
    SpiffeIdentity,
}

impl GuardMode {
    fn from_environment() -> Result<Self> {
        match std::env::var("GUARD_MODE")
            .map_err(|_| anyhow::anyhow!("GUARD_MODE must be explicitly set"))?
            .to_ascii_lowercase()
            .as_str()
        {
            "evidence" => Ok(Self::Evidence),
            "mock_allow" => Ok(Self::MockAllow),
            "spiffe_identity" => Ok(Self::SpiffeIdentity),
            value => anyhow::bail!(
                "unsupported GUARD_MODE {value:?}; expected evidence, mock_allow, or spiffe_identity"
            ),
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Evidence => "evidence",
            Self::MockAllow => "mock_allow",
            Self::SpiffeIdentity => "spiffe_identity",
        }
    }
}

/// Caller-local authorization handler - POST /guard/v1/authorize.
async fn spiffe_authorize_handler(
    State(state): State<GuardAppState>,
    Json(request): Json<SpiffeAuthorizationRequest>,
) -> Result<Json<SpiffeAuthorizationResponse>, StatusCode> {
    if state.mode != GuardMode::SpiffeIdentity {
        return Err(StatusCode::NOT_FOUND);
    }
    let guard = state
        .spiffe_guard
        .as_ref()
        .ok_or(StatusCode::INTERNAL_SERVER_ERROR)?;
    let started = state.spiffe_metrics.begin();
    let response = guard.authorize(&request);
    state.spiffe_metrics.finish(response.decision, started);
    tracing::info!(
        request_id = %response.request_id,
        caller_spiffe_id = %request.caller_spiffe_id,
        target_spiffe_id = %request.target_spiffe_id,
        target_service = %request.target_service,
        target_origin = %request.target_origin,
        operation = request.operation.as_deref().unwrap_or("none"),
        data_class = request.data_class.as_deref().unwrap_or("none"),
        decision = ?response.decision,
        decision_id = %response.decision_id,
        policy_id = %response.policy_id,
        rule_id = response.rule_id.as_deref().unwrap_or("none"),
        reason = %response.reason,
        "caller-local SPIFFE authorization decision"
    );
    Ok(Json(response))
}

async fn metrics_handler(State(state): State<GuardAppState>) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "text/plain; version=0.0.4; charset=utf-8")
        .body(Body::from(state.spiffe_metrics.render()))
        .expect("static metrics response must be valid")
}

fn valid_ascii_token(value: &str, max_length: usize) -> bool {
    !value.is_empty()
        && value.len() <= max_length
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric()
                || matches!(byte, b'-' | b'_' | b'.' | b':' | b'/')
        })
}

fn valid_sha256(value: &str) -> bool {
    let Some(hex_digest) = value.strip_prefix("sha256:") else {
        return false;
    };
    hex_digest.len() == 64
        && hex_digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn append_authorization_field(buffer: &mut Vec<u8>, value: &str) -> Result<(), StatusCode> {
    if value.len() > MAX_AUTHORIZATION_FIELD_LENGTH {
        return Err(StatusCode::BAD_REQUEST);
    }
    let length = u32::try_from(value.len()).map_err(|_| StatusCode::BAD_REQUEST)?;
    buffer.extend_from_slice(&length.to_be_bytes());
    buffer.extend_from_slice(value.as_bytes());
    Ok(())
}

fn compute_authorization_digest(
    context: &AuthorizationContext,
) -> Result<String, StatusCode> {
    let mut canonical = Vec::with_capacity(1024);
    canonical.extend_from_slice(AUTHORIZATION_CONTEXT_DOMAIN);
    let issued_at_unix = context.issued_at_unix.to_string();
    for value in [
        context.version.as_str(),
        context.request_id.as_str(),
        context.method.as_str(),
        context.path_and_query.as_str(),
        context.body_sha256.as_str(),
        context.caller_spiffe_id.as_str(),
        context.target_spiffe_id.as_str(),
        context.target_service.as_str(),
        context.target_uri.as_str(),
        context.operation.as_str(),
        context.data_class.as_str(),
        issued_at_unix.as_str(),
        context.nonce.as_str(),
    ] {
        append_authorization_field(&mut canonical, value)?;
    }
    Ok(format!("sha256:{}", hex::encode(Sha256::digest(&canonical))))
}

fn validate_authorization_context(
    request: &VerifyRequest,
    context: &AuthorizationContext,
) -> Result<(), StatusCode> {
    if context.version != AUTHORIZATION_CONTEXT_VERSION
        || !valid_ascii_token(&context.request_id, 128)
        || !valid_sha256(&context.request_digest)
        || !valid_sha256(&context.body_sha256)
        || context.method.is_empty()
        || context.method.len() > 32
        || !context
            .method
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte == b'-')
        || !context.path_and_query.starts_with('/')
        || context.path_and_query.len() > MAX_AUTHORIZATION_FIELD_LENGTH
        || context
            .path_and_query
            .bytes()
            .any(|byte| byte.is_ascii_control())
        || !context.caller_spiffe_id.starts_with("spiffe://")
        || context.caller_spiffe_id.len() <= "spiffe://".len()
        || !valid_ascii_token(&context.caller_spiffe_id, 2048)
        || !context.target_spiffe_id.starts_with("spiffe://")
        || context.target_spiffe_id.len() <= "spiffe://".len()
        || !valid_ascii_token(&context.target_spiffe_id, 2048)
        || !valid_ascii_token(&context.target_service, 256)
        || context.target_uri.is_empty()
        || context.target_uri.len() > MAX_AUTHORIZATION_FIELD_LENGTH
        || context
            .target_uri
            .bytes()
            .any(|byte| byte.is_ascii_control())
        || !valid_ascii_token(&context.operation, 128)
        || !valid_ascii_token(&context.data_class, 128)
        || context.nonce.len() != 32
        || !context.nonce.bytes().all(|byte| byte.is_ascii_hexdigit())
        || context.caller_spiffe_id != request.caller_id
        || context.target_service != request.target.service_name
        || context.target_uri != request.target.target_uri
        || context.operation != format!("http:{}", context.method)
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    let now = Utc::now().timestamp();
    if context.issued_at_unix < now - MAX_AUTHORIZATION_CLOCK_SKEW_SECONDS
        || context.issued_at_unix > now + MAX_AUTHORIZATION_CLOCK_SKEW_SECONDS
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    if compute_authorization_digest(context)? != context.request_digest {
        return Err(StatusCode::BAD_REQUEST);
    }
    Ok(())
}

fn new_decision_id() -> Result<String, StatusCode> {
    let mut random_bytes = [0u8; 16];
    getrandom::getrandom(&mut random_bytes).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(hex::encode(random_bytes))
}

fn validated_request_digest(
    request: &VerifyRequest,
    require_authorization_context: bool,
) -> Result<Option<String>, StatusCode> {
    let Some(context) = request.authorization_context.as_ref() else {
        if require_authorization_context {
            return Err(StatusCode::BAD_REQUEST);
        }
        return Ok(None);
    };
    validate_authorization_context(request, context)?;
    Ok(Some(context.request_digest.clone()))
}

fn decision_receipt(
    request_digest: Option<&str>,
    decision_ttl_seconds: i64,
) -> Result<Option<DecisionReceipt>, StatusCode> {
    let Some(request_digest) = request_digest else {
        return Ok(None);
    };
    Ok(Some(DecisionReceipt {
        decision_id: new_decision_id()?,
        request_digest: request_digest.to_string(),
        expires_at_unix: Utc::now().timestamp() + decision_ttl_seconds,
    }))
}

fn response_with_receipt(
    decision: String,
    reason: Option<String>,
    claims: Option<VerifiedClaims>,
    verification_mode: &'static str,
    receipt: Option<DecisionReceipt>,
) -> VerifyResponse {
    let (decision_id, request_digest, expires_at_unix) = match receipt {
        Some(receipt) => (
            Some(receipt.decision_id),
            Some(receipt.request_digest),
            Some(receipt.expires_at_unix),
        ),
        None => (None, None, None),
    };
    VerifyResponse {
        decision,
        reason,
        claims,
        verification_mode,
        decision_id,
        request_digest,
        expires_at_unix,
    }
}

fn validate_mock_request(request: &VerifyRequest) -> Result<(), StatusCode> {
    if request.caller_id.trim().is_empty()
        || request.target.service_name.trim().is_empty()
        || request.target.target_uri.trim().is_empty()
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    Ok(())
}

fn mock_allow_response(
    request: &VerifyRequest,
    require_authorization_context: bool,
    decision_ttl_seconds: i64,
) -> Result<VerifyResponse, StatusCode> {
    validate_mock_request(request)?;
    let request_digest = validated_request_digest(
        request,
        require_authorization_context,
    )?;
    let receipt = decision_receipt(request_digest.as_deref(), decision_ttl_seconds)?;
    let decision_id = receipt
        .as_ref()
        .map(|receipt| receipt.decision_id.as_str())
        .unwrap_or("none");
    let log_request_id = request
        .authorization_context
        .as_ref()
        .map(|context| context.request_id.as_str())
        .unwrap_or("none");
    let log_request_digest = request_digest.as_deref().unwrap_or("none");
    tracing::warn!(
        caller_id = %request.caller_id,
        target_service = %request.target.service_name,
        target_uri = %request.target.target_uri,
        request_id = %log_request_id,
        request_digest = %log_request_digest,
        decision_id = %decision_id,
        "Guard returned mock ALLOW without fetching or verifying evidence"
    );
    Ok(response_with_receipt(
        "ALLOW".to_string(),
        Some("mock_allow connectivity mode; no evidence was fetched".to_string()),
        None,
        GuardMode::MockAllow.as_str(),
        receipt,
    ))
}

/// Verification handler - POST /ra/v1/verify
async fn verify_handler(
    State(state): State<GuardAppState>,
    Json(request): Json<VerifyRequest>,
) -> Result<Json<VerifyResponse>, StatusCode> {
    if state.mode == GuardMode::SpiffeIdentity {
        return Err(StatusCode::NOT_FOUND);
    }
    if state.mode == GuardMode::MockAllow {
        return mock_allow_response(
            &request,
            state.require_authorization_context,
            state.decision_ttl_seconds,
        )
        .map(Json);
    }

    let request_digest = validated_request_digest(
        &request,
        state.require_authorization_context,
    )?;

    // Build guard context from request
    let context = GuardContext::from(&request);

    // Build evidence request
    let evidence_request = EvidenceRequest {
        version: "v1".to_string(),
        nonce: generate_nonce(),
        caller_id: request.caller_id.clone(),
        target: Some(request.target.clone()),
        requested_claims: context.requested_claims.clone(),
        profile_digest: request.profile_digest.clone(),
    };

    // Fetch evidence from peer
    let evidence = state
        .evidence_fetcher
        .request_evidence(&evidence_request)
        .await
        .map_err(|e| {
            tracing::error!("Evidence fetch failed: {}", e);
            StatusCode::BAD_GATEWAY
        })?;

    // Build expected binding for verification
    let expected_binding = ExpectedBinding {
        algorithm: BINDING_ALGORITHM.to_string(),
        report_data: evidence.report_data.clone(),
        canonical_request_digest: evidence.nonce_binding.canonical_request_digest.clone(),
    };

    // Verify evidence
    let verified_claims = state
        .ra_adapter
        .verify_evidence(&evidence, &expected_binding, &context.verification_options)
        .await
        .map_err(|e| {
            tracing::error!("Verification failed: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    // Evaluate policy
    let decision = state
        .policy_evaluator
        .evaluate_policy(&request.target, &verified_claims, &context)
        .await;
    let receipt = decision_receipt(
        request_digest.as_deref(),
        state.decision_ttl_seconds,
    )?;

    // Convert decision to response
    let response = match decision {
        GuardDecision::Allow(claims) => response_with_receipt(
            "ALLOW".to_string(),
            None,
            Some(claims),
            GuardMode::Evidence.as_str(),
            receipt,
        ),
        GuardDecision::Deny { reason, claims } => response_with_receipt(
            "DENY".to_string(),
            Some(format!("{:?}", reason)),
            claims,
            GuardMode::Evidence.as_str(),
            receipt,
        ),
    };

    Ok(Json(response))
}

/// Batch verification request
#[derive(serde::Deserialize)]
pub struct BatchVerifyRequest {
    pub requests: Vec<VerifyRequest>,
}

/// Batch verification response
#[derive(serde::Serialize)]
pub struct BatchVerifyResponse {
    pub results: Vec<VerifyResponse>,
}

/// Batch verification handler - POST /ra/v1/verify/batch
async fn batch_verify_handler(
    State(state): State<GuardAppState>,
    Json(request): Json<BatchVerifyRequest>,
) -> Result<Json<BatchVerifyResponse>, StatusCode> {
    if state.mode == GuardMode::SpiffeIdentity {
        return Err(StatusCode::NOT_FOUND);
    }
    let mut results = Vec::with_capacity(request.requests.len());

    for req in request.requests {
        if state.mode == GuardMode::MockAllow {
            match mock_allow_response(
                &req,
                state.require_authorization_context,
                state.decision_ttl_seconds,
            ) {
                Ok(response) => results.push(response),
                Err(_) => results.push(response_with_receipt(
                    "ERROR".to_string(),
                    Some(
                        "caller, target, and a valid authorization_context are required"
                            .to_string(),
                    ),
                    None,
                    GuardMode::MockAllow.as_str(),
                    None,
                )),
            }
            continue;
        }

        let request_digest = match validated_request_digest(
            &req,
            state.require_authorization_context,
        ) {
            Ok(request_digest) => request_digest,
            Err(_) => {
                results.push(response_with_receipt(
                    "ERROR".to_string(),
                    Some("authorization_context validation failed".to_string()),
                    None,
                    GuardMode::Evidence.as_str(),
                    None,
                ));
                continue;
            }
        };

        // Build guard context from request
        let context = GuardContext::from(&req);

        // Build evidence request
        let evidence_request = EvidenceRequest {
            version: "v1".to_string(),
            nonce: generate_nonce(),
            caller_id: req.caller_id.clone(),
            target: Some(req.target.clone()),
            requested_claims: context.requested_claims.clone(),
            profile_digest: req.profile_digest.clone(),
        };

        // Fetch evidence
        let evidence = match state.evidence_fetcher.request_evidence(&evidence_request).await {
            Ok(e) => e,
            Err(e) => {
                tracing::error!("Evidence fetch failed: {}", e);
                results.push(response_with_receipt(
                    "ERROR".to_string(),
                    Some(format!("Evidence fetch failed: {}", e)),
                    None,
                    GuardMode::Evidence.as_str(),
                    None,
                ));
                continue;
            }
        };

        // Build expected binding
        let expected_binding = ExpectedBinding {
            algorithm: BINDING_ALGORITHM.to_string(),
            report_data: evidence.report_data.clone(),
            canonical_request_digest: evidence.nonce_binding.canonical_request_digest.clone(),
        };

        // Verify evidence
        let verified_claims = match state
            .ra_adapter
            .verify_evidence(&evidence, &expected_binding, &context.verification_options)
            .await
        {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("Verification failed: {}", e);
                results.push(response_with_receipt(
                    "ERROR".to_string(),
                    Some(format!("Verification failed: {}", e)),
                    None,
                    GuardMode::Evidence.as_str(),
                    None,
                ));
                continue;
            }
        };

        // Evaluate policy
        let decision = state
            .policy_evaluator
            .evaluate_policy(&req.target, &verified_claims, &context)
            .await;
        let receipt = match decision_receipt(
            request_digest.as_deref(),
            state.decision_ttl_seconds,
        ) {
            Ok(receipt) => receipt,
            Err(_) => {
                results.push(response_with_receipt(
                    "ERROR".to_string(),
                    Some("decision receipt generation failed".to_string()),
                    None,
                    GuardMode::Evidence.as_str(),
                    None,
                ));
                continue;
            }
        };

        // Convert decision to response
        let response = match decision {
            GuardDecision::Allow(claims) => response_with_receipt(
                "ALLOW".to_string(),
                None,
                Some(claims),
                GuardMode::Evidence.as_str(),
                receipt,
            ),
            GuardDecision::Deny { reason, claims } => response_with_receipt(
                "DENY".to_string(),
                Some(format!("{:?}", reason)),
                claims,
                GuardMode::Evidence.as_str(),
                receipt,
            ),
        };

        results.push(response);
    }

    Ok(Json(BatchVerifyResponse { results }))
}

/// Configuration for the Guard server
#[derive(Clone)]
pub struct GuardConfig {
    pub host: String,
    pub port: u16,
    pub evidence_endpoint: String,
    pub policy_type: PolicyType,
}

/// Policy type for evaluation
#[derive(Clone, Copy, Debug, Default)]
pub enum PolicyType {
    #[default]
    AllowAll,
    Strict,
}

fn boolean_environment(name: &str, default: bool) -> Result<bool> {
    let Ok(raw) = std::env::var(name) else {
        return Ok(default);
    };
    match raw.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => anyhow::bail!("{name} must be a boolean"),
    }
}

fn decision_ttl_from_environment() -> Result<i64> {
    let ttl = std::env::var("GUARD_DECISION_TTL_SECONDS")
        .unwrap_or_else(|_| "15".to_string())
        .parse::<i64>()
        .map_err(|_| anyhow::anyhow!("GUARD_DECISION_TTL_SECONDS must be an integer"))?;
    if !(1..=300).contains(&ttl) {
        anyhow::bail!("GUARD_DECISION_TTL_SECONDS must be between 1 and 300");
    }
    Ok(ttl)
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize tracing with plain (non-ANSI) output so service logs remain
    // machine-greppable for the same-request audit correlation chain.
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_ansi(false)
        .init();

    // Get configuration from environment
    let guard_mode = GuardMode::from_environment()?;
    let host = std::env::var("HOST").unwrap_or_else(|_| {
        if guard_mode == GuardMode::SpiffeIdentity {
            "127.0.0.1".to_string()
        } else {
            "0.0.0.0".to_string()
        }
    });
    let port: u16 = std::env::var("PORT")
        .unwrap_or_else(|_| "8007".to_string())
        .parse()
        .map_err(|_| anyhow::anyhow!("PORT must be an integer between 1 and 65535"))?;
    if port == 0 {
        anyhow::bail!("PORT must be an integer between 1 and 65535");
    }
    let allow_incomplete_evidence =
        boolean_environment("GUARD_ALLOW_INCOMPLETE_EVIDENCE", false)?;
    if guard_mode == GuardMode::Evidence && !allow_incomplete_evidence {
        anyhow::bail!(
            "GUARD_MODE=evidence is disabled until expected binding and policy validation are completed; \
             set GUARD_ALLOW_INCOMPLETE_EVIDENCE=1 only for isolated development"
        );
    }
    let require_authorization_context = if guard_mode == GuardMode::SpiffeIdentity {
        false
    } else {
        boolean_environment("GUARD_REQUIRE_AUTHORIZATION_CONTEXT", true)?
    };
    let spiffe_guard = if guard_mode == GuardMode::SpiffeIdentity {
        let policy_file = std::env::var("GUARD_SPIFFE_POLICY_FILE").map_err(|_| {
            anyhow::anyhow!(
                "GUARD_SPIFFE_POLICY_FILE must be set when GUARD_MODE=spiffe_identity"
            )
        })?;
        let guard = SpiffeGuard::from_yaml_file(&policy_file)?;
        tracing::info!(
            policy_file = %policy_file,
            policy_id = %guard.policy_id(),
            "loaded caller-local SPIFFE authorization policy"
        );
        Some(Arc::new(guard))
    } else {
        None
    };
    let decision_ttl_seconds = match spiffe_guard.as_ref() {
        Some(guard) => guard.decision_ttl_seconds() as i64,
        None => decision_ttl_from_environment()?,
    };
    let evidence_endpoint = std::env::var("EVIDENCE_ENDPOINT")
        .unwrap_or_else(|_| "http://localhost:8006".to_string());
    
    // TC-API configuration for Agent-side metadata (optional)
    // When AGENT_TC_API_URL is set, Guard will use local TC-API for its own identity
    // This is needed when the Agent itself is also a TDX workload requiring remote attestation
    let agent_tc_api_url = std::env::var("AGENT_TC_API_URL").ok();
    let agent_tc_api_token = std::env::var("TRUCON_SERVICE_TOKEN").ok();

    // Create TC-API client for Agent-side if configured
    let tc_api_client = if let Some(ref url) = agent_tc_api_url {
        tracing::info!("Agent TC-API configured: {}", url);
        let mut client = TcApiClient::new(url);
        if let Some(ref token) = agent_tc_api_token {
            tracing::info!("Agent TC-API auth token configured");
            client = client.with_auth_token(token);
        }
        Some(Arc::new(client))
    } else {
        tracing::info!("Agent TC-API not configured (Agent is not a TDX workload)");
        None
    };

    // Create Argus Engine
    let engine = Arc::new(ArgusEngine::new());

    // Create evidence fetcher with peer endpoint
    let evidence_fetcher = Arc::new(EvidenceFetcherHttp::new(&evidence_endpoint));

    // Create RA adapter
    let ra_adapter = Arc::new(RaAdapter::new());

    // Create policy evaluator (AllowAll for now)
    let policy_evaluator: Arc<dyn PolicyEvaluatorTrait> = Arc::new(AllowAllPolicyEvaluator::new());

    // Create app state
    let state = GuardAppState {
        engine,
        evidence_fetcher,
        ra_adapter,
        policy_evaluator,
        mode: guard_mode,
        require_authorization_context,
        decision_ttl_seconds,
        tc_api_client,
        spiffe_guard,
        spiffe_metrics: Arc::new(SpiffeGuardMetrics::default()),
    };

    // Configure CORS
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(vec![Method::GET, Method::POST, Method::OPTIONS])
        .allow_headers(Any);

    // Build router
    let app = Router::new()
        .route("/health", get(health_handler))
        .route("/metrics", get(metrics_handler))
        .route("/guard/v1/authorize", post(spiffe_authorize_handler))
        .route("/ra/v1/verify", post(verify_handler))
        .route("/ra/v1/verify/batch", post(batch_verify_handler))
        .with_state(state);
    let app = if guard_mode == GuardMode::SpiffeIdentity {
        app
    } else {
        app.layer(cors)
    };

    // Parse address
    let addr: SocketAddr = format!("{}:{}", host, port)
        .parse()
        .expect("Failed to parse address");

    tracing::info!("Argus Guard starting on {}", addr);
    tracing::info!("Verification endpoint: POST /ra/v1/verify");
    tracing::info!("Batch verification endpoint: POST /ra/v1/verify/batch");
    tracing::info!("SPIFFE authorization endpoint: POST /guard/v1/authorize");
    tracing::info!("Health endpoint: GET /health");
    tracing::info!("Metrics endpoint: GET /metrics");
    tracing::info!("Guard mode: {}", guard_mode.as_str());
    tracing::info!(
        "Authorization context required: {}; decision TTL: {} seconds",
        require_authorization_context,
        decision_ttl_seconds
    );
    match guard_mode {
        GuardMode::Evidence => tracing::info!("Evidence endpoint: {}", evidence_endpoint),
        GuardMode::MockAllow => tracing::warn!(
            "mock_allow is connectivity-only: Evidence Provider and verifier are bypassed"
        ),
        GuardMode::SpiffeIdentity => tracing::info!(
            "spiffe_identity performs caller-local policy evaluation; SPIRE and the TLS client verify workload identities"
        ),
    }
    
    if agent_tc_api_url.is_some() {
        tracing::info!("Agent-side TC-API: enabled (Agent is a TDX workload)");
    } else {
        tracing::info!("Agent-side TC-API: disabled (Agent is not a TDX workload)");
    }

    // Start server
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

#[cfg(test)]
mod metrics_tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn spiffe_metrics_expose_decisions_latency_and_in_flight() {
        let metrics = SpiffeGuardMetrics::default();
        let started = metrics.begin() - Duration::from_millis(2);
        assert_eq!(metrics.in_flight.load(Ordering::Relaxed), 1);

        metrics.finish(SpiffeAuthorizationDecision::Allow, started);
        let rendered = metrics.render();

        assert!(rendered.contains(
            "argus_guard_requests_total{decision=\"allow\"} 1"
        ));
        assert!(rendered.contains(
            "argus_guard_requests_total{decision=\"deny\"} 0"
        ));
        assert!(rendered.contains("argus_guard_in_flight 0"));
        assert!(rendered.contains("argus_guard_decision_duration_seconds_count 1"));
        assert!(rendered.contains(
            "argus_guard_decision_duration_seconds_bucket{le=\"+Inf\"} 1"
        ));
    }
}

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

use anyhow::{bail, Result};
use argus::{
    engine::{EvidenceFetcher, EvidenceFetcherHttp, PolicyEvaluatorTrait, RaVerifier},
    policy::PolicyEvaluator,
    spiffe_guard::{
        SpiffeAuthorizationDecision, SpiffeAuthorizationRequest, SpiffeAuthorizationResponse,
        SpiffeGuard,
    },
    types::*,
    verifier::RaAdapter,
};
use axum::{
    body::Body,
    extract::{DefaultBodyLimit, State},
    http::{header::AUTHORIZATION, HeaderMap, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use std::net::SocketAddr;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use std::time::Instant;

/// Application state for the Guard HTTP server.
#[derive(Clone)]
struct GuardAppState {
    runtime: GuardRuntime,
    api_token: Option<Arc<str>>,
}

#[derive(Clone)]
enum GuardRuntime {
    Evidence(Arc<EvidenceGuardState>),
    SpiffeIdentity(Arc<SpiffeGuardState>),
}

struct EvidenceGuardState {
    evidence_fetcher: Arc<EvidenceFetcherHttp>,
    ra_adapter: Arc<RaAdapter>,
    policy_evaluator: Arc<dyn PolicyEvaluatorTrait>,
}

struct SpiffeGuardState {
    guard: SpiffeGuard,
    metrics: SpiffeGuardMetrics,
}

const MAX_BATCH_SIZE: usize = 32;
const MAX_REQUEST_BODY_BYTES: usize = 1024 * 1024;
const GUARD_DURATION_BUCKETS_SECONDS: [f64; 13] = [
    0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum GuardMode {
    Evidence,
    SpiffeIdentity,
}

impl GuardMode {
    fn from_environment() -> Result<Self> {
        match std::env::var("GUARD_MODE") {
            Err(std::env::VarError::NotPresent) => Ok(Self::Evidence),
            Err(error) => Err(error.into()),
            Ok(value) if value == "evidence" => Ok(Self::Evidence),
            Ok(value) if value == "spiffe_identity" => Ok(Self::SpiffeIdentity),
            Ok(value) => {
                bail!("unsupported GUARD_MODE {value:?}; expected evidence or spiffe_identity")
            }
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Evidence => "evidence",
            Self::SpiffeIdentity => "spiffe_identity",
        }
    }
}

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
        for (index, upper_bound) in GUARD_DURATION_BUCKETS_SECONDS.iter().enumerate() {
            if elapsed.as_secs_f64() <= *upper_bound {
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

fn authorize(headers: &HeaderMap, expected_token: Option<&str>) -> Result<(), StatusCode> {
    let Some(expected_token) = expected_token else {
        return Ok(());
    };
    let supplied_token = headers
        .get(AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "));

    if supplied_token == Some(expected_token) {
        Ok(())
    } else {
        Err(StatusCode::UNAUTHORIZED)
    }
}

/// Health check response
#[derive(serde::Serialize)]
struct HealthResponse {
    status: String,
    version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    decision_ttl_seconds: Option<u64>,
}

/// Verification request from caller
#[derive(serde::Deserialize)]
pub struct VerifyRequest {
    pub target: TargetService,
    pub caller_id: String,
    pub requested_claims: Option<Vec<RequestedClaim>>,
    pub verification_options: Option<VerificationOptions>,
    pub profile_digest: Option<String>,
}

/// Verification response
#[derive(serde::Serialize)]
pub struct VerifyResponse {
    pub decision: String,
    pub reason: Option<String>,
    pub claims: Option<VerifiedClaims>,
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
    let (mode, decision_ttl_seconds) = match &state.runtime {
        GuardRuntime::Evidence(_) => (None, None),
        GuardRuntime::SpiffeIdentity(runtime) => (
            Some(GuardMode::SpiffeIdentity.as_str()),
            Some(runtime.guard.decision_ttl_seconds()),
        ),
    };
    Json(HealthResponse {
        status: "OK".to_string(),
        version: "v1".to_string(),
        mode,
        decision_ttl_seconds,
    })
}

fn api_token_from_environment() -> Result<Option<Arc<str>>> {
    let token = std::env::var("ARGUS_API_TOKEN")
        .ok()
        .filter(|value| !value.is_empty());
    let token_file = std::env::var("ARGUS_API_TOKEN_FILE")
        .ok()
        .filter(|value| !value.is_empty());
    if token.is_some() && token_file.is_some() {
        bail!("set only one of ARGUS_API_TOKEN or ARGUS_API_TOKEN_FILE");
    }

    let token = match (token, token_file) {
        (Some(value), None) => Some(value),
        (None, Some(path)) => {
            let value = std::fs::read_to_string(&path).map_err(|error| {
                anyhow::anyhow!("failed to read ARGUS_API_TOKEN_FILE {path:?}: {error}")
            })?;
            Some(
                value
                    .trim_end_matches(|character| character == '\r' || character == '\n')
                    .to_string(),
            )
        }
        (None, None) => None,
        (Some(_), Some(_)) => unreachable!(),
    };
    if let Some(value) = token.as_deref() {
        if value.is_empty() || value.chars().any(char::is_whitespace) {
            bail!("Argus API token must be non-empty and contain no whitespace");
        }
    }
    Ok(token.map(Arc::<str>::from))
}

/// Caller-local SPIFFE authorization handler - POST /guard/v1/authorize.
async fn spiffe_authorize_handler(
    State(state): State<GuardAppState>,
    headers: HeaderMap,
    Json(request): Json<SpiffeAuthorizationRequest>,
) -> Result<Json<SpiffeAuthorizationResponse>, StatusCode> {
    let GuardRuntime::SpiffeIdentity(runtime) = &state.runtime else {
        return Err(StatusCode::NOT_FOUND);
    };
    authorize(&headers, state.api_token.as_deref())?;

    let started = runtime.metrics.begin();
    let response = runtime.guard.authorize(&request);
    runtime.metrics.finish(response.decision, started);
    tracing::info!(
        request_id = %request.request_id,
        caller_spiffe_id = %request.caller_spiffe_id,
        target_spiffe_id = %request.target_spiffe_id,
        target_service = %request.target_service,
        target_origin = %request.target_origin,
        operation = ?request.operation,
        data_class = ?request.data_class,
        decision = ?response.decision,
        decision_id = %response.decision_id,
        policy_id = %response.policy_id,
        rule_id = ?response.rule_id,
        "caller-local SPIFFE authorization decision"
    );
    Ok(Json(response))
}

async fn metrics_handler(State(state): State<GuardAppState>) -> Result<Response, StatusCode> {
    let GuardRuntime::SpiffeIdentity(runtime) = &state.runtime else {
        return Err(StatusCode::NOT_FOUND);
    };
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "text/plain; version=0.0.4; charset=utf-8")
        .body(Body::from(runtime.metrics.render()))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

/// Verification handler - POST /ra/v1/verify
async fn verify_handler(
    State(state): State<GuardAppState>,
    headers: HeaderMap,
    Json(request): Json<VerifyRequest>,
) -> Result<Json<VerifyResponse>, StatusCode> {
    let GuardRuntime::Evidence(runtime) = &state.runtime else {
        return Err(StatusCode::NOT_FOUND);
    };
    authorize(&headers, state.api_token.as_deref())?;

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
    let evidence = runtime
        .evidence_fetcher
        .request_evidence(&evidence_request)
        .await
        .map_err(|e| {
            tracing::error!("Evidence fetch failed: {}", e);
            StatusCode::BAD_GATEWAY
        })?;

    // Build expected binding for verification
    let binding_claims = evidence
        .binding_claims
        .as_ref()
        .ok_or(StatusCode::UNPROCESSABLE_ENTITY)?;
    let expected_binding =
        ExpectedBinding::from_request_and_claims(&evidence_request, binding_claims);

    // Verify evidence
    let verified_claims = runtime
        .ra_adapter
        .verify_evidence(&evidence, &expected_binding, &context.verification_options)
        .await
        .map_err(|e| {
            tracing::error!("Verification failed: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    // Evaluate policy
    let decision = runtime
        .policy_evaluator
        .evaluate_policy(&request.target, &verified_claims, &context)
        .await;

    // Convert decision to response
    let response = match decision {
        GuardDecision::Allow(claims) => VerifyResponse {
            decision: "ALLOW".to_string(),
            reason: None,
            claims: Some(claims),
        },
        GuardDecision::Deny { reason, claims } => VerifyResponse {
            decision: "DENY".to_string(),
            reason: Some(format!("{:?}", reason)),
            claims,
        },
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
    headers: HeaderMap,
    Json(request): Json<BatchVerifyRequest>,
) -> Result<Json<BatchVerifyResponse>, StatusCode> {
    let GuardRuntime::Evidence(runtime) = &state.runtime else {
        return Err(StatusCode::NOT_FOUND);
    };
    authorize(&headers, state.api_token.as_deref())?;
    if request.requests.is_empty() || request.requests.len() > MAX_BATCH_SIZE {
        return Err(StatusCode::PAYLOAD_TOO_LARGE);
    }

    let mut results = Vec::with_capacity(request.requests.len());

    for req in request.requests {
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
        let evidence = match runtime
            .evidence_fetcher
            .request_evidence(&evidence_request)
            .await
        {
            Ok(e) => e,
            Err(e) => {
                tracing::error!("Evidence fetch failed: {}", e);
                results.push(VerifyResponse {
                    decision: "ERROR".to_string(),
                    reason: Some(format!("Evidence fetch failed: {}", e)),
                    claims: None,
                });
                continue;
            }
        };

        // Build expected binding
        let Some(binding_claims) = evidence.binding_claims.as_ref() else {
            results.push(VerifyResponse {
                decision: "ERROR".to_string(),
                reason: Some("Evidence did not contain binding claims".to_string()),
                claims: None,
            });
            continue;
        };
        let expected_binding =
            ExpectedBinding::from_request_and_claims(&evidence_request, binding_claims);

        // Verify evidence
        let verified_claims = match runtime
            .ra_adapter
            .verify_evidence(&evidence, &expected_binding, &context.verification_options)
            .await
        {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("Verification failed: {}", e);
                results.push(VerifyResponse {
                    decision: "ERROR".to_string(),
                    reason: Some(format!("Verification failed: {}", e)),
                    claims: None,
                });
                continue;
            }
        };

        // Evaluate policy
        let decision = runtime
            .policy_evaluator
            .evaluate_policy(&req.target, &verified_claims, &context)
            .await;

        // Convert decision to response
        let response = match decision {
            GuardDecision::Allow(claims) => VerifyResponse {
                decision: "ALLOW".to_string(),
                reason: None,
                claims: Some(claims),
            },
            GuardDecision::Deny { reason, claims } => VerifyResponse {
                decision: "DENY".to_string(),
                reason: Some(format!("{:?}", reason)),
                claims,
            },
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

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt::init();

    // Intel's evidence verification path remains the default. Argus v2 enables
    // caller-local SPIFFE authorization explicitly with GUARD_MODE=spiffe_identity.
    let guard_mode = GuardMode::from_environment()?;
    let host = std::env::var("HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port: u16 = std::env::var("PORT")
        .unwrap_or_else(|_| "8007".to_string())
        .parse()
        .map_err(|_| anyhow::anyhow!("PORT must be an integer between 1 and 65535"))?;
    if port == 0 {
        bail!("PORT must be an integer between 1 and 65535");
    }

    let api_token = api_token_from_environment()?;
    if host != "127.0.0.1" && host != "::1" && host != "localhost" && api_token.is_none() {
        bail!(
            "ARGUS_API_TOKEN or ARGUS_API_TOKEN_FILE is required when Guard listens on a non-loopback address"
        );
    }

    let runtime = match guard_mode {
        GuardMode::Evidence => {
            let evidence_endpoint = std::env::var("EVIDENCE_ENDPOINT")
                .unwrap_or_else(|_| "http://localhost:8006".to_string());
            let intel_ca_cert_path = std::env::var("INTEL_CA_CERT_PATH")
                .map_err(|_| anyhow::anyhow!("INTEL_CA_CERT_PATH is required"))?;
            let intel_ca_cert = std::fs::read(&intel_ca_cert_path)?;
            let policy_evaluator: Arc<dyn PolicyEvaluatorTrait> = Arc::new(PolicyEvaluator::new());
            GuardRuntime::Evidence(Arc::new(EvidenceGuardState {
                evidence_fetcher: Arc::new(EvidenceFetcherHttp::new(&evidence_endpoint)),
                ra_adapter: Arc::new(RaAdapter::with_intel_ca_cert(&intel_ca_cert)),
                policy_evaluator,
            }))
        }
        GuardMode::SpiffeIdentity => {
            let policy_file = std::env::var("GUARD_SPIFFE_POLICY_FILE").map_err(|_| {
                anyhow::anyhow!(
                    "GUARD_SPIFFE_POLICY_FILE is required when GUARD_MODE=spiffe_identity"
                )
            })?;
            let guard = SpiffeGuard::from_yaml_file(&policy_file)?;
            tracing::info!(
                policy_file = %policy_file,
                policy_id = %guard.policy_id(),
                "loaded Argus v2 caller-local SPIFFE authorization policy"
            );
            GuardRuntime::SpiffeIdentity(Arc::new(SpiffeGuardState {
                guard,
                metrics: SpiffeGuardMetrics::default(),
            }))
        }
    };
    let state = GuardAppState { runtime, api_token };

    // Build router
    let app = Router::new().route("/health", get(health_handler));
    let app = match guard_mode {
        GuardMode::Evidence => app
            .route("/ra/v1/verify", post(verify_handler))
            .route("/ra/v1/verify/batch", post(batch_verify_handler)),
        GuardMode::SpiffeIdentity => app
            .route("/metrics", get(metrics_handler))
            .route("/guard/v1/authorize", post(spiffe_authorize_handler)),
    };
    let app = app
        .with_state(state)
        .layer(DefaultBodyLimit::max(MAX_REQUEST_BODY_BYTES));

    // Parse address
    let addr: SocketAddr = format!("{}:{}", host, port)
        .parse()
        .expect("Failed to parse address");

    tracing::info!("Argus Guard starting on {}", addr);
    tracing::info!("Health endpoint: GET /health");
    tracing::info!("Guard mode: {}", guard_mode.as_str());
    match guard_mode {
        GuardMode::Evidence => {
            tracing::info!("Verification endpoint: POST /ra/v1/verify");
            tracing::info!("Batch verification endpoint: POST /ra/v1/verify/batch");
        }
        GuardMode::SpiffeIdentity => {
            tracing::info!("SPIFFE authorization endpoint: POST /guard/v1/authorize");
            tracing::info!("Metrics endpoint: GET /metrics");
        }
    }

    // Start server
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn spiffe_metrics_expose_decisions_latency_and_in_flight() {
        let metrics = SpiffeGuardMetrics::default();
        let started = metrics.begin() - Duration::from_millis(2);
        assert_eq!(metrics.in_flight.load(Ordering::Relaxed), 1);

        metrics.finish(SpiffeAuthorizationDecision::Allow, started);
        let rendered = metrics.render();

        assert!(rendered.contains("argus_guard_requests_total{decision=\"allow\"} 1"));
        assert!(rendered.contains("argus_guard_requests_total{decision=\"deny\"} 0"));
        assert!(rendered.contains("argus_guard_in_flight 0"));
        assert!(rendered.contains("argus_guard_decision_duration_seconds_count 1"));
    }
}

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

//! Argus v1 - Application-non-invasive runtime trust verification framework
//!
//! This library provides Intel TDX quote verification for agent-to-service
//! communication. It enables callers to verify that peer services run in
//! expected Intel TDX trusted execution environments.
//!
//! # Core Components
//!
//! - [`ArgusEngine`] - Main facade for end-to-end target verification
//! - [`EvidenceEngine`] - Service-side evidence generation
//! - [`RaAdapter`] - TDX verifier abstraction
//! - [`PolicyEvaluator`] - Caller-side policy evaluation
//!
//! # Usage
//!
//! ```rust,no_run
//! use argus::{ArgusEngine, TargetService, GuardContext, engine::MockEvidenceFetcher, policy::AllowAllPolicyEvaluator};
//! use std::sync::Arc;
//!
//! #[tokio::main]
//! async fn main() -> anyhow::Result<()> {
//!     // Compile-only wiring example. A mock evidence fetcher is not accepted
//!     // as production attestation evidence by the strict RA adapter.
//!     let engine = ArgusEngine::with_components(
//!         Arc::new(MockEvidenceFetcher::new()),
//!         Arc::new(argus::RaAdapter::new()),
//!         Arc::new(AllowAllPolicyEvaluator::new()),
//!     );
//!     let target = TargetService::new("my-service", "https://my-service.local");
//!     let context = GuardContext::new("caller-1", vec![]);
//!
//!     let decision = engine.verify_target(&target, &context).await?;
//!     println!("Decision: {:?}", decision);
//!     Ok(())
//! }
//! ```

pub mod binding;
mod crypto_verifier;
pub mod engine;
pub mod errors;
pub mod policy;
pub mod service;
pub mod spiffe_guard;
pub mod tc_api_client;
pub mod tdx_verifier;
pub mod types;
pub mod verifier;

pub use binding::ServiceRuntimeBinding;
pub use engine::{
    ArgusEngine, EvidenceFetcher, MockEvidenceFetcher, PolicyEvaluatorTrait, RaVerifier,
};
pub use errors::{ArgusError, EvidenceError, Result};
pub use policy::{
    AllowAllPolicyEvaluator, CompositeRequirementConfig, ConfigurablePolicyEvaluator,
    DenyAllPolicyEvaluator, PolicyConfig, PolicyEvaluator,
};
pub use service::EvidenceEngine;
pub use spiffe_guard::{
    SpiffeAuthorizationDecision, SpiffeAuthorizationRequest, SpiffeAuthorizationResponse,
    SpiffeGuard, SpiffeGuardPolicy,
};
pub use tc_api_client::{ServiceMetadataFetcher, ServiceMetadataResponse, TcApiClient};
pub use types::*;
pub use verifier::RaAdapter;

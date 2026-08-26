package protocol

import "encoding/json"

// Version identifies the JSON contract shared with the Evidence Provider and
// Trustee. The plugin rejects Trustee verdicts that echo another version.
const Version = 1

// EvidenceRequest asks the agent-local Evidence Provider to collect evidence
// for the PID carried by a SPIFFE Broker workload reference. The nonce binds
// that collection to one attestation attempt.
type EvidenceRequest struct {
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
	PID             int32  `json:"pid"`
}

// VerifyRequest sends the collected evidence to the Trustee for appraisal.
// Evidence stays opaque to the plugin; the Trustee owns evidence semantics and
// the allow/deny decision.
type VerifyRequest struct {
	ProtocolVersion int             `json:"protocol_version"`
	Nonce           string          `json:"nonce"`
	PID             int32           `json:"pid"`
	Evidence        json.RawMessage `json:"evidence"`
}

// Verdict is the Trustee's appraisal result. ProtocolVersion, Nonce, and PID
// bind the result to its request; WorkloadID and PolicyID become selectors only
// after the plugin validates that binding and an explicit allow decision.
type Verdict struct {
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
	PID             int32  `json:"pid"`
	Decision        string `json:"decision"`
	StableErrorCode string `json:"stable_error_code"`
	WorkloadID      string `json:"workload_id"`
	PolicyID        string `json:"policy_id"`
}

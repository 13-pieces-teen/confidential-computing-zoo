package protocol

import "encoding/json"

const Version = 1

type EvidenceRequest struct {
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
	PID             int32  `json:"pid"`
}

type VerifyRequest struct {
	ProtocolVersion int             `json:"protocol_version"`
	Nonce           string          `json:"nonce"`
	PID             int32           `json:"pid"`
	Evidence        json.RawMessage `json:"evidence"`
}

type Verdict struct {
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
	PID             int32  `json:"pid"`
	Decision        string `json:"decision"`
	StableErrorCode string `json:"stable_error_code"`
	WorkloadID      string `json:"workload_id"`
	PolicyID        string `json:"policy_id"`
}

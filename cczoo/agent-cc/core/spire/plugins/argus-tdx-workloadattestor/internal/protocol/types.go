// Package protocol uses the frozen contract shared with Helper and the Provider.
package protocol

import shared "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/protocol"

const Version = shared.Version

type Target = shared.Target
type RuntimeData = shared.RuntimeData
type EvidenceRequest = shared.EvidenceRequest
type Evidence = shared.Evidence

var Decode = shared.Decode

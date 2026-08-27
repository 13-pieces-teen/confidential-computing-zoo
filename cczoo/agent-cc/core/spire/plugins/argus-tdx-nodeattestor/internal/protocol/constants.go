package protocol

const (
	FixedAgentSPIFFEID = "spiffe://argus.local/spire/agent/argus_tdx/openviking-node"

	PublicKeySize = 32
	NonceSize     = 32
	SignatureSize = 64

	MaxAgentHelloSize           = 4 << 10
	MaxChallengeSize            = 4 << 10
	MaxQuoteSize                = 4 << 20
	MaxNodeEvidenceResponseSize = MaxQuoteSize + 4<<10

	reportDataDomain = "argus.node.tdx.reportdata"
	transcriptDomain = "argus.node.tdx.transcript"
)

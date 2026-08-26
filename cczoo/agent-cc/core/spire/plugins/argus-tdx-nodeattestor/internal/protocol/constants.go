package protocol

const (
	// Version is the private Agent/Server handshake version, independent of the
	// SPIRE NodeAttestor RPC version.
	Version = uint32(1)

	PublicKeySize     = 32
	NonceSize         = 32
	SessionIDSize     = 32
	SignatureSize     = 64
	MaxAgentHelloSize = 4 << 10
	MaxChallengeSize  = 64 << 10
	MaxEvidenceSize   = 4 << 20
	MaxCapabilities   = 16
	MaxInstanceHint   = 128
	MaxSelectorValues = 32
	MaxSelectorSize   = 512

	ChallengeClockSkewSeconds = int64(5)
)

// Domain separators keep REPORTDATA and handshake transcript digests distinct
// from other uses of the same hash algorithms and serialized values.
var bindingDomain = []byte("argus-evidence-v1\x00")
var transcriptDomain = []byte("argus-spire-nodeattestor-v1\x00")

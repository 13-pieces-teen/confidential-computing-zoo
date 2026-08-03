package protocol

const (
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
)

var bindingDomain = []byte("argus-evidence-v1\x00")
var transcriptDomain = []byte("argus-spire-nodeattestor-v1\x00")

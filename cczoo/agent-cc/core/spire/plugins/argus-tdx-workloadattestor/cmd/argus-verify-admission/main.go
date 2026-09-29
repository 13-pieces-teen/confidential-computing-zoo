// argus-verify-admission reuses production EAR checks at an archived capture time.
// It does not perform offline DCAP verification or issue an identity.
package main

import (
	"crypto/ecdsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/trustee"
)

func run() error {
	evidencePath := flag.String("evidence", "", "original evidence.json")
	earPath := flag.String("ear", "", "original signed EAR")
	keyPath := flag.String("key", "", "approved EAR public key")
	issuer := flag.String("issuer", "", "expected issuer")
	profile := flag.String("profile", "", "expected profile")
	policy := flag.String("policy", "", "expected policy ID")
	at := flag.Int64("captured-at-ms", 0, "recorded original verification time")
	flag.Parse()
	if *at <= 0 {
		return fmt.Errorf("capture time is required")
	}
	raw, err := os.ReadFile(*evidencePath)
	if err != nil {
		return err
	}
	var evidence protocol.Evidence
	if err = json.Unmarshal(raw, &evidence); err != nil {
		return err
	}
	ear, err := os.ReadFile(*earPath)
	if err != nil {
		return err
	}
	raw, err = os.ReadFile(*keyPath)
	if err != nil {
		return err
	}
	block, rest := pem.Decode(raw)
	if block == nil || strings.TrimSpace(string(rest)) != "" || block.Type != "PUBLIC KEY" {
		return fmt.Errorf("expected one public key")
	}
	value, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return err
	}
	key, ok := value.(*ecdsa.PublicKey)
	if !ok {
		return fmt.Errorf("expected ECDSA key")
	}
	if err = trustee.VerifyArchivedEAR(ear, key, *issuer, *profile, *policy, evidence, time.UnixMilli(*at)); err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"schema": "argus.ear-reverification.v1", "result": "PASS",
		"scope": "production EAR signature, capture-time validity, policy and runtime binding", "offline_dcap": "NOT_RUN", "fresh_admission": "NOT_RUN"})
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

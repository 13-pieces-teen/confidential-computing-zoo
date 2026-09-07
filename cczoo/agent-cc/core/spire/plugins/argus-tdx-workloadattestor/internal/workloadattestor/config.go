package workloadattestor

import (
	"fmt"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
	"github.com/hashicorp/hcl"
	"net/url"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

type Config struct {
	EvidenceEndpoint, TrusteeEndpoint                                          *url.URL
	TargetRegistrationPath, TrusteeCAPath, TrusteeServerName, EARPublicKeyPath string
	EARExpectedIssuer, EARExpectedProfile, WorkloadID, PolicyID                string
	ImageConfigDigest, ConfigDigest                                            string
	RequestTimeout                                                             time.Duration
	MaxResponseBytes                                                           int64
}
type hclConfig struct {
	EvidenceEndpoint       string   `hcl:"evidence_endpoint"`
	TrusteeEndpoint        string   `hcl:"trustee_endpoint"`
	TargetRegistrationPath string   `hcl:"target_registration_path"`
	TrusteeCAPath          string   `hcl:"trustee_ca_path"`
	TrusteeServerName      string   `hcl:"trustee_server_name"`
	EARPublicKeyPath       string   `hcl:"ear_public_key_path"`
	EARExpectedIssuer      string   `hcl:"ear_expected_issuer"`
	EARExpectedProfile     string   `hcl:"ear_expected_profile"`
	WorkloadID             string   `hcl:"workload_id"`
	PolicyID               string   `hcl:"policy_id"`
	ImageConfigDigest      string   `hcl:"image_config_digest"`
	ConfigDigest           string   `hcl:"config_digest"`
	RequestTimeout         string   `hcl:"request_timeout"`
	MaxResponseBytes       int64    `hcl:"max_response_bytes"`
	Unused                 []string `hcl:",unusedKeys"`
}

func parseConfig(input string) (*Config, []string) {
	raw := hclConfig{RequestTimeout: "20s", MaxResponseBytes: 2 << 20}
	if err := hcl.Decode(&raw, input); err != nil {
		return nil, []string{err.Error()}
	}
	var notes []string
	if len(raw.Unused) > 0 {
		notes = append(notes, "unknown configuration: "+strings.Join(raw.Unused, ", "))
	}
	ep, err := url.Parse(raw.EvidenceEndpoint)
	if err != nil || ep == nil {
		notes = append(notes, "invalid evidence_endpoint")
	} else if err = validateEvidenceEndpoint(ep); err != nil {
		notes = append(notes, err.Error())
	}
	tp, err := url.Parse(raw.TrusteeEndpoint)
	if err != nil || tp.Scheme != "https" || tp.Host == "" || tp.User != nil || (tp.Path != "" && tp.Path != "/") || tp.RawQuery != "" || tp.Fragment != "" {
		notes = append(notes, "trustee_endpoint must be an HTTPS origin")
	}
	for key, value := range map[string]string{"target_registration_path": raw.TargetRegistrationPath, "trustee_ca_path": raw.TrusteeCAPath, "ear_public_key_path": raw.EARPublicKeyPath} {
		if !absoluteForHostOrLinux(value) {
			notes = append(notes, key+" must be absolute")
		}
	}
	if raw.TrusteeServerName == "" || strings.ContainsAny(raw.TrusteeServerName, "/:@ ") {
		notes = append(notes, "trustee_server_name must be a DNS name")
	}
	if raw.EARExpectedIssuer == "" || raw.EARExpectedProfile == "" {
		notes = append(notes, "fixed EAR issuer and profile are required")
	}
	for _, v := range []string{raw.WorkloadID, raw.PolicyID} {
		if !validSelectorComponent(v) {
			notes = append(notes, "workload_id and policy_id must be nonempty selector components")
		}
	}
	for _, v := range []string{raw.ImageConfigDigest, raw.ConfigDigest} {
		if !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(v) {
			notes = append(notes, "approved image_config_digest and config_digest must be sha256 digests")
		}
	}
	timeout, err := time.ParseDuration(raw.RequestTimeout)
	if err != nil || timeout <= 0 || timeout > 60*time.Second {
		notes = append(notes, "request_timeout must be in (0,60s]")
	}
	if raw.MaxResponseBytes <= 0 || raw.MaxResponseBytes > 4<<20 {
		notes = append(notes, "max_response_bytes must be in (0,4194304]")
	}
	if len(notes) > 0 {
		return nil, notes
	}
	return &Config{ep, tp, raw.TargetRegistrationPath, raw.TrusteeCAPath, raw.TrusteeServerName, raw.EARPublicKeyPath, raw.EARExpectedIssuer, raw.EARExpectedProfile, raw.WorkloadID, raw.PolicyID, raw.ImageConfigDigest, raw.ConfigDigest, timeout, raw.MaxResponseBytes}, nil
}
func validateEvidenceEndpoint(ep *url.URL) error {
	if ep.Scheme != "unix" || ep.Host != "" || ep.User != nil || !path.IsAbs(ep.Path) || ep.RawQuery != "" || ep.Fragment != "" {
		return fmt.Errorf("evidence_endpoint must be unix:///absolute/socket")
	}
	return nil
}
func absoluteForHostOrLinux(v string) bool { return filepath.IsAbs(v) || path.IsAbs(v) }
func validSelectorComponent(v string) bool {
	return regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`).MatchString(v)
}
func (c *Config) checkApproved(t protocol.Target) error {
	if err := t.Validate(); err != nil {
		return err
	}
	if t.WorkloadID != c.WorkloadID || t.PolicyID != c.PolicyID || t.ImageConfigDigest != c.ImageConfigDigest || t.ConfigDigest != c.ConfigDigest {
		return fmt.Errorf("target differs from approved workload baseline")
	}
	return nil
}

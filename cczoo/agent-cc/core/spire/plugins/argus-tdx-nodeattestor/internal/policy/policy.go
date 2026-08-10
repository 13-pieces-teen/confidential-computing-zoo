package policy

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"

	"github.com/gowebpki/jcs"
	"gopkg.in/yaml.v3"
)

var (
	identifierPattern  = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,127}$`)
	measurementPattern = regexp.MustCompile(`^[0-9a-f]+$`)
)

type Model struct {
	Version  int     `json:"version" yaml:"version"`
	PolicyID string  `json:"policy_id" yaml:"policy_id"`
	TEE      TEE     `json:"tee" yaml:"tee"`
	Binding  Binding `json:"binding" yaml:"binding"`
}

type TEE struct {
	Type             string              `json:"type" yaml:"type"`
	AllowDebug       bool                `json:"allow_debug" yaml:"allow_debug"`
	AllowedTCBStatus []string            `json:"allowed_tcb_status" yaml:"allowed_tcb_status"`
	AllowedMRTD      []string            `json:"allowed_mrtd" yaml:"allowed_mrtd"`
	AllowedRTMR      map[string][]string `json:"allowed_rtmr" yaml:"allowed_rtmr"`
}

type Binding struct {
	RequireReportData           bool `json:"require_report_data" yaml:"require_report_data"`
	RequireAttestationKeyDigest bool `json:"require_attestation_key_digest" yaml:"require_attestation_key_digest"`
	RequireInstanceID           bool `json:"require_instance_id" yaml:"require_instance_id"`
}

type Policy struct {
	Model         Model
	CanonicalJSON []byte
	Digest        string
}

func Load(path string) (*Policy, error) {
	contents, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read policy: %w", err)
	}
	return Parse(contents)
}

func Parse(contents []byte) (*Policy, error) {
	decoder := yaml.NewDecoder(bytes.NewReader(contents))
	decoder.KnownFields(true)
	var model Model
	if err := decoder.Decode(&model); err != nil {
		return nil, fmt.Errorf("decode policy: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err == nil {
		return nil, fmt.Errorf("decode policy: multiple YAML documents are not allowed")
	}
	if err := normalizeAndValidate(&model); err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(model)
	if err != nil {
		return nil, fmt.Errorf("marshal policy model: %w", err)
	}
	canonical, err := jcs.Transform(encoded)
	if err != nil {
		return nil, fmt.Errorf("canonicalize policy model: %w", err)
	}
	digest := sha256.Sum256(canonical)
	return &Policy{
		Model:         model,
		CanonicalJSON: canonical,
		Digest:        "sha256:" + hex.EncodeToString(digest[:]),
	}, nil
}

func (policy *Policy) AllowsTCBStatus(value string) bool {
	return contains(policy.Model.TEE.AllowedTCBStatus, value)
}

func (policy *Policy) AllowsMRTD(value string) bool {
	return contains(policy.Model.TEE.AllowedMRTD, value)
}

func (policy *Policy) AllowsRTMR(index, value string) bool {
	return contains(policy.Model.TEE.AllowedRTMR[index], value)
}

func normalizeAndValidate(model *Model) error {
	if model.Version != 1 {
		return fmt.Errorf("policy version must be 1")
	}
	if !identifierPattern.MatchString(model.PolicyID) {
		return fmt.Errorf("policy_id is invalid")
	}
	if model.TEE.Type != "tdx" {
		return fmt.Errorf("tee.type must be tdx")
	}
	var err error
	model.TEE.AllowedTCBStatus, err = normalizeIdentifiers("allowed_tcb_status", model.TEE.AllowedTCBStatus)
	if err != nil {
		return err
	}
	model.TEE.AllowedMRTD, err = normalizeMeasurements("allowed_mrtd", model.TEE.AllowedMRTD)
	if err != nil {
		return err
	}
	if len(model.TEE.AllowedRTMR) == 0 {
		return fmt.Errorf("allowed_rtmr must not be empty")
	}
	for index, measurements := range model.TEE.AllowedRTMR {
		if index != "0" && index != "1" && index != "2" && index != "3" {
			return fmt.Errorf("allowed_rtmr index %q is invalid", index)
		}
		normalized, err := normalizeMeasurements("allowed_rtmr."+index, measurements)
		if err != nil {
			return err
		}
		model.TEE.AllowedRTMR[index] = normalized
	}
	if !model.Binding.RequireReportData || !model.Binding.RequireAttestationKeyDigest || !model.Binding.RequireInstanceID {
		return fmt.Errorf("all baseline binding requirements must be true")
	}
	return nil
}

func normalizeIdentifiers(name string, values []string) ([]string, error) {
	if len(values) == 0 {
		return nil, fmt.Errorf("%s must not be empty", name)
	}
	for _, value := range values {
		if !identifierPattern.MatchString(value) {
			return nil, fmt.Errorf("%s contains invalid value %q", name, value)
		}
	}
	return uniqueSorted(values), nil
}

func normalizeMeasurements(name string, values []string) ([]string, error) {
	if len(values) == 0 {
		return nil, fmt.Errorf("%s must not be empty", name)
	}
	for _, value := range values {
		if len(value)%2 != 0 || !measurementPattern.MatchString(value) {
			return nil, fmt.Errorf("%s contains non-normalized measurement", name)
		}
	}
	return uniqueSorted(values), nil
}

func uniqueSorted(values []string) []string {
	result := append([]string(nil), values...)
	sort.Strings(result)
	output := result[:0]
	for _, value := range result {
		if len(output) == 0 || output[len(output)-1] != value {
			output = append(output, value)
		}
	}
	return output
}

func contains(values []string, expected string) bool {
	index := sort.SearchStrings(values, expected)
	return index < len(values) && values[index] == expected
}

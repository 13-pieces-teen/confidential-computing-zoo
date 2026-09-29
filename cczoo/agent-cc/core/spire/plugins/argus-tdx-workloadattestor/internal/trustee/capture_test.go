package trustee

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestCaptureDisabledByDefault(t *testing.T) {
	t.Setenv("ARGUS_ADMISSION_EVIDENCE_DIR", "")
	if newCaptureWriterFromEnv() != nil {
		t.Fatal("capture enabled without configuration")
	}
}

func TestCaptureFailureDoesNotChangeEARAcceptance(t *testing.T) {
	key := newSigningKey(t)
	now := time.Now()
	ev := fixture(t)
	canonical, _ := ev.RuntimeData.Canonical()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(signEAR(t, key, validClaims(now, canonical))))
	}))
	defer server.Close()
	client := testClient(server, key, now)
	invalidDirectory := filepath.Join(t.TempDir(), "ordinary-file")
	if err := os.WriteFile(invalidDirectory, []byte("not a directory"), 0600); err != nil {
		t.Fatal(err)
	}
	writer := &captureWriter{directory: invalidDirectory, jobs: make(chan captureJob, 1)}
	client.capture = writer
	if err := client.Verify(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
	if err := writer.write(<-writer.jobs); err == nil {
		t.Fatal("expected independent capture write failure")
	}
	// A full observation queue likewise cannot reject or block the next request.
	writer.jobs <- captureJob{}
	if err := client.Verify(context.Background(), ev); err != nil {
		t.Fatal(err)
	}
}

func TestCapturePreservesExactRequestEARAndLocalOutcome(t *testing.T) {
	writer := &captureWriter{directory: t.TempDir(), jobs: make(chan captureJob, 2)}
	ev := fixture(t)
	request, err := buildRequest(ev, testPolicyID)
	if err != nil {
		t.Fatal(err)
	}
	writer.appraisal(ev, request, []byte("original.signed.ear"), 200, time.Unix(123, 0), true)
	if err := writer.write(<-writer.jobs); err != nil {
		t.Fatal(err)
	}
	client := &Client{capture: writer}
	client.RecordLocalOutcome(ev, true)
	if err := writer.write(<-writer.jobs); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(writer.directory, ev.RuntimeData.Nonce)
	original, err := os.ReadFile(filepath.Join(dir, "request.json"))
	if err != nil || string(original) != string(request) {
		t.Fatal("request capture differs")
	}
	var metadata map[string]any
	raw, _ := os.ReadFile(filepath.Join(dir, "capture.json"))
	if err := json.Unmarshal(raw, &metadata); err != nil {
		t.Fatal(err)
	}
	if metadata["outcome"] != "EAR_ACCEPTED" || metadata["captured_at_ms"] != float64(123000) {
		t.Fatal(metadata)
	}
	if _, err := os.Stat(filepath.Join(dir, "local-check.json")); err != nil {
		t.Fatal(err)
	}
}

func TestArchivedEARUsesRecordedWindowNotNewAdmission(t *testing.T) {
	key := newSigningKey(t)
	ev := fixture(t)
	canonical, _ := ev.RuntimeData.Canonical()
	at := time.Unix(1700000000, 0)
	token := []byte(signEAR(t, key, validClaims(at, canonical)))
	if err := VerifyArchivedEAR(token, &key.PublicKey, testIssuer, testProfile, testPolicyID, ev, at); err != nil {
		t.Fatal(err)
	}
	if err := VerifyArchivedEAR(token, &key.PublicKey, testIssuer, testProfile, testPolicyID, ev, at.Add(time.Hour)); err == nil {
		t.Fatal("expired EAR accepted as current")
	}
}

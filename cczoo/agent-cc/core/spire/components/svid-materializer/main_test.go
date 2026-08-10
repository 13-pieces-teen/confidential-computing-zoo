package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/svid/x509svid"
)

func TestSelectSVIDRequiresExactIdentity(t *testing.T) {
	expected := spiffeid.RequireFromString("spiffe://argus.local/agent/openclaw")
	other := spiffeid.RequireFromString("spiffe://argus.local/service/openviking-cmem")
	svid, err := selectSVID([]*x509svid.SVID{{ID: other}, {ID: expected}}, expected)
	if err != nil {
		t.Fatalf("select exact SVID: %v", err)
	}
	if svid.ID != expected {
		t.Fatalf("selected %s, expected %s", svid.ID, expected)
	}
}

func TestSelectSVIDRejectsMissingIdentity(t *testing.T) {
	expected := spiffeid.RequireFromString("spiffe://argus.local/agent/openclaw")
	other := spiffeid.RequireFromString("spiffe://argus.local/service/openviking-cmem")
	if _, err := selectSVID([]*x509svid.SVID{{ID: other}}, expected); err == nil {
		t.Fatal("expected missing identity to be rejected")
	}
}

func TestAtomicWritePublishesModeAndContents(t *testing.T) {
	directory := t.TempDir()
	if err := atomicWrite(directory, "svid-key.pem", []byte("private"), 0o600); err != nil {
		t.Fatalf("atomic write: %v", err)
	}
	path := filepath.Join(directory, "svid-key.pem")
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read output: %v", err)
	}
	if string(contents) != "private" {
		t.Fatalf("unexpected contents %q", contents)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat output: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("mode is %o, expected 600", info.Mode().Perm())
	}
}

package agent

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestLoadOrCreateAttestationKeyPersistsRawKey(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "attestation-key")

	first, err := loadOrCreateAttestationKey(path)
	if err != nil {
		t.Fatal(err)
	}
	second, err := loadOrCreateAttestationKey(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first, second) {
		t.Fatal("reloading the key changed its value")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("key mode = %04o, want 0600", got)
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(contents, first) {
		t.Fatal("persisted raw key differs from returned key")
	}
}

func TestLoadAttestationKeyRejectsBroadPermissions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "attestation-key")
	if err := os.WriteFile(path, make([]byte, 64), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := loadAttestationKey(path); err == nil {
		t.Fatal("key with broad permissions was accepted")
	}
}

func TestLoadOrCreateAttestationKeyRejectsBroadDirectoryPermissions(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := loadOrCreateAttestationKey(filepath.Join(directory, "attestation-key")); err == nil {
		t.Fatal("key directory with broad permissions was accepted")
	}
}

package agent

import (
	"crypto/ed25519"
	"crypto/rand"
	"fmt"
	"os"
	"path/filepath"
)

// loadOrCreateAttestationKey preserves the proof key across Agent restarts. Once
// the Trustee verifies the evidence, the Server associates this key with the
// accepted instance and launch claims.
func loadOrCreateAttestationKey(path string) (ed25519.PrivateKey, error) {
	key, err := loadAttestationKey(path)
	if err == nil {
		return key, nil
	}
	if !os.IsNotExist(err) {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, fmt.Errorf("create attestation key directory: %w", err)
	}
	if err := ensurePrivateDirectory(filepath.Dir(path)); err != nil {
		return nil, err
	}
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate attestation key: %w", err)
	}
	if err := installKeyAtomically(path, privateKey); err != nil {
		if os.IsExist(err) {
			return loadAttestationKey(path)
		}
		return nil, err
	}
	return privateKey, nil
}

// loadAttestationKey rejects links, devices, and broadly readable key files.
func loadAttestationKey(path string) (ed25519.PrivateKey, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("attestation key is not a regular file")
	}
	if info.Mode().Perm() != 0o600 {
		return nil, fmt.Errorf("attestation key permissions must be 0600")
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read attestation key: %w", err)
	}
	if len(contents) != ed25519.PrivateKeySize {
		return nil, fmt.Errorf("attestation key must be %d raw bytes", ed25519.PrivateKeySize)
	}
	return ed25519.PrivateKey(contents), nil
}

// installKeyAtomically uses a hard-link publication step so concurrent Agent
// starts cannot replace a key that another process has already installed.
func installKeyAtomically(path string, privateKey ed25519.PrivateKey) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), ".attestation-key-*")
	if err != nil {
		return fmt.Errorf("create temporary attestation key: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)

	if _, err := temporary.Write(privateKey); err != nil {
		temporary.Close()
		return fmt.Errorf("write temporary attestation key: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return fmt.Errorf("sync temporary attestation key: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close temporary attestation key: %w", err)
	}
	if err := os.Link(temporaryPath, path); err != nil {
		return fmt.Errorf("install attestation key: %w", err)
	}
	// Persist the directory entry as well as the key contents.
	if err := syncDirectory(filepath.Dir(path)); err != nil {
		return err
	}
	return nil
}

func ensurePrivateDirectory(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return fmt.Errorf("stat attestation key directory: %w", err)
	}
	if !info.IsDir() || info.Mode().Perm()&0o077 != 0 {
		return fmt.Errorf("attestation key directory permissions must not grant group or other access")
	}
	return nil
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("open attestation key directory: %w", err)
	}
	defer directory.Close()
	if err := directory.Sync(); err != nil {
		return fmt.Errorf("sync attestation key directory: %w", err)
	}
	return nil
}

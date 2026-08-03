package server

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
)

const (
	bindingRecordVersion = 1
	maxBindingRecordSize = 4096
)

type instanceBinding struct {
	InstanceID string `json:"instance_id"`
	LaunchID   string `json:"launch_id,omitempty"`
}

type bindingRecord struct {
	Version    int    `json:"version"`
	InstanceID string `json:"instance_id"`
	LaunchID   string `json:"launch_id,omitempty"`
}

type bindingStore struct {
	directory string
}

func newBindingStore(directory string) (*bindingStore, error) {
	if !filepath.IsAbs(directory) {
		return nil, fmt.Errorf("binding state directory must be absolute")
	}
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return nil, fmt.Errorf("create binding state directory: %w", err)
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return nil, fmt.Errorf("inspect binding state directory: %w", err)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("binding state path must be a directory")
	}
	if info.Mode().Perm()&0o022 != 0 {
		return nil, fmt.Errorf("binding state directory must not be group or world writable")
	}
	return &bindingStore{directory: filepath.Clean(directory)}, nil
}

func (store *bindingStore) Bind(keyID string, binding instanceBinding) error {
	if err := validateBinding(keyID, binding); err != nil {
		return err
	}
	target := filepath.Join(store.directory, keyID+".json")
	existing, found, err := readBindingRecord(target)
	if err != nil {
		return err
	}
	if found {
		return compareBinding(existing, binding)
	}

	encoded, err := json.Marshal(bindingRecord{
		Version:    bindingRecordVersion,
		InstanceID: binding.InstanceID,
		LaunchID:   binding.LaunchID,
	})
	if err != nil {
		return fmt.Errorf("encode binding record: %w", err)
	}
	encoded = append(encoded, '\n')
	temporary, err := os.CreateTemp(store.directory, ".binding-*")
	if err != nil {
		return fmt.Errorf("create binding record: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return fmt.Errorf("secure binding record: %w", err)
	}
	if _, err := temporary.Write(encoded); err != nil {
		temporary.Close()
		return fmt.Errorf("write binding record: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return fmt.Errorf("sync binding record: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close binding record: %w", err)
	}

	if err := os.Link(temporaryPath, target); err != nil {
		if !errors.Is(err, fs.ErrExist) {
			return fmt.Errorf("publish binding record: %w", err)
		}
		existing, found, readErr := readBindingRecord(target)
		if readErr != nil {
			return readErr
		}
		if !found {
			return fmt.Errorf("binding record disappeared during concurrent publication")
		}
		return compareBinding(existing, binding)
	}
	if err := syncDirectory(store.directory); err != nil {
		return fmt.Errorf("sync binding state directory: %w", err)
	}
	return nil
}

func readBindingRecord(path string) (instanceBinding, bool, error) {
	file, err := os.Open(path)
	if errors.Is(err, fs.ErrNotExist) {
		return instanceBinding{}, false, nil
	}
	if err != nil {
		return instanceBinding{}, false, fmt.Errorf("open binding record: %w", err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return instanceBinding{}, false, fmt.Errorf("inspect binding record: %w", err)
	}
	if !info.Mode().IsRegular() {
		return instanceBinding{}, false, fmt.Errorf("binding record must be a regular file")
	}
	if info.Mode().Perm()&0o077 != 0 {
		return instanceBinding{}, false, fmt.Errorf("binding record permissions must not grant group or world access")
	}
	if info.Size() > maxBindingRecordSize {
		return instanceBinding{}, false, fmt.Errorf("binding record exceeds size limit")
	}
	decoder := json.NewDecoder(io.LimitReader(file, maxBindingRecordSize+1))
	decoder.DisallowUnknownFields()
	var record bindingRecord
	if err := decoder.Decode(&record); err != nil {
		return instanceBinding{}, false, fmt.Errorf("decode binding record: %w", err)
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return instanceBinding{}, false, err
	}
	if record.Version != bindingRecordVersion {
		return instanceBinding{}, false, fmt.Errorf("unsupported binding record version %d", record.Version)
	}
	binding := instanceBinding{InstanceID: record.InstanceID, LaunchID: record.LaunchID}
	if err := validateStoredBinding(binding); err != nil {
		return instanceBinding{}, false, fmt.Errorf("invalid binding record: %w", err)
	}
	return binding, true, nil
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var trailing any
	err := decoder.Decode(&trailing)
	if errors.Is(err, io.EOF) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("decode binding record trailer: %w", err)
	}
	return fmt.Errorf("binding record contains trailing JSON")
}

func validateBinding(keyID string, binding instanceBinding) error {
	decoded, err := hex.DecodeString(keyID)
	if err != nil || len(decoded) != 32 || keyID != fmt.Sprintf("%x", decoded) {
		return fmt.Errorf("key ID must be 64 lowercase hexadecimal characters")
	}
	return validateStoredBinding(binding)
}

func validateStoredBinding(binding instanceBinding) error {
	if binding.InstanceID == "" {
		return fmt.Errorf("verified instance ID is required")
	}
	return nil
}

func compareBinding(existing, candidate instanceBinding) error {
	if existing != candidate {
		return fmt.Errorf("key ID is already bound to another verified instance or launch")
	}
	return nil
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	if err := directory.Sync(); err != nil && runtime.GOOS != "windows" {
		return err
	}
	return nil
}

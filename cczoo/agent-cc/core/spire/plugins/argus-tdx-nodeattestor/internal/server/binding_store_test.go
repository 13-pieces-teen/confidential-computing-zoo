package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBindingStorePersistsAcrossInstances(t *testing.T) {
	directory := t.TempDir()
	keyID := strings.Repeat("a", 64)
	binding := instanceBinding{InstanceID: "tdvm-0001", LaunchID: "launch-0001"}

	first, err := newBindingStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	if err := first.Bind(keyID, binding); err != nil {
		t.Fatal(err)
	}
	second, err := newBindingStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	if err := second.Bind(keyID, binding); err != nil {
		t.Fatalf("persisted binding was not idempotent: %v", err)
	}
	if err := second.Bind(keyID, instanceBinding{InstanceID: "tdvm-0002", LaunchID: "launch-0002"}); err == nil {
		t.Fatal("persisted binding accepted a clone conflict")
	}

	info, err := os.Stat(filepath.Join(directory, keyID+".json"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("binding record mode = %o, want 600", info.Mode().Perm())
	}
}

func TestBindingStoreFailsClosedOnCorruptRecord(t *testing.T) {
	directory := t.TempDir()
	keyID := strings.Repeat("b", 64)
	if err := os.WriteFile(filepath.Join(directory, keyID+".json"), []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := newBindingStore(directory)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Bind(keyID, instanceBinding{InstanceID: "tdvm-0001"}); err == nil {
		t.Fatal("corrupt binding record was overwritten")
	}
}

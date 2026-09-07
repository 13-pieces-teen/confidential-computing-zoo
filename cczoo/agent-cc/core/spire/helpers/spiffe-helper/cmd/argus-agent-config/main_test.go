package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPreserveNodeContractAndReplaceWorkload(t *testing.T) {
	src := []byte(`agent { trust_domain="argus.local" experimental { use_sync_authorized_entries=true } }
 plugins { NodeAttestor "argus_tdx" { plugin_cmd="/node" plugin_data { proof_key_path="/same-key" } }
 WorkloadAttestor "unix" {} }`)
	overlay := []byte(`agent { experimental { broker { socket_path="/broker.sock" } } }
 plugins { WorkloadAttestor "unix" { plugin_data { discover_workload_path=true } } WorkloadAttestor "argus_tdx" { plugin_cmd="/workload" } }`)
	b, err := merge(src, overlay)
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range []string{"/same-key", "/node", "use_sync_authorized_entries", "/workload", "discover_workload_path"} {
		if !strings.Contains(string(b), v) {
			t.Fatal("lost", v)
		}
	}
	if strings.Count(string(b), `WorkloadAttestor "unix"`) != 1 {
		t.Fatal("duplicate unix plugin")
	}
	again, err := merge(b, overlay)
	if err != nil || strings.Count(string(again), "/broker.sock") != 1 {
		t.Fatal("merge not idempotent", err)
	}
}

func TestFirstBrokerSetupPreservesObjectKeys(t *testing.T) {
	source := []byte(`agent { trust_domain="argus.local" socket_path="/old.sock" } plugins { NodeAttestor "argus_tdx" {} }`)
	overlay := []byte(`agent { socket_path="/workload.sock" experimental { broker { socket_path="/broker.sock" } } } plugins { WorkloadAttestor "unix" {} }`)
	got, err := merge(source, overlay)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(got), "/old.sock") || !strings.Contains(string(got), "experimental") || !strings.Contains(string(got), "/workload.sock") {
		t.Fatal(string(got))
	}
	again, err := merge(got, overlay)
	if err != nil || strings.Count(string(again), "experimental") != 1 {
		t.Fatal("first setup was not idempotent", err)
	}
}

func TestOverlayCannotChangeOtherExperimentalSettings(t *testing.T) {
	overlay := []byte(`agent { experimental { broker { socket_path="/broker.sock" } use_sync_authorized_entries=false } } plugins { WorkloadAttestor "unix" {} }`)
	for _, existing := range []string{"", "experimental { use_sync_authorized_entries=true }"} {
		source := []byte(`agent { trust_domain="argus.local" ` + existing + ` } plugins { NodeAttestor "argus_tdx" {} }`)
		if _, err := merge(source, overlay); err == nil {
			t.Fatalf("accepted unrelated experimental settings with existing block %q", existing)
		}
	}
}

func TestUpgradeNodePreservesTrustAndProofPaths(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "node-plugin")
	if err := os.WriteFile(binary, []byte("version-1.15.3"), 0600); err != nil {
		t.Fatal(err)
	}
	source := []byte(`plugins { NodeAttestor "argus_tdx" {
 plugin_cmd="/old/plugin" plugin_checksum="old-checksum"
 plugin_data { evidence_socket_path="/old/evidence.sock" proof_key_path="/existing/proof.pem" trustee_ca_bundle_path="/existing/ca.pem" ear_verification_key_path="/existing/ear.pem" }
} }`)
	for _, role := range []string{"agent", "server"} {
		got, err := upgradeNode(source, role, binary)
		if err != nil {
			t.Fatal(err)
		}
		for _, keep := range []string{"/existing/proof.pem", "/existing/ca.pem", "/existing/ear.pem", fmt.Sprintf("%x", sha256.Sum256([]byte("version-1.15.3")))} {
			if !strings.Contains(string(got), keep) {
				t.Fatalf("%s lost %s", role, keep)
			}
		}
		if strings.Contains(string(got), "old-checksum") {
			t.Fatal("stale binary checksum")
		}
		if role == "server" && !strings.Contains(string(got), "/old/evidence.sock") {
			t.Fatal("Server plugin_data changed")
		}
		if role == "agent" && !strings.Contains(string(got), "/run/argus/evidence-provider.sock") {
			t.Fatal("Agent not pointed at upgraded Provider")
		}
	}
}

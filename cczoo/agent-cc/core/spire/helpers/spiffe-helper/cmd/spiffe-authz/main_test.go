package main

import "testing"

func TestRepeatedExactClientFlags(t *testing.T) {
	var ids identities
	for _, id := range []string{"spiffe://example.org/client/a", "spiffe://example.org/client/b"} {
		if err := ids.Set(id); err != nil {
			t.Fatal(err)
		}
	}
	for _, id := range []string{"spiffe://example.org/client/a", "spiffe://example.org", "invalid", "spiffe://example.org/client/*"} {
		if err := ids.Set(id); err == nil {
			t.Fatalf("accepted invalid or duplicate %s", id)
		}
	}
}

package main

import (
	"net"
	"testing"
	"time"
)

func TestLifetimeConnClosesBlockedReadAtExpiry(t *testing.T) {
	client, server := net.Pipe()
	defer server.Close()

	conn, err := newLifetimeConn(client, 50*time.Millisecond)
	if err != nil {
		t.Fatalf("wrap connection: %v", err)
	}
	defer conn.Close()

	started := time.Now()
	_, err = conn.Read(make([]byte, 1))
	if err == nil {
		t.Fatal("blocked read survived the maximum connection lifetime")
	}
	if elapsed := time.Since(started); elapsed < 20*time.Millisecond || elapsed > time.Second {
		t.Fatalf("blocked read closed after %s; expected the configured expiry", elapsed)
	}
}

func TestLifetimeConnCannotClearAbsoluteDeadline(t *testing.T) {
	client, server := net.Pipe()
	defer server.Close()

	conn, err := newLifetimeConn(client, 50*time.Millisecond)
	if err != nil {
		t.Fatalf("wrap connection: %v", err)
	}
	defer conn.Close()
	if err := conn.SetReadDeadline(time.Time{}); err != nil {
		t.Fatalf("clear read deadline: %v", err)
	}

	started := time.Now()
	_, err = conn.Read(make([]byte, 1))
	if err == nil {
		t.Fatal("clearing the read deadline bypassed the maximum connection lifetime")
	}
	if elapsed := time.Since(started); elapsed < 20*time.Millisecond || elapsed > time.Second {
		t.Fatalf("blocked read closed after %s; expected the absolute deadline", elapsed)
	}
}

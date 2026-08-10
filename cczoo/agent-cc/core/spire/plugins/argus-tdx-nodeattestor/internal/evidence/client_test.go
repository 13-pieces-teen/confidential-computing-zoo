package evidence

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestGetEvidencePostsOriginalRequest(t *testing.T) {
	requestBody := `{"version":"v1"}`
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			t.Errorf("method = %s", request.Method)
		}
		if request.Header.Get("Content-Type") != "application/json" {
			t.Errorf("Content-Type = %q", request.Header.Get("Content-Type"))
		}
		contents, err := io.ReadAll(request.Body)
		if err != nil {
			t.Error(err)
		}
		if string(contents) != requestBody {
			t.Errorf("request body = %s", contents)
		}
		_, _ = writer.Write([]byte(`{"evidence":"fixture"}`))
	}))
	defer server.Close()
	endpoint, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	client, err := NewClient(endpoint, time.Second, 1024)
	if err != nil {
		t.Fatal(err)
	}
	result, err := client.GetEvidence(context.Background(), []byte(requestBody))
	if err != nil {
		t.Fatal(err)
	}
	if string(result) != `{"evidence":"fixture"}` {
		t.Fatalf("response = %s", result)
	}
}

func TestGetEvidenceFailsClosedOnStatusAndSize(t *testing.T) {
	for name, handler := range map[string]http.HandlerFunc{
		"status": func(writer http.ResponseWriter, _ *http.Request) {
			writer.WriteHeader(http.StatusServiceUnavailable)
		},
		"size": func(writer http.ResponseWriter, _ *http.Request) {
			_, _ = writer.Write([]byte(strings.Repeat("x", 17)))
		},
	} {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewServer(handler)
			defer server.Close()
			endpoint, err := url.Parse(server.URL)
			if err != nil {
				t.Fatal(err)
			}
			client, err := NewClient(endpoint, time.Second, 16)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := client.GetEvidence(context.Background(), []byte(`{}`)); err == nil {
				t.Fatal("invalid Evidence Provider response was accepted")
			}
		})
	}
}

func TestNewClientRejectsInvalidLimits(t *testing.T) {
	if _, err := NewClient(nil, time.Second, 1); err == nil {
		t.Fatal("nil endpoint was accepted")
	}
	endpoint := &url.URL{Scheme: "unix", Path: "/run/argus/evidence.sock"}
	if _, err := NewClient(endpoint, 0, 1); err == nil {
		t.Fatal("zero timeout was accepted")
	}
	if _, err := NewClient(endpoint, time.Second, 0); err == nil {
		t.Fatal("zero size limit was accepted")
	}
}

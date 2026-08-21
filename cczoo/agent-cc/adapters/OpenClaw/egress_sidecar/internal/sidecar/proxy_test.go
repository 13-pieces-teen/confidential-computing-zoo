package sidecar

import (
	"crypto/tls"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
)

func TestAllowedRequestIsForwarded(t *testing.T) {
	var upstreamRequests atomic.Int32
	var authorized atomic.Bool
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		upstreamRequests.Add(1)
		if request.Method != http.MethodPost || request.URL.Path != "/api/v1/sessions" {
			t.Fatalf("unexpected upstream request: %s %s", request.Method, request.URL.Path)
		}
		body, err := io.ReadAll(request.Body)
		if err != nil {
			t.Fatal(err)
		}
		if string(body) != `{"message":"remember this"}` {
			t.Fatalf("upstream body = %q", body)
		}
		writer.WriteHeader(http.StatusCreated)
	}))
	defer upstream.Close()

	guard := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		var authorization AuthorizationRequest
		if err := json.NewDecoder(request.Body).Decode(&authorization); err != nil {
			t.Fatal(err)
		}
		if authorization.Operation != "memory.write" {
			t.Fatalf("operation = %q", authorization.Operation)
		}
		if authorization.CallerSPIFFEID != "spiffe://argus.local/agent/openclaw" ||
			authorization.TargetSPIFFEID != "spiffe://argus.local/service/openviking-cmem" ||
			authorization.TargetOrigin != "https://openviking.argus.local:1943" {
			t.Fatalf("unexpected Guard authorization metadata: %+v", authorization)
		}
		authorized.Store(true)
		_ = json.NewEncoder(writer).Encode(AuthorizationResponse{
			RequestID:     authorization.RequestID,
			Decision:      "ALLOW",
			Reason:        "matched",
			DecisionID:    "decision-1",
			ExpiresAtUnix: time.Now().Add(15 * time.Second).Unix(),
			PolicyID:      "policy-1",
			RuleID:        "rule-1",
		})
	}))
	defer guard.Close()

	target, err := url.Parse(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	handler := NewProxyHandler(ProxyConfig{
		Target:         target,
		Transport:      http.DefaultTransport,
		GuardURL:       guard.URL,
		GuardToken:     "test-token",
		CallerSPIFFEID: "spiffe://argus.local/agent/openclaw",
		TargetSPIFFEID: "spiffe://argus.local/service/openviking-cmem",
		TargetService:  "openviking-cmem",
		TargetOrigin:   "https://openviking.argus.local:1943",
		DataClass:      "sensitive",
	})

	request := httptest.NewRequest(http.MethodPost, "http://egress.local/api/v1/sessions", &bodyAfterAuthorization{
		reader:     strings.NewReader(`{"message":"remember this"}`),
		authorized: &authorized,
	})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	if response.Code != http.StatusCreated {
		t.Fatalf("status = %d, body = %q", response.Code, response.Body.String())
	}
	if upstreamRequests.Load() != 1 {
		t.Fatalf("upstream requests = %d", upstreamRequests.Load())
	}
}

func TestHTTPMethodsMapToGuardOperations(t *testing.T) {
	tests := map[string]string{
		http.MethodGet:    "memory.read",
		http.MethodHead:   "memory.read",
		http.MethodDelete: "memory.delete",
		http.MethodPost:   "memory.write",
		http.MethodPatch:  "memory.write",
	}
	for method, expected := range tests {
		if actual := operationFor(method); actual != expected {
			t.Errorf("operationFor(%q) = %q, want %q", method, actual, expected)
		}
	}
}

func TestDeniedRequestDoesNotReachUpstream(t *testing.T) {
	var upstreamRequests atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		upstreamRequests.Add(1)
	}))
	defer upstream.Close()
	guard := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		var authorization AuthorizationRequest
		if err := json.NewDecoder(request.Body).Decode(&authorization); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(writer).Encode(AuthorizationResponse{
			RequestID:     authorization.RequestID,
			Decision:      "DENY",
			Reason:        "policy denied",
			DecisionID:    "decision-deny",
			ExpiresAtUnix: time.Now().Add(15 * time.Second).Unix(),
			PolicyID:      "policy-1",
		})
	}))
	defer guard.Close()
	target, err := url.Parse(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	handler := NewProxyHandler(ProxyConfig{
		Target: target, Transport: http.DefaultTransport, GuardURL: guard.URL,
		GuardToken: "test-token", CallerSPIFFEID: "spiffe://argus.local/agent/openclaw",
		TargetSPIFFEID: "spiffe://argus.local/service/openviking-cmem",
		TargetService:  "openviking-cmem", TargetOrigin: "https://openviking.argus.local:1943",
		DataClass: "sensitive",
	})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodDelete, "http://egress.local/api/v1/sessions/1", nil))

	if response.Code != http.StatusForbidden {
		t.Fatalf("status = %d, body = %q", response.Code, response.Body.String())
	}
	if upstreamRequests.Load() != 0 {
		t.Fatalf("upstream requests = %d", upstreamRequests.Load())
	}
}

func TestInvalidGuardDecisionDoesNotReachUpstream(t *testing.T) {
	tests := map[string]func(AuthorizationRequest) AuthorizationResponse{
		"request ID mismatch": func(request AuthorizationRequest) AuthorizationResponse {
			return validAuthorizationResponse(request.RequestID + "-other")
		},
		"expired": func(request AuthorizationRequest) AuthorizationResponse {
			response := validAuthorizationResponse(request.RequestID)
			response.ExpiresAtUnix = time.Now().Add(-time.Second).Unix()
			return response
		},
		"missing receipt": func(request AuthorizationRequest) AuthorizationResponse {
			response := validAuthorizationResponse(request.RequestID)
			response.DecisionID = ""
			return response
		},
		"missing policy": func(request AuthorizationRequest) AuthorizationResponse {
			response := validAuthorizationResponse(request.RequestID)
			response.PolicyID = ""
			return response
		},
		"missing rule": func(request AuthorizationRequest) AuthorizationResponse {
			response := validAuthorizationResponse(request.RequestID)
			response.RuleID = ""
			return response
		},
	}
	for name, guardDecision := range tests {
		t.Run(name, func(t *testing.T) {
			var upstreamRequests atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				upstreamRequests.Add(1)
			}))
			defer upstream.Close()
			guard := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				var authorization AuthorizationRequest
				if err := json.NewDecoder(request.Body).Decode(&authorization); err != nil {
					t.Fatal(err)
				}
				_ = json.NewEncoder(writer).Encode(guardDecision(authorization))
			}))
			defer guard.Close()
			target, err := url.Parse(upstream.URL)
			if err != nil {
				t.Fatal(err)
			}
			handler := NewProxyHandler(ProxyConfig{
				Target: target, Transport: http.DefaultTransport, GuardURL: guard.URL,
				GuardToken: "test-token", CallerSPIFFEID: "spiffe://argus.local/agent/openclaw",
				TargetSPIFFEID: "spiffe://argus.local/service/openviking-cmem",
				TargetService:  "openviking-cmem", TargetOrigin: "https://openviking.argus.local:1943",
				DataClass: "sensitive",
			})
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://egress.local/health", nil))

			if response.Code != http.StatusServiceUnavailable {
				t.Fatalf("status = %d, body = %q", response.Code, response.Body.String())
			}
			if upstreamRequests.Load() != 0 {
				t.Fatalf("upstream requests = %d", upstreamRequests.Load())
			}
		})
	}
}

func TestGuardFailureDoesNotReachUpstream(t *testing.T) {
	tests := map[string]func(t *testing.T) string{
		"invalid decision": func(t *testing.T) string {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				_, _ = writer.Write([]byte(`{}`))
			}))
			t.Cleanup(server.Close)
			return server.URL
		},
		"invalid JSON": func(t *testing.T) string {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				_, _ = writer.Write([]byte("not-json"))
			}))
			t.Cleanup(server.Close)
			return server.URL
		},
		"trailing JSON": func(t *testing.T) string {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				var authorization AuthorizationRequest
				if err := json.NewDecoder(request.Body).Decode(&authorization); err != nil {
					t.Fatal(err)
				}
				response, err := json.Marshal(validAuthorizationResponse(authorization.RequestID))
				if err != nil {
					t.Fatal(err)
				}
				_, _ = writer.Write(append(response, []byte(`{}`)...))
			}))
			t.Cleanup(server.Close)
			return server.URL
		},
		"unavailable": func(t *testing.T) string {
			server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
			url := server.URL
			server.Close()
			return url
		},
	}
	for name, guardURL := range tests {
		t.Run(name, func(t *testing.T) {
			var upstreamRequests atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				upstreamRequests.Add(1)
			}))
			defer upstream.Close()
			target, err := url.Parse(upstream.URL)
			if err != nil {
				t.Fatal(err)
			}
			handler := NewProxyHandler(ProxyConfig{
				Target: target, Transport: http.DefaultTransport, GuardURL: guardURL(t),
				GuardToken: "test-token", CallerSPIFFEID: "spiffe://argus.local/agent/openclaw",
				TargetSPIFFEID: "spiffe://argus.local/service/openviking-cmem",
				TargetService:  "openviking-cmem", TargetOrigin: "https://openviking.argus.local:1943",
				DataClass: "sensitive",
			})
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://egress.local/health", nil))
			if response.Code != http.StatusServiceUnavailable {
				t.Fatalf("status = %d, body = %q", response.Code, response.Body.String())
			}
			if upstreamRequests.Load() != 0 {
				t.Fatalf("upstream requests = %d", upstreamRequests.Load())
			}
		})
	}
}

func TestWrongOpenVikingSPIFFEIDReturnsBadGateway(t *testing.T) {
	clientSVID, serverCertificate := testIdentityMaterial(
		t,
		"spiffe://argus.local/agent/openclaw",
		"spiffe://argus.local/service/not-openviking",
	)
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Fatal("upstream accepted the wrong server identity")
	}))
	upstream.TLS = &tls.Config{Certificates: []tls.Certificate{serverCertificate}, MinVersion: tls.VersionTLS12}
	upstream.StartTLS()
	defer upstream.Close()
	guard := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		var authorization AuthorizationRequest
		if err := json.NewDecoder(request.Body).Decode(&authorization); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(writer).Encode(validAuthorizationResponse(authorization.RequestID))
	}))
	defer guard.Close()
	store := NewIdentityStore("spiffe://argus.local/agent/openclaw")
	if _, err := store.ApplySnapshot(&broker.SubscribeToX509SVIDResponse{Svids: []*broker.X509SVID{clientSVID}}); err != nil {
		t.Fatal(err)
	}
	target, err := url.Parse(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	transport := &http.Transport{TLSClientConfig: store.ClientTLSConfig("spiffe://argus.local/service/openviking-cmem")}
	handler := NewProxyHandler(ProxyConfig{
		Target: target, Transport: transport, GuardURL: guard.URL,
		GuardToken: "test-token", CallerSPIFFEID: "spiffe://argus.local/agent/openclaw",
		TargetSPIFFEID: "spiffe://argus.local/service/openviking-cmem",
		TargetService:  "openviking-cmem", TargetOrigin: "https://openviking.argus.local:1943",
		DataClass: "sensitive",
	})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://egress.local/health", nil))
	if response.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, body = %q", response.Code, response.Body.String())
	}
}

func validAuthorizationResponse(requestID string) AuthorizationResponse {
	return AuthorizationResponse{
		RequestID: requestID, Decision: "ALLOW", Reason: "matched",
		DecisionID: "decision-1", ExpiresAtUnix: time.Now().Add(15 * time.Second).Unix(),
		PolicyID: "policy-1", RuleID: "rule-1",
	}
}

type bodyAfterAuthorization struct {
	reader     *strings.Reader
	authorized *atomic.Bool
}

func (body *bodyAfterAuthorization) Read(buffer []byte) (int, error) {
	if !body.authorized.Load() {
		return 0, io.ErrUnexpectedEOF
	}
	return body.reader.Read(buffer)
}

func (*bodyAfterAuthorization) Close() error { return nil }

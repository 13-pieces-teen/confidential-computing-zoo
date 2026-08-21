package sidecar

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"
)

type AuthorizationRequest struct {
	RequestID      string `json:"request_id"`
	CallerSPIFFEID string `json:"caller_spiffe_id"`
	TargetSPIFFEID string `json:"target_spiffe_id"`
	TargetService  string `json:"target_service"`
	TargetOrigin   string `json:"target_origin"`
	Operation      string `json:"operation"`
	DataClass      string `json:"data_class"`
}

type AuthorizationResponse struct {
	RequestID     string `json:"request_id"`
	Decision      string `json:"decision"`
	Reason        string `json:"reason"`
	DecisionID    string `json:"decision_id"`
	ExpiresAtUnix int64  `json:"expires_at_unix"`
	PolicyID      string `json:"policy_id"`
	RuleID        string `json:"rule_id"`
}

type ProxyConfig struct {
	Target         *url.URL
	Transport      http.RoundTripper
	GuardURL       string
	GuardToken     string
	CallerSPIFFEID string
	TargetSPIFFEID string
	TargetService  string
	TargetOrigin   string
	DataClass      string
	GuardTimeout   time.Duration
}

func NewProxyHandler(config ProxyConfig) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(config.Target)
	proxy.Transport = config.Transport
	proxy.ErrorHandler = func(writer http.ResponseWriter, _ *http.Request, _ error) {
		http.Error(writer, "OpenViking unavailable", http.StatusBadGateway)
	}
	guardTimeout := config.GuardTimeout
	if guardTimeout == 0 {
		guardTimeout = 2 * time.Second
	}
	guardHTTPClient := &http.Client{
		Transport: &http.Transport{Proxy: nil},
		Timeout:   guardTimeout,
	}
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		started := time.Now()
		statusWriter := &responseStatusWriter{ResponseWriter: writer, status: http.StatusOK}
		requestID := "none"
		decisionID := "none"
		defer func() {
			log.Printf(
				"request_id=%s method=%s path=%s decision_id=%s status=%d duration=%s",
				requestID,
				request.Method,
				request.URL.Path,
				decisionID,
				statusWriter.status,
				time.Since(started),
			)
		}()
		generatedRequestID, err := newRequestID()
		if err != nil {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		requestID = generatedRequestID
		authorization := AuthorizationRequest{
			RequestID:      requestID,
			CallerSPIFFEID: config.CallerSPIFFEID,
			TargetSPIFFEID: config.TargetSPIFFEID,
			TargetService:  config.TargetService,
			TargetOrigin:   config.TargetOrigin,
			Operation:      operationFor(request.Method),
			DataClass:      config.DataClass,
		}
		body, err := json.Marshal(authorization)
		if err != nil {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		guardRequest, err := http.NewRequestWithContext(request.Context(), http.MethodPost, config.GuardURL, bytes.NewReader(body))
		if err != nil {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		guardRequest.Header.Set("Authorization", "Bearer "+config.GuardToken)
		guardRequest.Header.Set("Content-Type", "application/json")
		guardResponse, err := guardHTTPClient.Do(guardRequest)
		if err != nil {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		defer guardResponse.Body.Close()
		if guardResponse.StatusCode < http.StatusOK || guardResponse.StatusCode >= http.StatusMultipleChoices {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		var decision AuthorizationResponse
		decoder := json.NewDecoder(guardResponse.Body)
		if err := decoder.Decode(&decision); err != nil {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		if err := decoder.Decode(&struct{}{}); err != io.EOF {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		if decision.DecisionID != "" {
			decisionID = decision.DecisionID
		}
		if decision.Decision == "DENY" {
			http.Error(statusWriter, "request denied by Argus Guard", http.StatusForbidden)
			return
		}
		if decision.Decision != "ALLOW" {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		if !validAllowDecision(decision, requestID, time.Now()) {
			http.Error(statusWriter, "Argus Guard unavailable", http.StatusServiceUnavailable)
			return
		}
		proxy.ServeHTTP(statusWriter, request)
	})
}

type responseStatusWriter struct {
	http.ResponseWriter
	status int
}

func (writer *responseStatusWriter) WriteHeader(status int) {
	writer.status = status
	writer.ResponseWriter.WriteHeader(status)
}

func (writer *responseStatusWriter) Unwrap() http.ResponseWriter {
	return writer.ResponseWriter
}

func validAllowDecision(decision AuthorizationResponse, requestID string, now time.Time) bool {
	return decision.RequestID == requestID &&
		decision.DecisionID != "" &&
		decision.PolicyID != "" &&
		decision.RuleID != "" &&
		decision.ExpiresAtUnix > now.Unix() &&
		decision.ExpiresAtUnix <= now.Add(300*time.Second).Unix()
}

func operationFor(method string) string {
	switch method {
	case http.MethodGet, http.MethodHead:
		return "memory.read"
	case http.MethodDelete:
		return "memory.delete"
	default:
		return "memory.write"
	}
}

func newRequestID() (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate request ID: %w", err)
	}
	return hex.EncodeToString(value), nil
}

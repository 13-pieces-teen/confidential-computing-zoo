package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
)

const maxProbeBody = 1 << 20

const (
	authorizationContextVersion = "argus-authorization-v2"
	authorizationContextDomain  = "argus-business-authorization-v2\x00"
	requestIDHeader             = "X-Argus-Request-ID"
	decisionIDHeader            = "X-Argus-Decision-ID"
	requestDigestHeader         = "X-Argus-Request-Digest"
	verificationModeHeader      = "X-Argus-Verification-Mode"
	maxGuardResponseBody        = 64 << 10
)

var errGuardDenied = errors.New("Argus Guard denied request")
var errRequestBodyTooLarge = errors.New("request body exceeds Guard authorization limit")

type authorizationContext struct {
	Version        string `json:"version"`
	RequestID      string `json:"request_id"`
	RequestDigest  string `json:"request_digest"`
	Method         string `json:"method"`
	PathAndQuery   string `json:"path_and_query"`
	BodySHA256     string `json:"body_sha256"`
	CallerSPIFFEID string `json:"caller_spiffe_id"`
	TargetSPIFFEID string `json:"target_spiffe_id"`
	TargetService  string `json:"target_service"`
	TargetURI      string `json:"target_uri"`
	Operation      string `json:"operation"`
	DataClass      string `json:"data_class"`
	IssuedAtUnix   int64  `json:"issued_at_unix"`
	Nonce          string `json:"nonce"`
}

type guardTarget struct {
	ServiceName string `json:"service_name"`
	TargetURI   string `json:"target_uri"`
}

type guardVerifyRequest struct {
	Target               guardTarget          `json:"target"`
	CallerID             string               `json:"caller_id"`
	AuthorizationContext authorizationContext `json:"authorization_context"`
}

type guardVerifyResponse struct {
	Decision         string          `json:"decision"`
	Reason           *string         `json:"reason"`
	Claims           json.RawMessage `json:"claims"`
	VerificationMode string          `json:"verification_mode"`
	DecisionID       *string         `json:"decision_id"`
	RequestDigest    *string         `json:"request_digest"`
	ExpiresAtUnix    *int64          `json:"expires_at_unix"`
}

type guardDecisionReceipt struct {
	DecisionID       string
	RequestDigest    string
	VerificationMode string
	ExpiresAtUnix    int64
}

type guardClient struct {
	endpoint         *url.URL
	httpClient       *http.Client
	expectedMode     string
	targetService    string
	targetURI        string
	callerSPIFFEID   string
	targetSPIFFEID   string
	dataClass        string
	maxBodyBytes     int64
	maxDecisionAhead time.Duration
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	if len(os.Args) < 2 {
		return fmt.Errorf("usage: spire-mtls <server|client-proxy|probe|identity> [flags]")
	}
	switch os.Args[1] {
	case "server":
		return runServer(os.Args[2:])
	case "client-proxy":
		return runClientProxy(os.Args[2:])
	case "probe":
		return runProbe(os.Args[2:])
	case "identity":
		return runIdentity(os.Args[2:])
	default:
		return fmt.Errorf("unknown mode %q; expected server, client-proxy, probe, or identity", os.Args[1])
	}
}

func runServer(arguments []string) error {
	flags := flag.NewFlagSet("server", flag.ContinueOnError)
	socket := flags.String("socket", "", "SPIFFE Workload API address")
	listen := flags.String("listen", "0.0.0.0:1943", "mTLS listen address")
	clientIDValue := flags.String("client-id", "", "only authorized client SPIFFE ID")
	upstreamValue := flags.String("upstream", "", "optional plaintext HTTP upstream")
	connMaxLifetime := flags.Duration(
		"conn-max-lifetime",
		60*time.Second,
		"maximum TLS connection lifetime before it is forced closed",
	)
	connIdleTimeout := flags.Duration(
		"conn-idle-timeout",
		30*time.Second,
		"idle keep-alive connection timeout",
	)
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *socket == "" || *clientIDValue == "" {
		return fmt.Errorf("-socket and -client-id are required")
	}
	if *connMaxLifetime <= 0 || *connIdleTimeout <= 0 {
		return fmt.Errorf("-conn-max-lifetime and -conn-idle-timeout must be positive")
	}
	clientID, err := spiffeid.FromString(*clientIDValue)
	if err != nil {
		return fmt.Errorf("parse client SPIFFE ID: %w", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	source, err := newX509Source(ctx, *socket)
	if err != nil {
		return err
	}
	defer source.Close()

	handler, err := serverHandler(*upstreamValue)
	if err != nil {
		return err
	}
	tlsConfig := tlsconfig.MTLSServerConfig(source, source, tlsconfig.AuthorizeID(clientID))
	listener, err := tls.Listen("tcp", *listen, tlsConfig)
	if err != nil {
		return fmt.Errorf("listen for SPIFFE mTLS: %w", err)
	}
	defer listener.Close()

	server := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       *connIdleTimeout,
	}
	errorChannel := make(chan error, 1)
	go func() {
		if err := server.Serve(&lifetimeListener{Listener: listener, lifetime: *connMaxLifetime}); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorChannel <- err
		}
	}()
	log.Printf(
		"SPIFFE mTLS server listening on %s; authorized client=%s; conn max lifetime=%s idle timeout=%s",
		*listen,
		clientID,
		*connMaxLifetime,
		*connIdleTimeout,
	)

	select {
	case <-ctx.Done():
	case err := <-errorChannel:
		return fmt.Errorf("serve SPIFFE mTLS: %w", err)
	}
	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return server.Shutdown(shutdownContext)
}

func runClientProxy(arguments []string) error {
	flags := flag.NewFlagSet("client-proxy", flag.ContinueOnError)
	socket := flags.String("socket", "", "SPIFFE Workload API address")
	listen := flags.String("listen", "0.0.0.0:1934", "local plaintext listen address")
	targetValue := flags.String("target", "", "remote mTLS target URL")
	serverIDValue := flags.String("server-id", "", "only authorized server SPIFFE ID")
	guardURLValue := flags.String(
		"guard-url",
		"",
		"required caller-side Argus Guard verification endpoint",
	)
	guardExpectedMode := flags.String(
		"guard-expected-mode",
		"",
		"required exact Guard verification_mode",
	)
	guardCallerIDValue := flags.String(
		"guard-caller-id",
		"",
		"required caller SPIFFE ID sent to Guard",
	)
	guardTargetService := flags.String(
		"guard-target-service",
		"",
		"required target service name sent to Guard",
	)
	guardDataClass := flags.String(
		"guard-data-class",
		"",
		"required caller-local data classification",
	)
	guardTimeout := flags.Duration(
		"guard-timeout",
		3*time.Second,
		"overall timeout for each Guard decision",
	)
	guardMaxBody := flags.Int64(
		"guard-max-body",
		8<<20,
		"maximum request body buffered before Guard authorization",
	)
	allowedSourceIPValue := flags.String(
		"allow-source-ip",
		"",
		"required exact source IP authorized to use this workload identity",
	)
	connMaxLifetime := flags.Duration(
		"conn-max-lifetime",
		60*time.Second,
		"maximum TLS connection lifetime before it is forced closed",
	)
	connIdleTimeout := flags.Duration(
		"conn-idle-timeout",
		30*time.Second,
		"idle keep-alive connection timeout",
	)
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *socket == "" ||
		*targetValue == "" ||
		*serverIDValue == "" ||
		*guardURLValue == "" ||
		*guardExpectedMode == "" ||
		*guardCallerIDValue == "" ||
		*guardTargetService == "" ||
		*guardDataClass == "" ||
		*allowedSourceIPValue == "" {
		return fmt.Errorf(
			"-socket, -target, -server-id, -guard-url, -guard-expected-mode, " +
				"-guard-caller-id, -guard-target-service, -guard-data-class, and " +
				"-allow-source-ip are required",
		)
	}
	if *guardTimeout <= 0 || *guardTimeout > 30*time.Second {
		return fmt.Errorf("-guard-timeout must be greater than zero and at most 30s")
	}
	if *guardMaxBody <= 0 || *guardMaxBody > 64<<20 {
		return fmt.Errorf("-guard-max-body must be greater than zero and at most 64 MiB")
	}
	if *connMaxLifetime <= 0 || *connIdleTimeout <= 0 {
		return fmt.Errorf("-conn-max-lifetime and -conn-idle-timeout must be positive")
	}
	target, err := parseHTTPURL(*targetValue, true)
	if err != nil {
		return err
	}
	serverID, err := spiffeid.FromString(*serverIDValue)
	if err != nil {
		return fmt.Errorf("parse server SPIFFE ID: %w", err)
	}
	guardCallerID, err := spiffeid.FromString(*guardCallerIDValue)
	if err != nil {
		return fmt.Errorf("parse Guard caller SPIFFE ID: %w", err)
	}
	guardEndpoint, err := parseGuardURL(*guardURLValue)
	if err != nil {
		return err
	}
	if !validAuthorizationLabel(*guardExpectedMode) {
		return fmt.Errorf("-guard-expected-mode is invalid")
	}
	if !validAuthorizationLabel(*guardTargetService) {
		return fmt.Errorf("-guard-target-service is invalid")
	}
	if !validAuthorizationLabel(*guardDataClass) {
		return fmt.Errorf("-guard-data-class is invalid")
	}
	allowedSourceIP := net.ParseIP(*allowedSourceIPValue)
	if allowedSourceIP == nil {
		return fmt.Errorf(
			"parse allowed source IP %q: expected an IP address",
			*allowedSourceIPValue,
		)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	source, err := newX509Source(ctx, *socket)
	if err != nil {
		return err
	}
	defer source.Close()
	callerSVID, err := source.GetX509SVID()
	if err != nil {
		return fmt.Errorf("get OpenClaw X.509-SVID before starting egress: %w", err)
	}
	if callerSVID.ID.String() != guardCallerID.String() {
		return fmt.Errorf(
			"OpenClaw X.509-SVID is %s; Guard caller identity requires %s",
			callerSVID.ID,
			guardCallerID,
		)
	}

	guard := &guardClient{
		endpoint: guardEndpoint,
		httpClient: &http.Client{
			Transport: &http.Transport{Proxy: nil},
			Timeout:   *guardTimeout,
		},
		expectedMode:     *guardExpectedMode,
		targetService:    *guardTargetService,
		targetURI:        target.String(),
		callerSPIFFEID:   guardCallerID.String(),
		targetSPIFFEID:   serverID.String(),
		dataClass:        *guardDataClass,
		maxBodyBytes:     *guardMaxBody,
		maxDecisionAhead: 5 * time.Minute,
	}

	proxy := httputil.NewSingleHostReverseProxy(target)
	upstreamTransport := &http.Transport{
		TLSClientConfig: tlsconfig.MTLSClientConfig(
			source,
			source,
			tlsconfig.AuthorizeID(serverID),
		),
		MaxIdleConns:          10,
		MaxIdleConnsPerHost:   2,
		IdleConnTimeout:       *connIdleTimeout,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			rawConn, err := (&net.Dialer{Timeout: 10 * time.Second}).DialContext(ctx, network, addr)
			if err != nil {
				return nil, err
			}
			return newLifetimeConn(rawConn, *connMaxLifetime)
		},
	}
	proxy.Transport = upstreamTransport
	// Drain idle mTLS connections whenever the Workload API reports an X.509
	// context update, so a rotated SVID or trust bundle is used for new
	// connections instead of reusing a stale one (WP3 convergence).
	watchErrorChannel := make(chan error, 1)
	go func() {
		err := workloadapi.WatchX509Context(
			ctx,
			&x509ContextDrainWatcher{drain: func() { upstreamTransport.CloseIdleConnections() }},
			workloadapi.WithAddr(*socket),
		)
		if ctx.Err() != nil {
			return
		}
		if err == nil {
			err = errors.New("X.509 context watch stopped unexpectedly")
		}
		watchErrorChannel <- err
	}()
	proxy.ModifyResponse = func(response *http.Response) error {
		response.Header.Del(requestIDHeader)
		response.Header.Del(decisionIDHeader)
		response.Header.Del(requestDigestHeader)
		response.Header.Del(verificationModeHeader)
		return nil
	}
	proxy.ErrorHandler = func(writer http.ResponseWriter, _ *http.Request, proxyErr error) {
		log.Printf("mTLS upstream request rejected: %v", proxyErr)
		http.Error(writer, "mTLS upstream unavailable", http.StatusBadGateway)
	}
	handler := http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		startedAt := time.Now()
		sourceIP, sourceErr := remoteIP(request.RemoteAddr)
		clientRequestID := validRequestID(request.Header.Get(requestIDHeader))
		if clientRequestID == "" {
			clientRequestID = "none"
		}
		requestID, requestIDErr := newRequestID()
		if requestIDErr != nil {
			writeExactResponse(
				writer,
				http.StatusServiceUnavailable,
				"OpenClaw egress request ID unavailable",
			)
			log.Printf(
				"request_id=none client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=request_id_error",
				clientRequestID,
				request.Method,
				request.URL.EscapedPath(),
				http.StatusServiceUnavailable,
				time.Since(startedAt).Round(time.Millisecond),
				sourceIPString(sourceIP, sourceErr),
			)
			return
		}
		request.Header.Del(requestIDHeader)
		request.Header.Del(decisionIDHeader)
		request.Header.Del(requestDigestHeader)
		request.Header.Del(verificationModeHeader)
		request.Header.Set(requestIDHeader, requestID)
		writer.Header().Set(requestIDHeader, requestID)

		recorder := &statusRecorder{ResponseWriter: writer}
		if sourceErr != nil || !sourceIP.Equal(allowedSourceIP) {
			writeExactResponse(
				recorder,
				http.StatusForbidden,
				"OpenClaw egress source rejected",
			)
			log.Printf(
				"request_id=%s client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=source_rejected",
				requestID,
				clientRequestID,
				request.Method,
				request.URL.EscapedPath(),
				recorder.statusCode(),
				time.Since(startedAt).Round(time.Millisecond),
				sourceIPString(sourceIP, sourceErr),
			)
			return
		}

		body, bodyErr := readAuthorizationBody(request, guard.maxBodyBytes)
		if bodyErr != nil {
			status := http.StatusBadRequest
			bodyMessage := "OpenClaw egress request body rejected"
			decision := "body_rejected"
			if errors.Is(bodyErr, errRequestBodyTooLarge) {
				status = http.StatusRequestEntityTooLarge
				bodyMessage = "OpenClaw egress request body too large"
				decision = "body_too_large"
			}
			writeExactResponse(recorder, status, bodyMessage)
			log.Printf("Guard request body preparation failed: %v", bodyErr)
			log.Printf(
				"request_id=%s client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=%s",
				requestID,
				clientRequestID,
				request.Method,
				request.URL.EscapedPath(),
				recorder.statusCode(),
				time.Since(startedAt).Round(time.Millisecond),
				sourceIP.String(),
				decision,
			)
			return
		}
		authorization, authorizationErr := guard.newAuthorizationContext(request, requestID, body)
		if authorizationErr != nil {
			writeExactResponse(
				recorder,
				http.StatusServiceUnavailable,
				"OpenClaw egress authorization context unavailable",
			)
			log.Printf("Build Guard authorization context failed: %v", authorizationErr)
			log.Printf(
				"request_id=%s client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=authorization_context_error",
				requestID,
				clientRequestID,
				request.Method,
				request.URL.EscapedPath(),
				recorder.statusCode(),
				time.Since(startedAt).Round(time.Millisecond),
				sourceIP.String(),
			)
			return
		}
		receipt, guardErr := guard.authorize(request.Context(), authorization)
		if guardErr != nil {
			if errors.Is(guardErr, errGuardDenied) {
				writer.Header().Set(decisionIDHeader, receipt.DecisionID)
				writer.Header().Set(requestDigestHeader, receipt.RequestDigest)
				writer.Header().Set(verificationModeHeader, receipt.VerificationMode)
				writeExactResponse(
					recorder,
					http.StatusForbidden,
					"Argus Guard denied request",
				)
				log.Printf("Guard authorization denied: %v", guardErr)
				log.Printf(
					"request_id=%s client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=guard_denied guard_decision_id=%s request_digest=%s verification_mode=%s",
					requestID,
					clientRequestID,
					request.Method,
					request.URL.EscapedPath(),
					recorder.statusCode(),
					time.Since(startedAt).Round(time.Millisecond),
					sourceIP.String(),
					receipt.DecisionID,
					receipt.RequestDigest,
					receipt.VerificationMode,
				)
				return
			}
			writeExactResponse(
				recorder,
				http.StatusServiceUnavailable,
				"Argus Guard unavailable",
			)
			log.Printf("Guard authorization failed: %v", guardErr)
			log.Printf(
				"request_id=%s client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=guard_error request_digest=%s",
				requestID,
				clientRequestID,
				request.Method,
				request.URL.EscapedPath(),
				recorder.statusCode(),
				time.Since(startedAt).Round(time.Millisecond),
				sourceIP.String(),
				authorization.RequestDigest,
			)
			return
		}
		if time.Now().Unix() >= receipt.ExpiresAtUnix {
			writeExactResponse(
				recorder,
				http.StatusServiceUnavailable,
				"Argus Guard unavailable",
			)
			log.Printf(
				"request_id=%s client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=guard_error request_digest=%s reason=decision_expired_before_forward",
				requestID,
				clientRequestID,
				request.Method,
				request.URL.EscapedPath(),
				recorder.statusCode(),
				time.Since(startedAt).Round(time.Millisecond),
				sourceIP.String(),
				authorization.RequestDigest,
			)
			return
		}
		request.Header.Set(decisionIDHeader, receipt.DecisionID)
		request.Header.Set(requestDigestHeader, receipt.RequestDigest)
		request.Header.Set(verificationModeHeader, receipt.VerificationMode)
		writer.Header().Set(decisionIDHeader, receipt.DecisionID)
		writer.Header().Set(requestDigestHeader, receipt.RequestDigest)
		writer.Header().Set(verificationModeHeader, receipt.VerificationMode)

		proxy.ServeHTTP(recorder, request)
		log.Printf(
			"request_id=%s client_request_id=%s method=%s path=%s status=%d duration=%s source_ip=%s decision=forwarded_mtls guard_decision_id=%s request_digest=%s verification_mode=%s",
			requestID,
			clientRequestID,
			request.Method,
			request.URL.EscapedPath(),
			recorder.statusCode(),
			time.Since(startedAt).Round(time.Millisecond),
			sourceIP.String(),
			receipt.DecisionID,
			receipt.RequestDigest,
			receipt.VerificationMode,
		)
	})
	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		return fmt.Errorf("listen for local proxy: %w", err)
	}
	defer listener.Close()
	server := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       *connIdleTimeout,
	}
	errorChannel := make(chan error, 1)
	go func() {
		if err := server.Serve(&lifetimeListener{Listener: listener, lifetime: *connMaxLifetime}); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorChannel <- err
		}
	}()
	log.Printf(
		"local proxy listening on %s; mTLS target=%s authorized server=%s allowed source=%s Guard=%s mode=%s conn max lifetime=%s idle timeout=%s",
		*listen,
		target,
		serverID,
		allowedSourceIP.String(),
		guard.endpoint,
		guard.expectedMode,
		*connMaxLifetime,
		*connIdleTimeout,
	)

	select {
	case <-ctx.Done():
	case err := <-errorChannel:
		return fmt.Errorf("serve local proxy: %w", err)
	case err := <-watchErrorChannel:
		return fmt.Errorf("watch X.509 context for connection draining: %w", err)
	}
	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return server.Shutdown(shutdownContext)
}

func parseGuardURL(value string) (*url.URL, error) {
	parsed, err := url.Parse(value)
	if err != nil ||
		parsed.Host == "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") ||
		parsed.RawQuery != "" ||
		parsed.Fragment != "" ||
		parsed.User != nil ||
		parsed.Path != "/ra/v1/verify" {
		return nil, fmt.Errorf(
			"invalid Guard URL %q; expected http(s)://host/ra/v1/verify",
			value,
		)
	}
	return parsed, nil
}

func validAuthorizationLabel(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for _, character := range value {
		switch {
		case character >= 'a' && character <= 'z':
		case character >= 'A' && character <= 'Z':
		case character >= '0' && character <= '9':
		case character == '-', character == '_', character == '.':
		default:
			return false
		}
	}
	return true
}

func writeExactResponse(writer http.ResponseWriter, status int, body string) {
	writer.Header().Set("Content-Type", "text/plain; charset=utf-8")
	writer.Header().Set("X-Content-Type-Options", "nosniff")
	writer.WriteHeader(status)
	_, _ = io.WriteString(writer, body)
}

func readAuthorizationBody(request *http.Request, maxBodyBytes int64) ([]byte, error) {
	if request.Body == nil || request.Body == http.NoBody {
		request.Body = http.NoBody
		request.ContentLength = 0
		request.TransferEncoding = nil
		return []byte{}, nil
	}
	defer request.Body.Close()
	body, err := io.ReadAll(io.LimitReader(request.Body, maxBodyBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read request body for Guard authorization: %w", err)
	}
	if int64(len(body)) > maxBodyBytes {
		return nil, errRequestBodyTooLarge
	}
	request.Body = io.NopCloser(bytes.NewReader(body))
	request.GetBody = func() (io.ReadCloser, error) {
		return io.NopCloser(bytes.NewReader(body)), nil
	}
	request.ContentLength = int64(len(body))
	request.TransferEncoding = nil
	return body, nil
}

func appendAuthorizationField(buffer *bytes.Buffer, value string) error {
	if len(value) > 8192 {
		return fmt.Errorf("authorization field exceeds 8192 bytes")
	}
	var length [4]byte
	binary.BigEndian.PutUint32(length[:], uint32(len(value)))
	_, _ = buffer.Write(length[:])
	_, _ = buffer.WriteString(value)
	return nil
}

func authorizationDigest(context authorizationContext) (string, error) {
	buffer := bytes.NewBuffer(make([]byte, 0, 1024))
	_, _ = buffer.WriteString(authorizationContextDomain)
	for _, value := range []string{
		context.Version,
		context.RequestID,
		context.Method,
		context.PathAndQuery,
		context.BodySHA256,
		context.CallerSPIFFEID,
		context.TargetSPIFFEID,
		context.TargetService,
		context.TargetURI,
		context.Operation,
		context.DataClass,
		fmt.Sprintf("%d", context.IssuedAtUnix),
		context.Nonce,
	} {
		if err := appendAuthorizationField(buffer, value); err != nil {
			return "", err
		}
	}
	digest := sha256.Sum256(buffer.Bytes())
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

func requestPathAndQuery(request *http.Request) string {
	path := request.URL.EscapedPath()
	if path == "" {
		path = "/"
	}
	if request.URL.RawQuery != "" {
		path += "?" + request.URL.RawQuery
	}
	return path
}

func randomHex(byteCount int) (string, error) {
	randomBytes := make([]byte, byteCount)
	if _, err := rand.Read(randomBytes); err != nil {
		return "", fmt.Errorf("generate cryptographic random value: %w", err)
	}
	return hex.EncodeToString(randomBytes), nil
}

func (client *guardClient) newAuthorizationContext(
	request *http.Request,
	requestID string,
	body []byte,
) (authorizationContext, error) {
	nonce, err := randomHex(16)
	if err != nil {
		return authorizationContext{}, err
	}
	bodyDigest := sha256.Sum256(body)
	authorization := authorizationContext{
		Version:        authorizationContextVersion,
		RequestID:      requestID,
		Method:         request.Method,
		PathAndQuery:   requestPathAndQuery(request),
		BodySHA256:     "sha256:" + hex.EncodeToString(bodyDigest[:]),
		CallerSPIFFEID: client.callerSPIFFEID,
		TargetSPIFFEID: client.targetSPIFFEID,
		TargetService:  client.targetService,
		TargetURI:      client.targetURI,
		Operation:      "http:" + request.Method,
		DataClass:      client.dataClass,
		IssuedAtUnix:   time.Now().Unix(),
		Nonce:          nonce,
	}
	authorization.RequestDigest, err = authorizationDigest(authorization)
	if err != nil {
		return authorizationContext{}, err
	}
	return authorization, nil
}

func validDecisionID(value string) bool {
	if len(value) != 32 {
		return false
	}
	for _, character := range value {
		if !((character >= '0' && character <= '9') ||
			(character >= 'a' && character <= 'f')) {
			return false
		}
	}
	return true
}

func (client *guardClient) authorize(
	ctx context.Context,
	authorization authorizationContext,
) (guardDecisionReceipt, error) {
	payload, err := json.Marshal(guardVerifyRequest{
		Target: guardTarget{
			ServiceName: client.targetService,
			TargetURI:   client.targetURI,
		},
		CallerID:             client.callerSPIFFEID,
		AuthorizationContext: authorization,
	})
	if err != nil {
		return guardDecisionReceipt{}, fmt.Errorf("encode Guard request: %w", err)
	}
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		client.endpoint.String(),
		bytes.NewReader(payload),
	)
	if err != nil {
		return guardDecisionReceipt{}, fmt.Errorf("build Guard request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set(requestIDHeader, authorization.RequestID)

	response, err := client.httpClient.Do(request)
	if err != nil {
		return guardDecisionReceipt{}, fmt.Errorf("call Argus Guard: %w", err)
	}
	defer response.Body.Close()
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, maxGuardResponseBody+1))
	if err != nil {
		return guardDecisionReceipt{}, fmt.Errorf("read Argus Guard response: %w", err)
	}
	if len(responseBody) > maxGuardResponseBody {
		return guardDecisionReceipt{}, fmt.Errorf("Argus Guard response exceeded %d bytes", maxGuardResponseBody)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return guardDecisionReceipt{}, fmt.Errorf(
			"Argus Guard returned HTTP %d",
			response.StatusCode,
		)
	}
	decoder := json.NewDecoder(bytes.NewReader(responseBody))
	decoder.DisallowUnknownFields()
	var guardResponse guardVerifyResponse
	if err := decoder.Decode(&guardResponse); err != nil {
		return guardDecisionReceipt{}, fmt.Errorf("decode Argus Guard response: %w", err)
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return guardDecisionReceipt{}, fmt.Errorf("Argus Guard response has trailing JSON")
	}
	if guardResponse.VerificationMode != client.expectedMode {
		return guardDecisionReceipt{}, fmt.Errorf(
			"Argus Guard verification_mode is %q; expected %q",
			guardResponse.VerificationMode,
			client.expectedMode,
		)
	}
	if client.expectedMode == "mock_allow" &&
		!bytes.Equal(bytes.TrimSpace(guardResponse.Claims), []byte("null")) {
		return guardDecisionReceipt{}, fmt.Errorf(
			"mock_allow Guard response contained verified claims",
		)
	}
	if guardResponse.DecisionID == nil ||
		!validDecisionID(*guardResponse.DecisionID) ||
		guardResponse.RequestDigest == nil ||
		*guardResponse.RequestDigest != authorization.RequestDigest ||
		guardResponse.ExpiresAtUnix == nil {
		return guardDecisionReceipt{}, fmt.Errorf(
			"Argus Guard response did not contain a valid matching decision receipt",
		)
	}
	now := time.Now().Unix()
	maximumExpiry := time.Now().Add(client.maxDecisionAhead).Unix()
	if *guardResponse.ExpiresAtUnix <= now || *guardResponse.ExpiresAtUnix > maximumExpiry {
		return guardDecisionReceipt{}, fmt.Errorf(
			"Argus Guard decision expiry is outside the accepted window",
		)
	}
	receipt := guardDecisionReceipt{
		DecisionID:       *guardResponse.DecisionID,
		RequestDigest:    *guardResponse.RequestDigest,
		VerificationMode: guardResponse.VerificationMode,
		ExpiresAtUnix:    *guardResponse.ExpiresAtUnix,
	}
	switch guardResponse.Decision {
	case "ALLOW":
		return receipt, nil
	case "DENY":
		reason := "no reason"
		if guardResponse.Reason != nil && *guardResponse.Reason != "" {
			reason = *guardResponse.Reason
		}
		return receipt, fmt.Errorf("%w: %s", errGuardDenied, reason)
	default:
		return guardDecisionReceipt{}, fmt.Errorf(
			"Argus Guard returned decision %q",
			guardResponse.Decision,
		)
	}
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (recorder *statusRecorder) WriteHeader(status int) {
	if status >= 100 && status < 200 && status != http.StatusSwitchingProtocols {
		recorder.ResponseWriter.WriteHeader(status)
		return
	}
	if recorder.status != 0 {
		return
	}
	recorder.status = status
	recorder.ResponseWriter.WriteHeader(status)
}

func (recorder *statusRecorder) Write(payload []byte) (int, error) {
	if recorder.status == 0 {
		recorder.WriteHeader(http.StatusOK)
	}
	return recorder.ResponseWriter.Write(payload)
}

func (recorder *statusRecorder) Unwrap() http.ResponseWriter {
	return recorder.ResponseWriter
}

func (recorder *statusRecorder) statusCode() int {
	if recorder.status == 0 {
		return http.StatusOK
	}
	return recorder.status
}

func remoteIP(remoteAddress string) (net.IP, error) {
	host, _, err := net.SplitHostPort(remoteAddress)
	if err != nil {
		return nil, fmt.Errorf("parse remote address %q: %w", remoteAddress, err)
	}
	parsed := net.ParseIP(host)
	if parsed == nil {
		return nil, fmt.Errorf("parse remote IP %q", host)
	}
	return parsed, nil
}

func sourceIPString(sourceIP net.IP, err error) string {
	if err != nil {
		return "invalid"
	}
	return sourceIP.String()
}

func validRequestID(value string) string {
	if value == "" || len(value) > 128 {
		return ""
	}
	for _, character := range value {
		switch {
		case character >= 'a' && character <= 'z':
		case character >= 'A' && character <= 'Z':
		case character >= '0' && character <= '9':
		case character == '-', character == '_', character == '.':
		default:
			return ""
		}
	}
	return value
}

func newRequestID() (string, error) {
	return randomHex(12)
}

// lifetimeConn enforces an absolute connection expiry. The timer interrupts
// active I/O, while the deadline methods prevent higher protocol layers from
// extending their own deadlines beyond the configured maximum lifetime.
type lifetimeConn struct {
	net.Conn
	expiresAt time.Time
	timer     *time.Timer
}

func newLifetimeConn(conn net.Conn, lifetime time.Duration) (*lifetimeConn, error) {
	expiresAt := time.Now().Add(lifetime)
	if err := conn.SetDeadline(expiresAt); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("set connection lifetime deadline: %w", err)
	}
	wrapped := &lifetimeConn{Conn: conn, expiresAt: expiresAt}
	wrapped.timer = time.AfterFunc(lifetime, func() {
		_ = conn.Close()
	})
	return wrapped, nil
}

func (conn *lifetimeConn) Close() error {
	if conn.timer != nil {
		conn.timer.Stop()
	}
	return conn.Conn.Close()
}

func (conn *lifetimeConn) SetDeadline(deadline time.Time) error {
	return conn.Conn.SetDeadline(conn.capDeadline(deadline))
}

func (conn *lifetimeConn) SetReadDeadline(deadline time.Time) error {
	return conn.Conn.SetReadDeadline(conn.capDeadline(deadline))
}

func (conn *lifetimeConn) SetWriteDeadline(deadline time.Time) error {
	return conn.Conn.SetWriteDeadline(conn.capDeadline(deadline))
}

func (conn *lifetimeConn) capDeadline(deadline time.Time) time.Time {
	if deadline.IsZero() || deadline.After(conn.expiresAt) {
		return conn.expiresAt
	}
	return deadline
}

// lifetimeListener wraps a net.Listener so every accepted connection has a
// bounded lifetime.
type lifetimeListener struct {
	net.Listener
	lifetime time.Duration
}

func (listener *lifetimeListener) Accept() (net.Conn, error) {
	conn, err := listener.Listener.Accept()
	if err != nil {
		return nil, err
	}
	return newLifetimeConn(conn, listener.lifetime)
}

// x509ContextDrainWatcher drains idle mTLS connections whenever the Workload
// API reports an X.509 context (SVID or trust bundle) update, so connections
// carrying a rotated identity are not reused.
type x509ContextDrainWatcher struct {
	drain func()
}

func (watcher *x509ContextDrainWatcher) OnX509ContextUpdate(*workloadapi.X509Context) {
	watcher.drain()
}

func (watcher *x509ContextDrainWatcher) OnX509ContextWatchError(err error) {
	log.Printf("X.509 context watch error while draining idle mTLS connections: %v", err)
}

func runProbe(arguments []string) error {
	flags := flag.NewFlagSet("probe", flag.ContinueOnError)
	socket := flags.String("socket", "", "SPIFFE Workload API address")
	targetValue := flags.String("target", "", "mTLS URL to request")
	serverIDValue := flags.String("server-id", "", "only authorized server SPIFFE ID")
	timeout := flags.Duration("timeout", 10*time.Second, "overall probe timeout")
	expectServerIDRejection := flags.Bool(
		"expect-server-id-rejection",
		false,
		"succeed only when a reachable SPIFFE peer presents a different server ID",
	)
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *socket == "" || *targetValue == "" || *serverIDValue == "" {
		return fmt.Errorf("-socket, -target, and -server-id are required")
	}
	if _, err := parseHTTPURL(*targetValue, true); err != nil {
		return err
	}
	serverID, err := spiffeid.FromString(*serverIDValue)
	if err != nil {
		return fmt.Errorf("parse server SPIFFE ID: %w", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	source, err := newX509Source(ctx, *socket)
	if err != nil {
		return err
	}
	defer source.Close()
	var observedServerID spiffeid.ID
	var serverIDObserved bool
	authorizer := tlsconfig.Authorizer(func(
		actual spiffeid.ID,
		verifiedChains [][]*x509.Certificate,
	) error {
		observedServerID = actual
		serverIDObserved = true
		return tlsconfig.AuthorizeID(serverID)(actual, verifiedChains)
	})
	client := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: tlsconfig.MTLSClientConfig(
				source,
				source,
				authorizer,
			),
		},
		Timeout: *timeout,
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, *targetValue, nil)
	if err != nil {
		return fmt.Errorf("build probe request: %w", err)
	}
	response, err := client.Do(request)
	if *expectServerIDRejection {
		if err == nil {
			response.Body.Close()
			return fmt.Errorf(
				"server SPIFFE ID %s was unexpectedly authorized",
				observedServerID,
			)
		}
		if !serverIDObserved {
			return fmt.Errorf(
				"probe failed before the server SPIFFE ID could be authorized: %w",
				err,
			)
		}
		if observedServerID == serverID {
			return fmt.Errorf(
				"probe failed after the expected server SPIFFE ID %s was observed: %w",
				serverID,
				err,
			)
		}
		fmt.Printf(
			"server SPIFFE ID rejected as expected: actual=%s expected=%s\n",
			observedServerID,
			serverID,
		)
		return nil
	}
	if err != nil {
		return fmt.Errorf("SPIFFE mTLS probe failed: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxProbeBody))
	if err != nil {
		return fmt.Errorf("read probe response: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("probe returned HTTP %d: %s", response.StatusCode, body)
	}
	_, _ = os.Stdout.Write(body)
	if len(body) == 0 || body[len(body)-1] != '\n' {
		fmt.Println()
	}
	return nil
}

func runIdentity(arguments []string) error {
	flags := flag.NewFlagSet("identity", flag.ContinueOnError)
	socket := flags.String("socket", "", "SPIFFE Workload API address")
	expectedValue := flags.String("expected-id", "", "optional exact expected workload SPIFFE ID")
	expectNoIdentity := flags.Bool(
		"expect-no-identity",
		false,
		"succeed only when the Workload API explicitly denies identity issuance",
	)
	expiryUnix := flags.Bool(
		"expiry-unix",
		false,
		"print only the current X.509-SVID NotAfter time as Unix seconds",
	)
	timeout := flags.Duration("timeout", 10*time.Second, "Workload API timeout")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *socket == "" {
		return fmt.Errorf("-socket is required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	if *expectNoIdentity {
		return expectIdentityDenial(ctx, *socket)
	}
	source, err := newX509Source(ctx, *socket)
	if err != nil {
		return err
	}
	defer source.Close()
	svid, err := source.GetX509SVID()
	if err != nil {
		return fmt.Errorf("get X.509-SVID: %w", err)
	}
	if *expectedValue != "" && svid.ID.String() != *expectedValue {
		return fmt.Errorf("workload SPIFFE ID is %s; expected %s", svid.ID, *expectedValue)
	}
	if *expiryUnix {
		if len(svid.Certificates) == 0 {
			return errors.New("X.509-SVID has no certificates")
		}
		fmt.Println(svid.Certificates[0].NotAfter.Unix())
		return nil
	}
	fmt.Println(svid.ID)
	return nil
}

func expectIdentityDenial(ctx context.Context, socket string) error {
	client, err := workloadapi.New(ctx, workloadapi.WithAddr(socket))
	if err != nil {
		return fmt.Errorf("connect to Workload API at %s: %w", socket, err)
	}
	defer client.Close()
	svid, err := client.FetchX509SVID(ctx)
	if err == nil {
		return fmt.Errorf("workload unexpectedly obtained X.509-SVID %s", svid.ID)
	}
	if !isNoIdentityDenial(err) {
		return fmt.Errorf(
			"Workload API failed without an explicit identity denial: %w",
			err,
		)
	}
	fmt.Println("Workload API denied identity issuance as expected")
	return nil
}

func isNoIdentityDenial(err error) bool {
	message := err.Error()
	return strings.Contains(message, "code = PermissionDenied") &&
		strings.Contains(message, "no identity issued")
}

func newX509Source(ctx context.Context, socket string) (*workloadapi.X509Source, error) {
	source, err := workloadapi.NewX509Source(
		ctx,
		workloadapi.WithClientOptions(workloadapi.WithAddr(socket)),
	)
	if err != nil {
		return nil, fmt.Errorf("load X.509-SVID from %s: %w", socket, err)
	}
	return source, nil
}

func serverHandler(upstreamValue string) (http.Handler, error) {
	if upstreamValue == "" {
		mux := http.NewServeMux()
		mux.HandleFunc("/health", func(writer http.ResponseWriter, _ *http.Request) {
			writer.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(writer, `{"status":"ok","transport":"spiffe-mtls"}`+"\n")
		})
		return mux, nil
	}
	upstream, err := parseHTTPURL(upstreamValue, false)
	if err != nil {
		return nil, err
	}
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	proxy.ErrorHandler = func(writer http.ResponseWriter, _ *http.Request, proxyErr error) {
		log.Printf("plaintext service upstream failed: %v", proxyErr)
		http.Error(writer, "service upstream unavailable", http.StatusBadGateway)
	}
	return proxy, nil
}

func parseHTTPURL(value string, requireTLS bool) (*url.URL, error) {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Host == "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, fmt.Errorf("invalid HTTP URL %q", value)
	}
	if requireTLS && parsed.Scheme != "https" {
		return nil, fmt.Errorf("mTLS target must use https")
	}
	if !requireTLS && parsed.Scheme != "http" {
		return nil, fmt.Errorf("service upstream must use http")
	}
	return parsed, nil
}

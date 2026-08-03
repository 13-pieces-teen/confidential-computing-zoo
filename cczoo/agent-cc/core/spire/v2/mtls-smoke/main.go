package main

import (
	"context"
	"crypto/tls"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
)

const maxProbeBody = 1 << 20

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
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *socket == "" || *clientIDValue == "" {
		return fmt.Errorf("-socket and -client-id are required")
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
	}
	errorChannel := make(chan error, 1)
	go func() {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorChannel <- err
		}
	}()
	log.Printf("SPIFFE mTLS server listening on %s; authorized client=%s", *listen, clientID)

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
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *socket == "" || *targetValue == "" || *serverIDValue == "" {
		return fmt.Errorf("-socket, -target, and -server-id are required")
	}
	target, err := parseHTTPURL(*targetValue, true)
	if err != nil {
		return err
	}
	serverID, err := spiffeid.FromString(*serverIDValue)
	if err != nil {
		return fmt.Errorf("parse server SPIFFE ID: %w", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	source, err := newX509Source(ctx, *socket)
	if err != nil {
		return err
	}
	defer source.Close()

	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.Transport = &http.Transport{
		TLSClientConfig: tlsconfig.MTLSClientConfig(
			source,
			source,
			tlsconfig.AuthorizeID(serverID),
		),
	}
	proxy.ErrorHandler = func(writer http.ResponseWriter, _ *http.Request, proxyErr error) {
		log.Printf("mTLS upstream request rejected: %v", proxyErr)
		http.Error(writer, "mTLS upstream unavailable", http.StatusBadGateway)
	}
	server := &http.Server{
		Addr:              *listen,
		Handler:           proxy,
		ReadHeaderTimeout: 5 * time.Second,
	}
	errorChannel := make(chan error, 1)
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorChannel <- err
		}
	}()
	log.Printf(
		"local proxy listening on %s; mTLS target=%s authorized server=%s",
		*listen,
		target,
		serverID,
	)

	select {
	case <-ctx.Done():
	case err := <-errorChannel:
		return fmt.Errorf("serve local proxy: %w", err)
	}
	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return server.Shutdown(shutdownContext)
}

func runProbe(arguments []string) error {
	flags := flag.NewFlagSet("probe", flag.ContinueOnError)
	socket := flags.String("socket", "", "SPIFFE Workload API address")
	targetValue := flags.String("target", "", "mTLS URL to request")
	serverIDValue := flags.String("server-id", "", "only authorized server SPIFFE ID")
	timeout := flags.Duration("timeout", 10*time.Second, "overall probe timeout")
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
	client := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: tlsconfig.MTLSClientConfig(
				source,
				source,
				tlsconfig.AuthorizeID(serverID),
			),
		},
		Timeout: *timeout,
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, *targetValue, nil)
	if err != nil {
		return fmt.Errorf("build probe request: %w", err)
	}
	response, err := client.Do(request)
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
	timeout := flags.Duration("timeout", 10*time.Second, "Workload API timeout")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *socket == "" {
		return fmt.Errorf("-socket is required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
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
	fmt.Println(svid.ID)
	return nil
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

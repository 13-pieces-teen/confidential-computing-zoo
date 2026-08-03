package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/fakeservices"
)

type options struct {
	listen         string
	serverCertPath string
	serverKeyPath  string
	clientCAPath   string
	instanceID     string
	tcbStatus      string
	mrtd           string
	rtmr           [4]string
	debugEnabled   bool
	trusteeStatus  int
	trusteeDelay   time.Duration
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	options := parseFlags()
	handler, err := fakeservices.NewHandler(fakeservices.Config{
		InstanceID: options.instanceID,
		TCBStatus:  options.tcbStatus,
		MRTD:       options.mrtd,
		RTMR: map[string]*string{
			"0": optionalString(options.rtmr[0]),
			"1": optionalString(options.rtmr[1]),
			"2": optionalString(options.rtmr[2]),
			"3": optionalString(options.rtmr[3]),
		},
		DebugEnabled:  options.debugEnabled,
		TrusteeStatus: options.trusteeStatus,
		TrusteeDelay:  options.trusteeDelay,
	})
	if err != nil {
		return fmt.Errorf("configure mock Trustee: %w", err)
	}
	tlsConfig, err := loadServerTLS(options)
	if err != nil {
		return err
	}

	tcpListener, err := net.Listen("tcp", options.listen)
	if err != nil {
		return fmt.Errorf("listen for mock Trustee: %w", err)
	}
	defer tcpListener.Close()
	listener := tls.NewListener(tcpListener, tlsConfig)
	server := &http.Server{
		Handler:           handler.TrusteeHTTPHandler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	errorChannel := make(chan error, 1)
	go func() {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorChannel <- fmt.Errorf("serve mock Trustee: %w", err)
		}
	}()
	log.Printf("mock Trustee listening on https://%s/v1/verify/tdx-node", options.listen)

	signalContext, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	select {
	case <-signalContext.Done():
	case err := <-errorChannel:
		return err
	}

	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownContext); err != nil {
		return fmt.Errorf("shut down mock Trustee: %w", err)
	}
	return nil
}

func parseFlags() options {
	var result options
	flag.StringVar(&result.listen, "listen", "0.0.0.0:18443", "Trustee listen address")
	flag.StringVar(&result.serverCertPath, "tls-cert", "", "Trustee server certificate path")
	flag.StringVar(&result.serverKeyPath, "tls-key", "", "Trustee server private key path")
	flag.StringVar(&result.clientCAPath, "client-ca", "", "CA used to verify Trustee clients")
	flag.StringVar(&result.instanceID, "instance-id", "tdvm-v2-0001", "mock TD instance ID")
	flag.StringVar(&result.tcbStatus, "tcb-status", "up_to_date", "mock TCB status")
	flag.StringVar(&result.mrtd, "mrtd", "aabb", "mock MRTD")
	flag.StringVar(&result.rtmr[0], "rtmr-0", "0011", "mock RTMR 0, empty means null")
	flag.StringVar(&result.rtmr[1], "rtmr-1", "", "mock RTMR 1, empty means null")
	flag.StringVar(&result.rtmr[2], "rtmr-2", "", "mock RTMR 2, empty means null")
	flag.StringVar(&result.rtmr[3], "rtmr-3", "", "mock RTMR 3, empty means null")
	flag.BoolVar(&result.debugEnabled, "debug", false, "report a debug-enabled TD")
	flag.IntVar(&result.trusteeStatus, "trustee-status", 0, "force the Trustee HTTP status")
	flag.DurationVar(&result.trusteeDelay, "trustee-delay", 0, "delay Trustee responses")
	flag.Parse()
	return result
}

func loadServerTLS(options options) (*tls.Config, error) {
	if options.serverCertPath == "" || options.serverKeyPath == "" || options.clientCAPath == "" {
		return nil, fmt.Errorf("tls-cert, tls-key, and client-ca are required")
	}
	certificate, err := tls.LoadX509KeyPair(options.serverCertPath, options.serverKeyPath)
	if err != nil {
		return nil, fmt.Errorf("load Trustee server certificate: %w", err)
	}
	caPEM, err := os.ReadFile(options.clientCAPath)
	if err != nil {
		return nil, fmt.Errorf("read Trustee client CA: %w", err)
	}
	clientCAs := x509.NewCertPool()
	if !clientCAs.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("Trustee client CA contains no certificates")
	}
	return &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{certificate},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    clientCAs,
	}, nil
}

func optionalString(value string) *string {
	if value == "" {
		return nil
	}
	copy := value
	return &copy
}

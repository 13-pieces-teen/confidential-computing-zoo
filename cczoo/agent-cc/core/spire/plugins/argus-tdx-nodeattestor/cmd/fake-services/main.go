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
	evidenceListen   string
	trusteeListen    string
	serverCertPath   string
	serverKeyPath    string
	clientCAPath     string
	instanceID       string
	workloadID       string
	workloadPolicy   string
	workloadDecision string
	tcbStatus        string
	mrtd             string
	rtmr             [4]string
	debugEnabled     bool
	replayEvidence   bool
	evidenceStatus   int
	evidenceDelay    time.Duration
	trusteeStatus    int
	trusteeDelay     time.Duration
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	options := parseFlags()
	handler, err := fakeservices.NewHandler(fakeservices.Config{
		InstanceID:       options.instanceID,
		TCBStatus:        options.tcbStatus,
		MRTD:             options.mrtd,
		WorkloadID:       options.workloadID,
		WorkloadPolicyID: options.workloadPolicy,
		WorkloadDecision: options.workloadDecision,
		RTMR: map[string]*string{
			"0": optionalString(options.rtmr[0]),
			"1": optionalString(options.rtmr[1]),
			"2": optionalString(options.rtmr[2]),
			"3": optionalString(options.rtmr[3]),
		},
		DebugEnabled:   options.debugEnabled,
		ReplayEvidence: options.replayEvidence,
		EvidenceStatus: options.evidenceStatus,
		TrusteeStatus:  options.trusteeStatus,
		EvidenceDelay:  options.evidenceDelay,
		TrusteeDelay:   options.trusteeDelay,
	})
	if err != nil {
		return fmt.Errorf("configure fake services: %w", err)
	}
	tlsConfig, err := loadServerTLS(options)
	if err != nil {
		return err
	}
	evidenceListener, err := net.Listen("tcp", options.evidenceListen)
	if err != nil {
		return fmt.Errorf("listen for Evidence Provider: %w", err)
	}
	defer evidenceListener.Close()
	trusteeTCPListener, err := net.Listen("tcp", options.trusteeListen)
	if err != nil {
		return fmt.Errorf("listen for Trustee: %w", err)
	}
	defer trusteeTCPListener.Close()
	trusteeListener := tls.NewListener(trusteeTCPListener, tlsConfig)

	evidenceServer := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	trusteeServer := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	errorChannel := make(chan error, 2)
	go serve(errorChannel, "Evidence Provider", evidenceServer, evidenceListener)
	go serve(errorChannel, "Trustee", trusteeServer, trusteeListener)
	log.Printf("fake Evidence Provider listening on http://%s/ra/v1/evidence", options.evidenceListen)
	log.Printf("fake Trustee listening on https://%s/v1/verify/tdx-node", options.trusteeListen)

	signalContext, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	select {
	case <-signalContext.Done():
	case err := <-errorChannel:
		return err
	}
	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := evidenceServer.Shutdown(shutdownContext); err != nil {
		return fmt.Errorf("shut down Evidence Provider: %w", err)
	}
	if err := trusteeServer.Shutdown(shutdownContext); err != nil {
		return fmt.Errorf("shut down Trustee: %w", err)
	}
	return nil
}

func parseFlags() options {
	var result options
	flag.StringVar(&result.evidenceListen, "evidence-listen", "127.0.0.1:18080", "Evidence Provider listen address")
	flag.StringVar(&result.trusteeListen, "trustee-listen", "127.0.0.1:18443", "Trustee listen address")
	flag.StringVar(&result.serverCertPath, "tls-cert", "", "Trustee server certificate path")
	flag.StringVar(&result.serverKeyPath, "tls-key", "", "Trustee server private key path")
	flag.StringVar(&result.clientCAPath, "client-ca", "", "CA used to verify Trustee clients")
	flag.StringVar(&result.instanceID, "instance-id", "tdvm-m3-0001", "verified TD instance ID")
	flag.StringVar(&result.workloadID, "workload-id", "openviking-cmem", "verified workload ID")
	flag.StringVar(&result.workloadPolicy, "workload-policy-id", "openviking-cmem-v1", "verified workload policy ID")
	flag.StringVar(&result.workloadDecision, "workload-decision", "allow", "workload Trustee decision: allow or deny")
	flag.StringVar(&result.tcbStatus, "tcb-status", "up_to_date", "verified TCB status")
	flag.StringVar(&result.mrtd, "mrtd", "aabb", "verified MRTD")
	flag.StringVar(&result.rtmr[0], "rtmr-0", "0011", "verified RTMR 0, empty means null")
	flag.StringVar(&result.rtmr[1], "rtmr-1", "", "verified RTMR 1, empty means null")
	flag.StringVar(&result.rtmr[2], "rtmr-2", "", "verified RTMR 2, empty means null")
	flag.StringVar(&result.rtmr[3], "rtmr-3", "", "verified RTMR 3, empty means null")
	flag.BoolVar(&result.debugEnabled, "debug", false, "report a debug-enabled TD")
	flag.BoolVar(&result.replayEvidence, "replay-evidence", false, "replay the first evidence response for later requests")
	flag.IntVar(&result.evidenceStatus, "evidence-status", 0, "force the Evidence Provider HTTP status")
	flag.DurationVar(&result.evidenceDelay, "evidence-delay", 0, "delay Evidence Provider responses")
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
		MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{certificate},
		ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: clientCAs,
	}, nil
}

func serve(errorChannel chan<- error, name string, server *http.Server, listener net.Listener) {
	if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
		errorChannel <- fmt.Errorf("serve %s: %w", name, err)
	}
}

func optionalString(value string) *string {
	if value == "" {
		return nil
	}
	copy := value
	return &copy
}

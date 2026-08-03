package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/fakeservices"
)

type options struct {
	listen         string
	instanceID     string
	tcbStatus      string
	mrtd           string
	rtmr           [4]string
	debugEnabled   bool
	replayEvidence bool
	evidenceStatus int
	evidenceDelay  time.Duration
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
		DebugEnabled:   options.debugEnabled,
		ReplayEvidence: options.replayEvidence,
		EvidenceStatus: options.evidenceStatus,
		EvidenceDelay:  options.evidenceDelay,
	})
	if err != nil {
		return fmt.Errorf("configure mock Evidence Provider: %w", err)
	}

	server := &http.Server{
		Addr:              options.listen,
		Handler:           handler.EvidenceHTTPHandler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	errorChannel := make(chan error, 1)
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorChannel <- fmt.Errorf("serve mock Evidence Provider: %w", err)
		}
	}()
	log.Printf("mock Evidence Provider listening on http://%s/ra/v1/evidence", options.listen)

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
		return fmt.Errorf("shut down mock Evidence Provider: %w", err)
	}
	return nil
}

func parseFlags() options {
	var result options
	flag.StringVar(&result.listen, "listen", "127.0.0.1:18080", "Evidence Provider listen address")
	flag.StringVar(&result.instanceID, "instance-id", "tdvm-v2-0001", "mock TD instance ID")
	flag.StringVar(&result.tcbStatus, "tcb-status", "up_to_date", "mock TCB status")
	flag.StringVar(&result.mrtd, "mrtd", "aabb", "mock MRTD")
	flag.StringVar(&result.rtmr[0], "rtmr-0", "0011", "mock RTMR 0, empty means null")
	flag.StringVar(&result.rtmr[1], "rtmr-1", "", "mock RTMR 1, empty means null")
	flag.StringVar(&result.rtmr[2], "rtmr-2", "", "mock RTMR 2, empty means null")
	flag.StringVar(&result.rtmr[3], "rtmr-3", "", "mock RTMR 3, empty means null")
	flag.BoolVar(&result.debugEnabled, "debug", false, "report a debug-enabled TD")
	flag.BoolVar(&result.replayEvidence, "replay-evidence", false, "replay the first evidence response")
	flag.IntVar(&result.evidenceStatus, "evidence-status", 0, "force the Evidence Provider HTTP status")
	flag.DurationVar(&result.evidenceDelay, "evidence-delay", 0, "delay Evidence Provider responses")
	flag.Parse()
	return result
}

func optionalString(value string) *string {
	if value == "" {
		return nil
	}
	copy := value
	return &copy
}

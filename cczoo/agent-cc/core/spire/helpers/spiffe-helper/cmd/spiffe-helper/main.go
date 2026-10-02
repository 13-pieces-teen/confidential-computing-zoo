package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/sirupsen/logrus"
	"github.com/spiffe/spiffe-helper/cmd/spiffe-helper/config"
	"github.com/spiffe/spiffe-helper/pkg/broker"
	"github.com/spiffe/spiffe-helper/pkg/health"
	"github.com/spiffe/spiffe-helper/pkg/sidecar"
	"github.com/spiffe/spiffe-helper/pkg/util"
	"github.com/spiffe/spiffe-helper/pkg/version"
)

const (
	daemonModeFlagName = "daemon-mode"
)

func main() {
	versionFlag := flag.Bool("version", false, "print version")
	configFile := flag.String("config", "helper.conf", "<configFile> Configuration file path")
	daemonModeFlag := flag.Bool(daemonModeFlagName, true, "Toggle running as a daemon to rotate X.509/JWT or just fetch and exit")
	probeBroker := flag.Bool("probe-broker", false, "observe one separate Broker subscription without publishing credentials or running hooks")
	probeRunID := flag.String("probe-run-id", "", "experiment run ID for --probe-broker")
	probeTimeout := flag.Duration("probe-timeout", 30*time.Second, "bounded one-subscription probe timeout (at most 5m)")
	flag.Parse()

	if *versionFlag {
		fmt.Println(version.Version())
		os.Exit(0)
	}

	log := logrus.WithField("system", "spiffe-helper")

	log.Infof("Using configuration file: %q", *configFile)
	hclConfig, err := config.ParseConfig(*configFile, *daemonModeFlag, daemonModeFlagName)
	if err != nil {
		log.WithError(err).Errorf("failed to parse configuration")
		os.Exit(1)
	}

	if err := hclConfig.ValidateConfig(log); err != nil {
		log.WithError(err).Errorf("invalid configuration")
		os.Exit(1)
	}
	if *probeBroker {
		if hclConfig.Broker == nil {
			log.Error("--probe-broker requires Broker configuration")
			os.Exit(2)
		}
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		result := broker.Probe(ctx, hclConfig.AgentAddress, hclConfig.CertDir, *hclConfig.Broker, *probeRunID, *probeTimeout)
		if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
			os.Exit(2)
		}
		if result.Result != "OBSERVED" {
			os.Exit(2)
		}
		return
	}

	if err = startSidecar(hclConfig, log); err != nil {
		log.WithError(err).Errorf("Error starting spiffe-helper")
		os.Exit(1)
	}

	log.Infof("Exiting")
	os.Exit(0)
}

func startSidecar(hclConfig *config.Config, log logrus.FieldLogger) error {
	sidecarConfig := config.NewSidecarConfig(hclConfig, log)
	spiffeSidecar := sidecar.New(sidecarConfig)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if hclConfig.Broker != nil {
		return broker.Run(ctx, hclConfig.AgentAddress, hclConfig.CertDir, *hclConfig.Broker)
	}

	if !*hclConfig.DaemonMode {
		log.Info("Daemon mode disabled")
		return spiffeSidecar.Run(ctx)
	}

	log.Info("Launching daemon")
	tasks := []func(context.Context) error{
		spiffeSidecar.RunDaemon,
	}

	if hclConfig.HealthCheck.ListenerEnabled {
		healthServer := health.New(&hclConfig.HealthCheck, log, spiffeSidecar)
		tasks = append(tasks, healthServer.Start)
	}

	err := util.RunTasks(ctx, tasks...)
	if errors.Is(err, context.Canceled) {
		return nil
	}

	return err
}

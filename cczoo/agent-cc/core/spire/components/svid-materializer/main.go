package main

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/svid/x509svid"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
)

type options struct {
	socket     string
	expectedID spiffeid.ID
	outputDir  string
}

type status struct {
	SPIFFEID     string `json:"spiffe_id"`
	SerialNumber string `json:"serial_number"`
	NotBefore    int64  `json:"not_before_unix"`
	NotAfter     int64  `json:"not_after_unix"`
	UpdatedAt    int64  `json:"updated_at_unix"`
}

type materializer struct {
	options options
	fatal   chan<- error
	ready   chan struct{}
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	socket := flag.String("socket", "", "SPIFFE Workload API address")
	expectedIDValue := flag.String("spiffe-id", "", "exact workload SPIFFE ID to materialize")
	outputDir := flag.String("output-dir", "", "absolute credential output directory")
	flag.Parse()

	if *socket == "" || *expectedIDValue == "" || *outputDir == "" {
		return errors.New("-socket, -spiffe-id, and -output-dir are required")
	}
	expectedID, err := spiffeid.FromString(*expectedIDValue)
	if err != nil {
		return fmt.Errorf("parse expected SPIFFE ID: %w", err)
	}
	if !filepath.IsAbs(*outputDir) {
		return errors.New("-output-dir must be an absolute path")
	}
	if err := ensureOutputDirectory(*outputDir); err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	fatal := make(chan error, 1)
	ready := make(chan struct{})
	watcher := &materializer{
		options: options{socket: *socket, expectedID: expectedID, outputDir: *outputDir},
		fatal:   fatal,
		ready:   ready,
	}
	watchDone := make(chan error, 1)
	go func() {
		watchDone <- workloadapi.WatchX509Context(
			ctx,
			watcher,
			workloadapi.WithAddr(*socket),
		)
	}()

	select {
	case <-ready:
		log.Printf("materialized initial X509-SVID for %s in %s", expectedID, *outputDir)
	case err := <-fatal:
		return err
	case err := <-watchDone:
		if err == nil {
			err = errors.New("X.509 context watch stopped before the first update")
		}
		return err
	case <-ctx.Done():
		return nil
	}

	select {
	case err := <-fatal:
		return err
	case err := <-watchDone:
		if ctx.Err() != nil {
			return nil
		}
		if err == nil {
			err = errors.New("X.509 context watch stopped unexpectedly")
		}
		return err
	case <-ctx.Done():
		return nil
	}
}

func (watcher *materializer) OnX509ContextUpdate(x509Context *workloadapi.X509Context) {
	if err := materializeContext(watcher.options, x509Context); err != nil {
		select {
		case watcher.fatal <- err:
		default:
		}
		return
	}
	select {
	case <-watcher.ready:
	default:
		close(watcher.ready)
	}
}

func (watcher *materializer) OnX509ContextWatchError(err error) {
	log.Printf("SPIFFE Workload API watch error: %v", err)
}

func materializeContext(options options, x509Context *workloadapi.X509Context) error {
	if x509Context == nil || x509Context.Bundles == nil {
		return errors.New("Workload API returned an incomplete X.509 context")
	}
	svid, err := selectSVID(x509Context.SVIDs, options.expectedID)
	if err != nil {
		return err
	}
	if len(svid.Certificates) == 0 || svid.PrivateKey == nil {
		return errors.New("selected X509-SVID has no certificate chain or private key")
	}
	bundle, ok := x509Context.Bundles.Get(options.expectedID.TrustDomain())
	if !ok || bundle.Empty() {
		return fmt.Errorf("no X.509 bundle for trust domain %s", options.expectedID.TrustDomain())
	}

	certificatePEM := make([]byte, 0)
	for _, certificate := range svid.Certificates {
		certificatePEM = append(certificatePEM, pem.EncodeToMemory(&pem.Block{
			Type:  "CERTIFICATE",
			Bytes: certificate.Raw,
		})...)
	}
	privateKeyDER, err := x509.MarshalPKCS8PrivateKey(svid.PrivateKey)
	if err != nil {
		return fmt.Errorf("marshal X509-SVID private key: %w", err)
	}
	privateKeyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateKeyDER})
	bundlePEM, err := bundle.Marshal()
	if err != nil {
		return fmt.Errorf("marshal X.509 trust bundle: %w", err)
	}
	leaf := svid.Certificates[0]
	statusJSON, err := json.MarshalIndent(status{
		SPIFFEID:     svid.ID.String(),
		SerialNumber: leaf.SerialNumber.String(),
		NotBefore:    leaf.NotBefore.Unix(),
		NotAfter:     leaf.NotAfter.Unix(),
		UpdatedAt:    time.Now().Unix(),
	}, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal credential status: %w", err)
	}
	statusJSON = append(statusJSON, '\n')

	files := []struct {
		name string
		data []byte
		mode os.FileMode
	}{
		{name: "svid.pem", data: certificatePEM, mode: 0o644},
		{name: "svid-key.pem", data: privateKeyPEM, mode: 0o600},
		{name: "bundle.pem", data: bundlePEM, mode: 0o644},
		{name: "status.json", data: statusJSON, mode: 0o644},
	}
	for _, file := range files {
		if err := atomicWrite(options.outputDir, file.name, file.data, file.mode); err != nil {
			return err
		}
	}
	log.Printf(
		"updated X509-SVID id=%s serial=%s not_after=%d",
		svid.ID,
		leaf.SerialNumber,
		leaf.NotAfter.Unix(),
	)
	return nil
}

func selectSVID(svids []*x509svid.SVID, expectedID spiffeid.ID) (*x509svid.SVID, error) {
	for _, svid := range svids {
		if svid != nil && svid.ID == expectedID {
			return svid, nil
		}
	}
	return nil, fmt.Errorf("Workload API did not return expected SPIFFE ID %s", expectedID)
}

func ensureOutputDirectory(path string) error {
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		if err := os.MkdirAll(path, 0o700); err != nil {
			return fmt.Errorf("create credential output directory: %w", err)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("inspect credential output directory: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return errors.New("credential output path must be a real directory, not a symlink")
	}
	return os.Chmod(path, 0o700)
}

func atomicWrite(directory, name string, data []byte, mode os.FileMode) error {
	path := filepath.Join(directory, name)
	if info, err := os.Lstat(path); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("refusing to replace symlink %s", path)
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect output file %s: %w", path, err)
	}
	temporary, err := os.CreateTemp(directory, "."+name+"-*")
	if err != nil {
		return fmt.Errorf("create temporary output for %s: %w", name, err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(mode); err != nil {
		temporary.Close()
		return fmt.Errorf("set mode on temporary output for %s: %w", name, err)
	}
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return fmt.Errorf("write temporary output for %s: %w", name, err)
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return fmt.Errorf("sync temporary output for %s: %w", name, err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close temporary output for %s: %w", name, err)
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return fmt.Errorf("publish output %s: %w", name, err)
	}
	return nil
}

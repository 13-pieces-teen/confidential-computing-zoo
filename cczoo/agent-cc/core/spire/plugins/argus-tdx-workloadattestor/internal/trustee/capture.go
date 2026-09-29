package trustee

// Optional research capture. A small nonblocking queue separates filesystem
// failures from the admission decision. Missing records mean incomplete evidence.
import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
)

type captureJob struct {
	nonce string
	files map[string][]byte
}
type captureWriter struct {
	directory string
	jobs      chan captureJob
}

func newCaptureWriterFromEnv() *captureWriter {
	directory := os.Getenv("ARGUS_ADMISSION_EVIDENCE_DIR")
	if directory == "" {
		return nil
	}
	if !filepath.IsAbs(directory) {
		log.Print("admission capture disabled: directory must be absolute")
		return nil
	}
	writer := &captureWriter{directory: directory, jobs: make(chan captureJob, 8)}
	go func() {
		for job := range writer.jobs {
			if err := writer.write(job); err != nil {
				log.Print("admission capture incomplete: filesystem write failed")
			}
		}
	}()
	return writer
}

func (writer *captureWriter) enqueue(job captureJob) {
	select {
	case writer.jobs <- job:
	default:
		log.Print("admission capture incomplete: queue full")
	}
}

func (writer *captureWriter) write(job captureJob) error {
	if err := protocol.ValidateNonce(job.nonce); err != nil {
		return err
	}
	directory := filepath.Join(writer.directory, job.nonce)
	if err := os.MkdirAll(directory, 0700); err != nil {
		return err
	}
	for name, contents := range job.files {
		f, err := os.CreateTemp(directory, ".capture-")
		if err != nil {
			return err
		}
		temp := f.Name()
		_, err = f.Write(contents)
		if closeErr := f.Close(); err == nil {
			err = closeErr
		}
		if err == nil {
			err = os.Rename(temp, filepath.Join(directory, name))
		}
		if err != nil {
			_ = os.Remove(temp)
			return err
		}
	}
	return nil
}

func (writer *captureWriter) appraisal(input protocol.Evidence, request, ear []byte, status int, at time.Time, accepted bool) {
	evidence, err := json.Marshal(input)
	if err != nil {
		return
	}
	quote, err := base64.RawURLEncoding.Strict().DecodeString(input.Quote)
	if err != nil {
		return
	}
	files := map[string][]byte{"evidence.json": evidence, "request.json": append([]byte(nil), request...), "quote.bin": quote}
	if len(ear) > 0 {
		files["ear.jwt"] = append([]byte(nil), ear...)
	}
	hashes := map[string]string{}
	for name, contents := range files {
		sum := sha256.Sum256(contents)
		hashes[name] = hex.EncodeToString(sum[:])
	}
	outcome := "NOT_ACCEPTED"
	if accepted {
		outcome = "EAR_ACCEPTED"
	}
	metadata, _ := json.Marshal(map[string]any{"schema": "argus.admission-export.v1", "nonce": input.RuntimeData.Nonce,
		"captured_at_ms": at.UnixMilli(), "http_status": status, "outcome": outcome, "artifacts": hashes,
		"boundary": "workload_attestor_trustee_client", "fresh_admission": "NOT_ESTABLISHED_BY_EAR_ALONE"})
	files["capture.json"] = metadata
	writer.enqueue(captureJob{input.RuntimeData.Nonce, files})
}

// RecordLocalOutcome is called only after the same post-appraisal target check
// that already controls selector return. It does not add another target check.
func (client *Client) RecordLocalOutcome(input protocol.Evidence, matched bool) {
	if client.capture == nil {
		return
	}
	contents, err := json.Marshal(map[string]any{"schema": "argus.admission-local-check.v1", "nonce": input.RuntimeData.Nonce,
		"target": input.RuntimeData.Target, "matched": matched, "checked_at_ms": time.Now().UnixMilli()})
	if err != nil {
		log.Print(fmt.Errorf("admission local capture: %w", err))
		return
	}
	client.capture.enqueue(captureJob{input.RuntimeData.Nonce, map[string][]byte{"local-check.json": contents}})
}

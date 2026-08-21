package sidecar

import (
	"bytes"
	"crypto"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"sync"
	"sync/atomic"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
)

type Identity struct {
	Certificate tls.Certificate
	Roots       *x509.CertPool
}

type IdentityStore struct {
	targetSPIFFEID string
	current        atomic.Pointer[Identity]
	ready          chan struct{}
	readyOnce      sync.Once
}

func NewIdentityStore(targetSPIFFEID string) *IdentityStore {
	return &IdentityStore{targetSPIFFEID: targetSPIFFEID, ready: make(chan struct{})}
}

func (store *IdentityStore) ApplySnapshot(response *broker.SubscribeToX509SVIDResponse) (bool, error) {
	for _, svid := range response.GetSvids() {
		if svid.GetSpiffeId() != store.targetSPIFFEID {
			continue
		}
		identity, err := parseIdentity(svid, store.targetSPIFFEID)
		if err != nil {
			store.Clear()
			return false, err
		}
		store.current.Store(identity)
		store.readyOnce.Do(func() { close(store.ready) })
		return true, nil
	}
	store.Clear()
	return false, nil
}

func (store *IdentityStore) Current() *Identity {
	return store.current.Load()
}

func (store *IdentityStore) Ready() <-chan struct{} {
	return store.ready
}

func (store *IdentityStore) Clear() {
	store.current.Store(nil)
}

func (store *IdentityStore) ClientTLSConfig(expectedServerSPIFFEID string) *tls.Config {
	return &tls.Config{
		MinVersion:         tls.VersionTLS12,
		// SPIFFE authenticates the peer URI SAN below instead of a DNS SAN.
		InsecureSkipVerify: true,
		GetClientCertificate: func(*tls.CertificateRequestInfo) (*tls.Certificate, error) {
			identity := store.Current()
			if identity == nil {
				return nil, fmt.Errorf("OpenClaw workload identity is unavailable")
			}
			return &identity.Certificate, nil
		},
		VerifyConnection: func(state tls.ConnectionState) error {
			identity := store.Current()
			if identity == nil {
				return fmt.Errorf("OpenClaw workload identity is unavailable")
			}
			if len(state.PeerCertificates) == 0 {
				return fmt.Errorf("OpenViking presented no certificate")
			}
			intermediates := x509.NewCertPool()
			for _, certificate := range state.PeerCertificates[1:] {
				intermediates.AddCert(certificate)
			}
			if _, err := state.PeerCertificates[0].Verify(x509.VerifyOptions{
				Roots:         identity.Roots,
				Intermediates: intermediates,
				KeyUsages:     []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
			}); err != nil {
				return fmt.Errorf("verify OpenViking X.509-SVID chain: %w", err)
			}
			return authorizeSPIFFEID(state.PeerCertificates[0], expectedServerSPIFFEID)
		},
	}
}

func parseIdentity(svid *broker.X509SVID, expectedSPIFFEID string) (*Identity, error) {
	certificates, err := x509.ParseCertificates(svid.GetX509Svid())
	if err != nil {
		return nil, fmt.Errorf("parse OpenClaw X.509-SVID: %w", err)
	}
	if len(certificates) == 0 {
		return nil, fmt.Errorf("OpenClaw X.509-SVID contains no certificates")
	}
	if err := authorizeSPIFFEID(certificates[0], expectedSPIFFEID); err != nil {
		return nil, fmt.Errorf("OpenClaw X.509-SVID identity: %w", err)
	}
	privateKey, err := x509.ParsePKCS8PrivateKey(svid.GetX509SvidKey())
	if err != nil {
		return nil, fmt.Errorf("parse OpenClaw X.509-SVID private key: %w", err)
	}
	signer, ok := privateKey.(crypto.Signer)
	if !ok {
		return nil, fmt.Errorf("OpenClaw X.509-SVID private key is not a signer")
	}
	certificatePublicKey, err := x509.MarshalPKIXPublicKey(certificates[0].PublicKey)
	if err != nil {
		return nil, fmt.Errorf("marshal OpenClaw certificate public key: %w", err)
	}
	privatePublicKey, err := x509.MarshalPKIXPublicKey(signer.Public())
	if err != nil {
		return nil, fmt.Errorf("marshal OpenClaw private-key public key: %w", err)
	}
	if !bytes.Equal(certificatePublicKey, privatePublicKey) {
		return nil, fmt.Errorf("OpenClaw X.509-SVID certificate and private key do not match")
	}
	bundleCertificates, err := x509.ParseCertificates(svid.GetBundle())
	if err != nil {
		return nil, fmt.Errorf("parse OpenClaw trust bundle: %w", err)
	}
	if len(bundleCertificates) == 0 {
		return nil, fmt.Errorf("OpenClaw trust bundle contains no certificates")
	}
	roots := x509.NewCertPool()
	for _, certificate := range bundleCertificates {
		roots.AddCert(certificate)
	}
	chain := make([][]byte, 0, len(certificates))
	for _, certificate := range certificates {
		chain = append(chain, certificate.Raw)
	}
	return &Identity{
		Certificate: tls.Certificate{Certificate: chain, PrivateKey: privateKey, Leaf: certificates[0]},
		Roots:       roots,
	}, nil
}

func authorizeSPIFFEID(certificate *x509.Certificate, expectedSPIFFEID string) error {
	if len(certificate.URIs) == 1 && certificate.URIs[0].String() == expectedSPIFFEID {
		return nil
	}
	return fmt.Errorf("certificate must contain exactly SPIFFE ID %q", expectedSPIFFEID)
}

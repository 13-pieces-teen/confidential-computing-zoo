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
	ClientCAs   *x509.CertPool
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

func (store *IdentityStore) ServerTLSConfig(expectedClientSPIFFEID string) *tls.Config {
	return &tls.Config{
		MinVersion: tls.VersionTLS12,
		GetConfigForClient: func(*tls.ClientHelloInfo) (*tls.Config, error) {
			identity := store.Current()
			if identity == nil {
				return nil, fmt.Errorf("OpenViking workload identity is unavailable")
			}
			return &tls.Config{
				MinVersion:   tls.VersionTLS12,
				Certificates: []tls.Certificate{identity.Certificate},
				ClientAuth:   tls.RequireAndVerifyClientCert,
				ClientCAs:    identity.ClientCAs,
				NextProtos:   []string{"http/1.1"},
				VerifyConnection: func(state tls.ConnectionState) error {
					return authorizeClient(state.PeerCertificates, expectedClientSPIFFEID)
				},
			}, nil
		},
	}
}

func parseIdentity(svid *broker.X509SVID, expectedSPIFFEID string) (*Identity, error) {
	certificates, err := x509.ParseCertificates(svid.GetX509Svid())
	if err != nil {
		return nil, fmt.Errorf("parse target X.509-SVID: %w", err)
	}
	if len(certificates) == 0 {
		return nil, fmt.Errorf("target X.509-SVID contains no certificates")
	}
	if err := authorizeClient(certificates[:1], expectedSPIFFEID); err != nil {
		return nil, fmt.Errorf("target X.509-SVID identity: %w", err)
	}
	privateKey, err := x509.ParsePKCS8PrivateKey(svid.GetX509SvidKey())
	if err != nil {
		return nil, fmt.Errorf("parse target X.509-SVID private key: %w", err)
	}
	signer, ok := privateKey.(crypto.Signer)
	if !ok {
		return nil, fmt.Errorf("target X.509-SVID private key is not a signer")
	}
	certificatePublicKey, err := x509.MarshalPKIXPublicKey(certificates[0].PublicKey)
	if err != nil {
		return nil, fmt.Errorf("marshal target certificate public key: %w", err)
	}
	privatePublicKey, err := x509.MarshalPKIXPublicKey(signer.Public())
	if err != nil {
		return nil, fmt.Errorf("marshal target private-key public key: %w", err)
	}
	if !bytes.Equal(certificatePublicKey, privatePublicKey) {
		return nil, fmt.Errorf("target X.509-SVID certificate and private key do not match")
	}
	bundleCertificates, err := x509.ParseCertificates(svid.GetBundle())
	if err != nil {
		return nil, fmt.Errorf("parse target trust bundle: %w", err)
	}
	if len(bundleCertificates) == 0 {
		return nil, fmt.Errorf("target trust bundle contains no certificates")
	}
	clientCAs := x509.NewCertPool()
	for _, certificate := range bundleCertificates {
		clientCAs.AddCert(certificate)
	}
	chain := make([][]byte, 0, len(certificates))
	for _, certificate := range certificates {
		chain = append(chain, certificate.Raw)
	}
	return &Identity{
		Certificate: tls.Certificate{Certificate: chain, PrivateKey: privateKey, Leaf: certificates[0]},
		ClientCAs:   clientCAs,
	}, nil
}

func authorizeClient(certificates []*x509.Certificate, expectedSPIFFEID string) error {
	if len(certificates) == 0 {
		return fmt.Errorf("peer presented no certificate")
	}
	for _, uri := range certificates[0].URIs {
		if uri.String() == expectedSPIFFEID {
			return nil
		}
	}
	return fmt.Errorf("peer SPIFFE ID does not match %q", expectedSPIFFEID)
}

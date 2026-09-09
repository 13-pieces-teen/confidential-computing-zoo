// Generate disposable, real certificates for the Node TLS integration tests.
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/pem"
	"math/big"
	"net/url"
	"os"
	"path/filepath"
	"time"
)

func main() {
	out := os.Args[1]
	now := time.Now()
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Argus test CA"},
		NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
	if err != nil {
		panic(err)
	}
	write(out, "bundle.pem", "CERTIFICATE", caDER)
	for index, name := range []string{"client", "client-rotated", "server", "server-rotated", "wrong-server", "wrong-client", "multiple-uri", "bad-ku", "bad-eku", "expired", "short-server", "spoofed-san"} {
		id := "spiffe://argus.local/service/openviking-cmem"
		if name == "client" || name == "client-rotated" {
			id = "spiffe://argus.local/agent/openclaw"
		}
		if name == "wrong-server" || name == "wrong-client" {
			id = "spiffe://argus.local/service/wrong"
		}
		uri, _ := url.Parse(id)
		leaf := &x509.Certificate{SerialNumber: big.NewInt(int64(index + 10)), Subject: pkix.Name{CommonName: name},
			NotBefore: now.Add(-time.Minute), NotAfter: now.Add(30 * time.Minute), URIs: []*url.URL{uri},
			KeyUsage: x509.KeyUsageDigitalSignature, BasicConstraintsValid: true,
			ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}}
		if name == "multiple-uri" {
			leaf.URIs = append(leaf.URIs, uri)
		}
		if name == "bad-ku" {
			leaf.KeyUsage |= x509.KeyUsageCRLSign
		}
		if name == "bad-eku" {
			leaf.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		}
		if name == "expired" {
			leaf.NotAfter = now.Add(-time.Second)
		}
		if name == "short-server" {
			leaf.NotAfter = now.Add(5 * time.Second)
		}
		if name == "spoofed-san" {
			// A DNS name containing a textual URI is not a SPIFFE URI SAN.
			data, _ := asn1.Marshal([]asn1.RawValue{{Class: 2, Tag: 2, Bytes: []byte("example, URI:spiffe://argus.local/service/openviking-cmem")}})
			leaf.ExtraExtensions = []pkix.Extension{{Id: asn1.ObjectIdentifier{2, 5, 29, 17}, Value: data}}
		}
		leafKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		der, err := x509.CreateCertificate(rand.Reader, leaf, ca, &leafKey.PublicKey, key)
		if err != nil {
			panic(err)
		}
		pkcs8, _ := x509.MarshalPKCS8PrivateKey(leafKey)
		write(out, name+".pem", "CERTIFICATE", der)
		write(out, name+"-key.pem", "PRIVATE KEY", pkcs8)
	}
}

func write(dir, name, kind string, data []byte) {
	if err := os.WriteFile(filepath.Join(dir, name), pem.EncodeToMemory(&pem.Block{Type: kind, Bytes: data}), 0600); err != nil {
		panic(err)
	}
}

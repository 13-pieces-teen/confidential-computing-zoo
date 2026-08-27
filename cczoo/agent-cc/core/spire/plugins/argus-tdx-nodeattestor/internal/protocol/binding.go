package protocol

import (
	"crypto/sha256"
	"crypto/sha512"
	"encoding/binary"
	"encoding/hex"
	"fmt"
)

func NodeRuntimeData(nonce, proofPublicKey []byte) ([]byte, error) {
	if len(nonce) != NonceSize {
		return nil, fmt.Errorf("nonce must be %d bytes", NonceSize)
	}
	if len(proofPublicKey) != PublicKeySize {
		return nil, fmt.Errorf("proof public key must be %d bytes", PublicKeySize)
	}

	runtimeData := make([]byte, 0, 2+len(reportDataDomain)+2+len(FixedAgentSPIFFEID)+NonceSize+PublicKeySize)
	runtimeData = appendLP16(runtimeData, []byte(reportDataDomain))
	runtimeData = appendLP16(runtimeData, []byte(FixedAgentSPIFFEID))
	runtimeData = append(runtimeData, nonce...)
	runtimeData = append(runtimeData, proofPublicKey...)
	return runtimeData, nil
}

func ReportData(nonce, proofPublicKey []byte) ([64]byte, error) {
	var reportData [64]byte
	runtimeData, err := NodeRuntimeData(nonce, proofPublicKey)
	if err != nil {
		return reportData, err
	}
	digest := sha512.Sum384(runtimeData)
	copy(reportData[:48], digest[:])
	return reportData, nil
}

func TranscriptDigest(proofPublicKey, nonce []byte, expiresAtUnixMs uint64, tdxQuote []byte) ([64]byte, error) {
	var result [64]byte
	if len(proofPublicKey) != PublicKeySize {
		return result, fmt.Errorf("proof public key must be %d bytes", PublicKeySize)
	}
	if len(nonce) != NonceSize {
		return result, fmt.Errorf("nonce must be %d bytes", NonceSize)
	}
	if len(tdxQuote) == 0 {
		return result, fmt.Errorf("TDX Quote is required")
	}

	quoteDigest := sha256.Sum256(tdxQuote)
	transcript := make([]byte, 0, 2+len(transcriptDomain)+PublicKeySize+NonceSize+8+sha256.Size)
	transcript = appendLP16(transcript, []byte(transcriptDomain))
	transcript = append(transcript, proofPublicKey...)
	transcript = append(transcript, nonce...)
	expiresAt := make([]byte, 8)
	binary.BigEndian.PutUint64(expiresAt, expiresAtUnixMs)
	transcript = append(transcript, expiresAt...)
	transcript = append(transcript, quoteDigest[:]...)
	return sha512.Sum512(transcript), nil
}

func KeyID(proofPublicKey []byte) (string, error) {
	if len(proofPublicKey) != PublicKeySize {
		return "", fmt.Errorf("proof public key must be %d bytes", PublicKeySize)
	}
	digest := sha256.Sum256(proofPublicKey)
	return hex.EncodeToString(digest[:]), nil
}

func appendLP16(destination, value []byte) []byte {
	length := make([]byte, 2)
	binary.BigEndian.PutUint16(length, uint16(len(value)))
	destination = append(destination, length...)
	return append(destination, value...)
}

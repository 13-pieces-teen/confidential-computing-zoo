package protocol

import (
	"crypto/subtle"
	"encoding/base64"
)

func encodeBase64URL(value []byte) string {
	return base64.RawURLEncoding.EncodeToString(value)
}

// equalBytes compares session identifiers without leaking a matching prefix.
func equalBytes(left, right []byte) bool {
	if len(left) != len(right) {
		return false
	}
	return subtle.ConstantTimeCompare(left, right) == 1
}

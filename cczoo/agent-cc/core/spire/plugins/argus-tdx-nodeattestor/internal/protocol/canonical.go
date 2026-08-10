package protocol

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"

	"github.com/gowebpki/jcs"
)

func canonicalizeJSON(input []byte) ([]byte, error) {
	if !utf8.Valid(input) {
		return nil, fmt.Errorf("JSON is not valid UTF-8")
	}
	if err := inspectJSON(input); err != nil {
		return nil, err
	}
	canonical, err := jcs.Transform(input)
	if err != nil {
		return nil, fmt.Errorf("canonicalize JSON: %w", err)
	}
	return canonical, nil
}

func inspectJSON(input []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(input))
	decoder.UseNumber()
	if err := inspectJSONValue(decoder); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		if err == nil {
			return fmt.Errorf("JSON contains trailing data")
		}
		return fmt.Errorf("read JSON trailer: %w", err)
	}
	return nil
}

func inspectJSONValue(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return fmt.Errorf("read JSON token: %w", err)
	}

	switch value := token.(type) {
	case json.Delim:
		switch value {
		case '{':
			seen := make(map[string]struct{})
			for decoder.More() {
				keyToken, err := decoder.Token()
				if err != nil {
					return fmt.Errorf("read JSON object key: %w", err)
				}
				key, ok := keyToken.(string)
				if !ok {
					return fmt.Errorf("JSON object key is not a string")
				}
				if _, duplicate := seen[key]; duplicate {
					return fmt.Errorf("duplicate JSON object key %q", key)
				}
				seen[key] = struct{}{}
				if err := inspectJSONValue(decoder); err != nil {
					return err
				}
			}
			closing, err := decoder.Token()
			if err != nil || closing != json.Delim('}') {
				return fmt.Errorf("unterminated JSON object")
			}
		case '[':
			for decoder.More() {
				if err := inspectJSONValue(decoder); err != nil {
					return err
				}
			}
			closing, err := decoder.Token()
			if err != nil || closing != json.Delim(']') {
				return fmt.Errorf("unterminated JSON array")
			}
		default:
			return fmt.Errorf("unexpected JSON delimiter %q", value)
		}
	case json.Number:
		if strings.ContainsAny(value.String(), ".eE") {
			return fmt.Errorf("floating-point JSON numbers are not allowed")
		}
	}
	return nil
}

func CanonicalizeJSON(input []byte) ([]byte, error) {
	return canonicalizeJSON(input)
}

package broker

import (
	"fmt"
	"testing"
	"time"

	"github.com/hashicorp/hcl"
)

func TestStartupTimeoutConfiguration(t *testing.T) {
	for value, want := range map[string]time.Duration{"": 2 * time.Minute, "120s": 2 * time.Minute, "130s": 130 * time.Second} {
		t.Run(value, func(t *testing.T) {
			var config Config
			if err := hcl.Decode(&config, fmt.Sprintf("startup_timeout=%q", value)); err != nil {
				t.Fatal(err)
			}
			if len(config.UnusedKeyPositions) != 0 {
				t.Fatalf("startup_timeout was not recognized: %v", config.UnusedKeyPositions)
			}
			got, err := config.startupTimeout()
			if err != nil || got != want {
				t.Fatalf("got %v, %v; want %v", got, err, want)
			}
		})
	}
	for _, value := range []string{"0", "-1s", "301s", "invalid"} {
		if _, err := (Config{StartupTimeout: value}).startupTimeout(); err == nil {
			t.Errorf("accepted invalid startup timeout %q", value)
		}
	}
}

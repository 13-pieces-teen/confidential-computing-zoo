// Experimental entrypoint, built only by static_clients.py with its Go overlay.
package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/spiffe/spiffe-helper/pkg/clientcredentials"
)

func main() {
	config := flag.String("config", "", "isolated fleet credentials.json")
	cert := flag.String("cert", "", "dedicated static experiment client certificate")
	key := flag.String("key", "", "dedicated root-only private key")
	bundle := flag.String("bundle", "", "dedicated experiment CA bundle")
	pid := flag.Int("register-pid", 0, "register actual Gateway PID without contacting SPIRE")
	clear := flag.Bool("clear", false, "clear this publisher lease and generations")
	flag.Parse()
	c, err := clientcredentials.LoadConfig(*config)
	if err != nil {
		log.Fatal(err)
	}
	if !strings.Contains(c.TargetID, "/experiment/") || !strings.HasSuffix(c.TargetID, "/static") {
		log.Fatal("isolated static experiment identity required")
	}
	if *clear {
		if *pid != 0 {
			log.Fatal("clear/register are mutually exclusive")
		}
		if err = clientcredentials.Clear(c); err != nil {
			log.Fatal(err)
		}
		return
	}
	if *pid != 0 {
		if err = clientcredentials.Register(c, *pid); err != nil {
			log.Fatal(err)
		}
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err = clientcredentials.RunExperimentStatic(ctx, c, *cert, *key, *bundle); err != nil && ctx.Err() == nil {
		log.Fatal(err)
	}
}

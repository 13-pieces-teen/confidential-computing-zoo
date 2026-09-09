package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/spiffe/spiffe-helper/pkg/clientcredentials"
)

func main() {
	path := flag.String("config", "/etc/argus-openclaw/credentials.json", "root-owned configuration")
	pid := flag.Int("register-pid", 0, "register actual OpenClaw host PID and exit; never overwrites an existing registration")
	clear := flag.Bool("clear", false, "clear only this publisher's credential generations and lease")
	flag.Parse()
	c, err := clientcredentials.LoadConfig(*path)
	if err != nil {
		log.Fatal(err)
	}
	if *clear {
		if *pid != 0 {
			log.Fatal("clear and register-pid are mutually exclusive")
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
		log.Printf("registered OpenClaw PID %d", *pid)
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err = clientcredentials.Run(ctx, c); err != nil && ctx.Err() == nil {
		log.Fatal(err)
	}
}

package main

import (
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/workloadattestor"
	"github.com/spiffe/spire-plugin-sdk/pluginmain"
	workloadattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/workloadattestor/v1"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
)

func main() {
	// SPIRE owns and configures the plugin process. The plugin returns selectors;
	// SPIRE later matches them against registration entries and issues any
	// resulting SVID.
	plugin := workloadattestor.New(nil, nil)
	pluginmain.Serve(
		workloadattestorv1.WorkloadAttestorPluginServer(plugin),
		configv1.ConfigServiceServer(plugin),
	)
}

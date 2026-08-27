// Command agent serves the Agent-side Argus TDX NodeAttestor plugin to SPIRE.
package main

import (
	"github.com/spiffe/spire-plugin-sdk/pluginmain"
	nodeattestorapi "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/nodeattestor/v1"
	configapi "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/agent"
)

func main() {
	plugin := agent.New()
	pluginmain.Serve(
		nodeattestorapi.NodeAttestorPluginServer(plugin),
		configapi.ConfigServiceServer(plugin),
	)
}

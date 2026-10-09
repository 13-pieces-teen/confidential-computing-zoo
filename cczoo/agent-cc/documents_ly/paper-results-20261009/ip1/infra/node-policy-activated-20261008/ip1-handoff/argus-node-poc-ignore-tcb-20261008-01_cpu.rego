package policy

import rego.v1

not_before := "2026-10-08T01:00:00Z"
not_after := "2026-10-11T01:00:00Z"

default executables := 33
default hardware := 97
default configuration := 36
default file_system := 0
default instance_identity := 0
default runtime_opaque := 0
default storage_opaque := 0
default sourced_data := 0

trust_claims := {
	"executables": executables,
	"hardware": hardware,
	"configuration": configuration,
	"file-system": file_system,
	"instance-identity": instance_identity,
	"runtime-opaque": runtime_opaque,
	"storage-opaque": storage_opaque,
	"sourced-data": sourced_data,
}

extensions := []

executables := 3 if {
	tdx_evidence_allowed
}

hardware := 2 if {
	tdx_evidence_allowed
}

configuration := 2 if {
	tdx_evidence_allowed
}

tdx_evidence_allowed if {
	input.tdx
	time_window_valid

	input.tdx.quote.header.tee_type == "81000000"
	input.tdx.quote.header.vendor_id == "939a7233f79c4ca9940a0db3957f0607"
	input.tdx.td_attributes.debug == false
	input.tdx.collateral_expiration_status == "0"

	input.tdx.quote.body.mr_td == "81a3ac2d05c448a517c975fc2ffc1a83f2f99eb19176927329ae15848df00650135236cab85cece2ced656b07293f34f"
	input.tdx.quote.body.rtmr_0 == "354e1a21735feea391c0f8c080d6ca24f6389ae4e66b6ae77281fee60001589cf9be4abf2c826e6fc3c024e8a78361af"
	input.tdx.quote.body.rtmr_1 == "69ea76fd3d75e58705badc14e68ff752a25db315de98db76be0843eea2d9b4748e57214cb0991d322c5bc983bd2d702b"
	input.tdx.quote.body.xfam == "e71a060000000000"
}

time_window_valid if {
	now_ns := time.now_ns()
	now_ns >= time.parse_rfc3339_ns(not_before)
	now_ns < time.parse_rfc3339_ns(not_after)
}

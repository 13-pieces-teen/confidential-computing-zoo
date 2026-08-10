#!/usr/bin/env python3
"""Run OpenViking's native ASGI application behind an exact SPIFFE mTLS boundary."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import ssl
import threading
from pathlib import Path

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from openviking.server.app import create_app
from openviking.server.config import load_server_config
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.logger import configure_uvicorn_logging

from .identity import certificate_uri_sans, is_exact_spiffe_identity

LOGGER = logging.getLogger("argus.openviking.spiffe")


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


class RotatingServerContext:
    """Reload materialized SVID files for new TLS handshakes."""

    def __init__(self, credential_directory: Path):
        self._directory = credential_directory
        self._lock = threading.Lock()
        self._fingerprint: tuple[tuple[int, int], ...] | None = None
        self._context: ssl.SSLContext | None = None
        self._reload_if_changed(required=True)

    @property
    def context(self) -> ssl.SSLContext:
        assert self._context is not None
        return self._context

    def _paths(self) -> tuple[Path, Path, Path]:
        return (
            self._directory / "svid.pem",
            self._directory / "svid-key.pem",
            self._directory / "bundle.pem",
        )

    def _current_fingerprint(self) -> tuple[tuple[int, int], ...]:
        return tuple((path.stat().st_mtime_ns, path.stat().st_size) for path in self._paths())

    def _build_context(self) -> ssl.SSLContext:
        certificate, private_key, bundle = self._paths()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = False
        context.load_cert_chain(certfile=certificate, keyfile=private_key)
        context.load_verify_locations(cafile=bundle)
        context.set_alpn_protocols(["http/1.1"])
        context.set_servername_callback(self._servername_callback)
        return context

    def _reload_if_changed(self, *, required: bool = False) -> None:
        try:
            fingerprint = self._current_fingerprint()
            if fingerprint == self._fingerprint:
                return
            context = self._build_context()
        except Exception:
            if required:
                raise
            LOGGER.exception("failed to reload rotated SPIFFE TLS context; retaining current context")
            return
        self._fingerprint = fingerprint
        self._context = context
        LOGGER.info("loaded SPIFFE TLS context from %s", self._directory)

    def _servername_callback(
        self, ssl_object: ssl.SSLObject, _server_name: str | None, _initial: ssl.SSLContext
    ) -> None:
        with self._lock:
            self._reload_if_changed()
            ssl_object.context = self.context


class ExactSPIFFEH11Protocol(H11Protocol):
    """Reject a TLS connection before HTTP parsing unless its peer ID is exact."""

    def connection_made(self, transport: asyncio.Transport) -> None:
        expected = getattr(self.config, "argus_expected_client_spiffe_id", "")
        ssl_object = transport.get_extra_info("ssl_object")
        peer = None if ssl_object is None else ssl_object.getpeercert()
        identities = [] if not peer else certificate_uri_sans(peer)
        if not expected or not is_exact_spiffe_identity(identities, expected):
            LOGGER.warning(
                "rejected TLS peer before HTTP parsing expected=%s actual=%s remote=%s",
                expected,
                identities or ["none"],
                transport.get_extra_info("peername"),
            )
            super().connection_made(transport)
            transport.close()
            return
        LOGGER.info(
            "accepted SPIFFE TLS peer identity=%s remote=%s",
            expected,
            transport.get_extra_info("peername"),
        )
        super().connection_made(transport)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenViking native SPIFFE mTLS server")
    parser.add_argument("--config", default=os.environ.get("OPENVIKING_CONFIG_FILE"))
    parser.add_argument("--host", default=os.environ.get("ARGUS_OPENVIKING_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("ARGUS_OPENVIKING_MTLS_PORT", "1943"))
    )
    parser.add_argument(
        "--credential-dir",
        default=os.environ.get("ARGUS_SPIFFE_CREDENTIAL_DIR", "/run/argus-svid"),
    )
    parser.add_argument(
        "--expected-client-id",
        default=os.environ.get("ARGUS_EXPECTED_CLIENT_SPIFFE_ID"),
    )
    return parser.parse_args()


async def serve() -> None:
    arguments = parse_arguments()
    expected_client_id = arguments.expected_client_id or required_environment(
        "ARGUS_EXPECTED_CLIENT_SPIFFE_ID"
    )
    if not expected_client_id.startswith("spiffe://"):
        raise RuntimeError("ARGUS_EXPECTED_CLIENT_SPIFFE_ID must be a SPIFFE ID")
    if arguments.config:
        os.environ[OPENVIKING_CONFIG_ENV] = arguments.config

    server_config = load_server_config(arguments.config)
    OpenVikingConfigSingleton.initialize(config_path=arguments.config)
    server_config.host = arguments.host
    server_config.port = arguments.port
    server_config.workers = 1
    configure_uvicorn_logging()
    application = create_app(server_config)

    rotating_context = RotatingServerContext(Path(arguments.credential_dir))
    config = uvicorn.Config(
        application,
        host=arguments.host,
        port=arguments.port,
        http=ExactSPIFFEH11Protocol,
        ws="none",
        workers=1,
        timeout_keep_alive=int(os.environ.get("ARGUS_SPIFFE_KEEPALIVE_SECONDS", "15")),
        log_config=None,
        proxy_headers=False,
        server_header=False,
    )
    setattr(config, "argus_expected_client_spiffe_id", expected_client_id)
    config.load()
    config.ssl = rotating_context.context
    LOGGER.info(
        "OpenViking native SPIFFE mTLS server starting address=%s:%d expected_client=%s",
        arguments.host,
        arguments.port,
        expected_client_id,
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

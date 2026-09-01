"""Top-level entry point for the ``moa-gateway`` console script.

``pyproject.toml`` declares ``moa-gateway = "moa_gateway.__main__:main"``.
This module makes three things true:

1. ``moa-gateway`` (no arguments) boots the gateway server with the
   configured defaults — the zero-argument path used by the desktop shell
   and by service managers.
2. ``moa-gateway serve|chat|run-moa|models|...`` delegates to the full
   CLI in :mod:`moa_gateway.cli.main`, so the console script is a single
   unified entry point rather than a stub.
3. ``python -m moa_gateway`` behaves identically (this module is executed
   by the interpreter with ``__name__ == "__main__"``).

The server is launched through ``uvicorn`` in-process (no extra subprocess)
so signals, logging and exit codes propagate cleanly.
"""

from __future__ import annotations

import argparse
import sys


def _run_server(host: str | None, port: int | None, workers: int | None) -> int:
    """Boot the ASGI app via uvicorn, honoring settings defaults.

    Any of host/port/workers may be ``None`` — the corresponding value is
    then taken from the loaded settings (config.yaml / env overrides), which
    keeps ``moa-gateway`` consistent with ``moa serve``.
    """
    import uvicorn

    from .config import get_settings

    s = get_settings()
    uvicorn.run(
        "moa_gateway.server:app",
        host=host or s.server.host,
        port=port if port is not None else s.server.port,
        workers=workers if workers is not None else s.server.workers,
        log_level=s.server.log_level.lower(),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Fast paths that must not require the (heavy) CLI import.
    if argv and argv[0] in ("--version", "-V"):
        from . import __version__

        print(f"moa-gateway {__version__}")
        return 0

    # Bare invocation or explicit ``serve``: boot the server. ``serve`` is
    # accepted here directly (with server flags) so both spellings work:
    #   moa-gateway                     -> serve with settings defaults
    #   moa-gateway --port 9000         -> serve with overrides
    #   moa-gateway serve --port 9000   -> serve with overrides
    # Anything that is a known non-serve CLI subcommand is delegated to cli.main.
    _NON_SERVE_COMMANDS = {
        "chat", "run-moa", "models", "discover", "prompts",
        "mcp", "config", "params", "workflow", "setup", "ask",
    }
    if not argv or argv[0] not in _NON_SERVE_COMMANDS:
        parser = argparse.ArgumentParser(
            prog="moa-gateway",
            description="MoA Gateway Pro — commercial-grade multi-model AI gateway",
        )
        parser.add_argument("command", nargs="?", default="serve", help="serve (default)")
        parser.add_argument("--host", default=None, help="bind host (default: from settings)")
        parser.add_argument("--port", type=int, default=None, help="bind port (default: from settings)")
        parser.add_argument("--workers", type=int, default=None, help="worker count (default: from settings)")
        args = parser.parse_args(argv)
        return _run_server(args.host, args.port, args.workers)

    # Everything else: delegate to the full CLI (chat / run-moa / models /
    # discover / prompts / mcp / config / params / workflow / ask / ...).
    from .cli.main import main as cli_main

    return int(cli_main(argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

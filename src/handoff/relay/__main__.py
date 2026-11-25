"""Run the relay: python -m handoff.relay --domain example.com --port 9000

Put it behind a reverse proxy that holds a wildcard certificate for *.example.com (Caddy or nginx), and set --trust-proxy so the
phone's real address is used. For a local trial, `--domain localhost` works: open http://<pc-id>.localhost:9000 in a browser.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .registry import Registry
from .server import create_relay_app, settings_from_environment


def main() -> None:
    env = settings_from_environment()
    parser = argparse.ArgumentParser(prog="handoff.relay")
    parser.add_argument("--domain", default=env["domain"], help="the name phones' addresses end in (env RELAY_DOMAIN)")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (use 0.0.0.0 only behind a firewall or proxy)")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--db", default=str(env["database"]), help="where the list of PCs is kept (env RELAY_DB)")
    parser.add_argument("--trust-proxy", action="store_true", default=env["trust_proxy"], help="believe X-Forwarded-* from the reverse proxy in front")
    args = parser.parse_args()
    key = env["register_key"]  # a secret: environment only, never on the command line
    if not key:
        print("WARNING: RELAY_REGISTER_KEY is not set, so anyone who can reach this server can register a PC.")
    app = create_relay_app(args.domain, Registry(Path(args.db)), register_key=key, trust_proxy=args.trust_proxy)
    print(f"Relay for *.{args.domain} on http://{args.host}:{args.port}  (PCs known: {app.state.registry.count()})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()

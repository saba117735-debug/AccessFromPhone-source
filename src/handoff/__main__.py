"""Start the server: python -m handoff [--config path]

For the installed app (tray icon, no terminal) see handoff.desktop.
"""
from __future__ import annotations

import argparse
import sys
import time
import webbrowser

from .config import load_settings
from .connect import open_connect_page
from .runtime import start, wait_until_listening


def _print_qr(url: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:  # the QR code is a convenience, never a requirement
        print("(could not draw a QR code; type the link into your phone instead)")


def _open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:  # the printed address still works
        return False


def main() -> None:
    parser = argparse.ArgumentParser(prog="handoff")
    parser.add_argument("--config", help="path to config.yaml (default: ./config.yaml)")
    parser.add_argument("--reset-owner", action="store_true", help="forget the owner account and all paired phones, then exit (for a lost password and recovery code)")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    settings = load_settings(args.config)
    if args.reset_owner:
        from .accounts import AccountStore
        from .audit import AuditLog
        from .db import Database

        db = Database(settings.storage.database)
        removed = AccountStore(db).reset_everything()
        AuditLog(db).record("-", "owner_reset", phones_removed=removed)
        print(f"Done. The owner account and {removed} paired phone(s) were removed. Start the app and open the PC page to set up again.")
        return

    try:
        running = start(settings)
    except ImportError:
        sys.exit('server.https needs the "cryptography" package. Run:  .\\.venv\\Scripts\\pip install cryptography')
    app, server = running.app, settings.server
    print("\nAccessFromPhone is starting.")
    print(f"  Allowed folder(s): {', '.join(str(f) for f in settings.agent.allowed_folders)}")
    print(f"  {app.state.planner_note}")
    print(f"  Notifications: {app.state.notifier.describe()}")
    for note in running.notes:
        print(f"  {note}")
    accounts = app.state.accounts
    if accounts is not None:
        print(f"\n  This PC's page (set up, connect phones, manage them):\n\n    {running.admin_url}\n")
        if not accounts.owner_exists:
            print("  First time here: open that page and create your account.")
        if server.open_connect_page and _open_browser(running.admin_url):
            print("  (It has been opened in your browser.)")
        print("\n  Phones join by scanning the code on that page. There is no link to share or keep secret.")
    else:
        link = f"{server.link_base}/?token={app.state.token}"
        print(f"  Open on your phone (same Wi-Fi):\n\n    {link}\n")
        shown = open_connect_page(link, server.token_file.parent, server.https) if server.open_connect_page else None
        if shown:
            print(f"  A \"Connect your phone\" page opened on this PC with a large QR code. (Saved at {shown})")
        else:
            _print_qr(link)
        print("\nKeep this link private: it signs you in.")
    if server.https:
        print("\nThe phone link is encrypted. A phone warns once that the certificate is not from a")
        print("known company: choose Advanced, then Continue. After that, voice input can use the microphone.")
    else:
        print("\nVoice input needs a secure page. To use it, set server.https: true in config.yaml.")
    print("\nPress Ctrl+C to stop.\n")

    if not wait_until_listening(running):
        running.stop()
        sys.exit(f"Could not start: port {server.port} or {settings.accounts.admin_port} is already in use. Is AccessFromPhone already running?")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        running.stop()


if __name__ == "__main__":
    main()

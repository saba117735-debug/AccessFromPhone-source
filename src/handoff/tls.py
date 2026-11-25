"""A self-signed certificate for the phone link.

Browsers only allow the microphone (and so voice input) on a secure page. Turning on
`server.https` serves the page over https with a certificate made here. The phone shows a
one-time "not private" warning because nobody it trusts signed the certificate; after you
continue, the link is encrypted and the microphone works.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 365
RENEW_WITHIN_DAYS = 30


def hosts_for(public_url: str) -> list[str]:
    """Every name or address the phone may use to reach this PC."""
    hosts = ["localhost", "127.0.0.1"]
    named = urlparse(public_url).hostname
    if named:
        hosts.append(named)
    try:
        name = socket.gethostname()
        hosts += [name, name.split(".")[0].lower() + ".local"]  # the .local name works even when the address changes
    except OSError:
        pass
    return list(dict.fromkeys(h for h in hosts if h))


def _san(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def _still_good(cert_file: Path, hosts: list[str]) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(v) for v in names.get_values_for_type(x509.DNSName)}
        have |= {str(v) for v in names.get_values_for_type(x509.IPAddress)}
        left = cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False
    return left > dt.timedelta(days=RENEW_WITHIN_DAYS) and all(h in have for h in hosts)


def ensure_certificate(tls_dir: Path, hosts: list[str]) -> tuple[Path, Path]:
    """Return (certificate, key) files, making new ones if they are missing, expiring or for another address."""
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_file, key_file = tls_dir / "cert.pem", tls_dir / "key.pem"
    if cert_file.is_file() and key_file.is_file() and _still_good(cert_file, hosts):
        return cert_file, key_file

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AccessFromPhone")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName([_san(h) for h in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_file, key_file

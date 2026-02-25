"""Self-signed TLS certificate generation for localhost HTTPS."""

from __future__ import annotations

import datetime
import ipaddress
import logging
import stat
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

_CERT_VALIDITY_DAYS = 365
_RENEWAL_THRESHOLD_DAYS = 30


def _is_cert_valid(cert_path: Path, renewal_days: int = _RENEWAL_THRESHOLD_DAYS) -> bool:
    """Return True if *cert_path* exists and the certificate expires in more than *renewal_days*."""
    if not cert_path.exists():
        return False
    try:
        pem_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(pem_data)
        remaining = cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)
        return remaining > datetime.timedelta(days=renewal_days)
    except Exception:
        logger.warning("Could not parse existing certificate at %s — will regenerate", cert_path)
        return False


def _generate_certificate(cert_path: Path, key_path: Path) -> None:
    """Generate an ECDSA P-256 self-signed certificate with localhost SANs."""
    private_key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Anteroom Local")])
    now = datetime.datetime.now(datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=_CERT_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    x509.IPAddress(ipaddress.IPv6Address("::1")),
                ]
            ),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    key_path.parent.mkdir(parents=True, exist_ok=True)

    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 0o644

    logger.info("Generated self-signed TLS certificate at %s (valid %d days)", cert_path, _CERT_VALIDITY_DAYS)


def ensure_certificates(data_dir: Path) -> tuple[Path, Path]:
    """Return ``(cert_path, key_path)``, generating a new keypair when necessary.

    Certificates are stored in ``data_dir/tls/``. An existing certificate is
    reused if it is still valid for more than 30 days; otherwise a fresh one is
    generated automatically.
    """
    tls_dir = data_dir / "tls"
    cert_path = tls_dir / "cert.pem"
    key_path = tls_dir / "key.pem"

    if _is_cert_valid(cert_path) and key_path.exists():
        logger.debug("Reusing existing TLS certificate at %s", cert_path)
        return cert_path, key_path

    _generate_certificate(cert_path, key_path)
    return cert_path, key_path

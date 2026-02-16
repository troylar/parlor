"""Tests for self-signed TLS certificate generation."""

from __future__ import annotations

import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from anteroom.tls import _is_cert_valid, ensure_certificates


def test_ensure_certificates_creates_files(tmp_path):
    cert_path, key_path = ensure_certificates(tmp_path)

    assert cert_path.exists()
    assert key_path.exists()
    assert cert_path == tmp_path / "tls" / "cert.pem"
    assert key_path == tmp_path / "tls" / "key.pem"


def test_ensure_certificates_reuses_valid_cert(tmp_path):
    cert_path_1, key_path_1 = ensure_certificates(tmp_path)
    cert_bytes_1 = cert_path_1.read_bytes()

    cert_path_2, key_path_2 = ensure_certificates(tmp_path)
    cert_bytes_2 = cert_path_2.read_bytes()

    assert cert_bytes_1 == cert_bytes_2


def test_cert_has_correct_sans(tmp_path):
    cert_path, _ = ensure_certificates(tmp_path)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())

    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san.value.get_values_for_type(x509.DNSName)
    ip_addresses = san.value.get_values_for_type(x509.IPAddress)

    assert "localhost" in dns_names
    assert ipaddress.IPv4Address("127.0.0.1") in ip_addresses
    assert ipaddress.IPv6Address("::1") in ip_addresses


def test_key_file_permissions(tmp_path):
    _, key_path = ensure_certificates(tmp_path)
    mode = key_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_expired_cert_triggers_renewal(tmp_path):
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    cert_path = tls_dir / "cert.pem"
    key_path = tls_dir / "key.pem"

    private_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    expired_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Expired")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Expired")]))
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=400))
        .not_valid_after(now - datetime.timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    cert_path.write_bytes(expired_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    old_serial = expired_cert.serial_number

    new_cert_path, _ = ensure_certificates(tmp_path)
    new_cert = x509.load_pem_x509_certificate(new_cert_path.read_bytes())

    assert new_cert.serial_number != old_serial
    assert new_cert.not_valid_after_utc > now


def test_is_cert_valid_returns_false_for_missing_file(tmp_path):
    assert _is_cert_valid(tmp_path / "nonexistent.pem") is False

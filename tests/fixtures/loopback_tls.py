"""ローカル検収でのみ信頼する短命の CA とサーバー証明書。"""
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from ipaddress import IPv4Address
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


class LoopbackTLS:
    def __init__(self, root: Path):
        ca_key = ec.generate_private_key(ec.SECP256R1())
        key = ec.generate_private_key(ec.SECP256R1())
        issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Temporary monitor test CA")])
        now = datetime.now(timezone.utc)
        builder = (x509.CertificateBuilder().issuer_name(issuer)
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1)))
        ca = (builder.subject_name(issuer).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Monitor test server")])
        sans = x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("127.0.0.1")), x509.DNSName("localhost")])
        cert = (builder.subject_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .add_extension(sans, critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        self.cert, self.key, self.ca = (root / filename for filename in ("monitor.pem", "monitor.key", "ca.pem"))
        self.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        self.ca.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        self.key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.spki_pin = b64encode(sha256(spki).digest()).decode("ascii")

    def server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.cert, self.key)
        return context

    def client_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(self.ca))

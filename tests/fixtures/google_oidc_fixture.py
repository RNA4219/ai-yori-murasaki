"""Google の代役としてテストプロセス内だけで署名する鍵。"""
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt


class SyntheticGoogleKey:
    def __init__(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        kid = "local-fixture"
        self._signer = crypt.RSASigner(key, key_id=kid)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture.invalid")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .sign(key, hashes.SHA256())
        )
        self.certs = {kid: cert.public_bytes(serialization.Encoding.PEM).decode("ascii")}

    def sign(self, claims: dict) -> str:
        return jwt.encode(self._signer, claims).decode("ascii")

"""ローカルの合成マネージャーで、スモークCLIの接続先検証を確かめる。"""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
import os
from pathlib import Path
import ssl
import subprocess
import sys
from threading import Thread

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wazuh_live_smoke.py"


@pytest.fixture(scope="module")
def tls_lab(tmp_path_factory):
    pytest.importorskip("cryptography", reason="合成証明書の生成には cryptography が必要")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    lab = tmp_path_factory.mktemp("wazuh-tls")
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Temporary test CA")])
    base = (x509.CertificateBuilder().issuer_name(ca_name)
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1)))
    root = (base.subject_name(ca_name).serial_number(x509.random_serial_number()).public_key(ca_key.public_key())
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    identity = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local test manager")])
    leaf = (base.subject_name(identity).serial_number(x509.random_serial_number()).public_key(server_key.public_key())
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("127.0.0.1"))]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca, cert, key = lab / "ca.pem", lab / "manager.pem", lab / "manager.key"
    ca.write_bytes(root.public_bytes(serialization.Encoding.PEM))
    cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(cert, key)
    finally:
        key.unlink()
    return ca, context


@pytest.mark.parametrize(("host", "use_ca", "authorized"), [
    ("127.0.0.1", True, True),
    ("127.0.0.1", False, False),  # 標準の信頼ストアにはないCA
    ("localhost", True, False),   # 信頼された証明書のSANと異なる名前
])
def test_cli_sends_requests_only_to_verified_manager(tls_lab, host, use_ca, authorized):
    ca, context = tls_lab
    observed = []

    class Manager(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.command, self.path, self.headers["Authorization"].split()[0]))
            self.respond(b'{"data":{"token":"synthetic-test-token"}}')

        def do_GET(self):
            observed.append((self.command, self.path, self.headers["Authorization"].split()[0]))
            self.respond(b'{"data":{"affected_items":[]}}')

        def respond(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Manager) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            cmd = [sys.executable, str(SCRIPT), "--host", host, "--port", str(server.server_port)]
            if use_ca:
                cmd += ["--ca-file", str(ca)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                                    env=dict(os.environ, WAZUH_USER="fixture", WAZUH_PASS="fixture"))
        finally:
            server.shutdown()
            worker.join()
    if authorized:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "authenticate: pass" in result.stdout and "query_alerts: pass" in result.stdout
        assert observed == [
            ("POST", "/security/user/authenticate", "Basic"),
            ("GET", "/alerts?limit=5&pretty=true", "Bearer"),
        ]
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "authenticate: fail (TLS certificate or hostname verification failed)" in result.stdout
        assert observed == []

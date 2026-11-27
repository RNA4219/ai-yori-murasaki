"""ローカルモニターの TLS 設定。鍵や証明書の内容・パスを応答に含めない。"""
import os
from pathlib import Path
import ssl

from .jsonio import DataError


def server_context(cert: str | Path | None, key: str | Path | None, *,
                   allow_http_loopback: bool = False) -> ssl.SSLContext | None:
    cert = cert if cert is not None else os.environ.get("AYM_MONITOR_TLS_CERT")
    key = key if key is not None else os.environ.get("AYM_MONITOR_TLS_KEY")
    if allow_http_loopback:
        if cert is not None or key is not None:
            raise DataError("平文起動とTLS設定が併用されています", "monitor_tls_configuration")
        return None
    if not cert and not key:
        raise DataError("HTTPSの証明書と鍵が必要です", "monitor_tls_required")
    if not cert or not key:
        raise DataError("証明書と鍵を両方指定してください", "monitor_tls_configuration")
    try:
        if not Path(cert).is_file() or not Path(key).is_file():
            raise OSError("証明書または鍵を読み取れません")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # 暗号化された鍵でもコンソールへのパスワード入力を要求しない。
        context.load_cert_chain(cert, key, password=lambda: b"")
        return context
    except (OSError, ValueError):
        raise DataError("証明書と鍵を確認してください", "monitor_tls_configuration") from None

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import ssl
import sys
from urllib.request import Request, urlopen


@lru_cache(maxsize=1)
def build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()

    bundled_bundle = _bundled_ca_bundle()
    if bundled_bundle is not None:
        context.load_verify_locations(cafile=str(bundled_bundle))
        return context

    certifi_bundle = _load_certifi_bundle()
    if certifi_bundle:
        context.load_verify_locations(cafile=certifi_bundle)
        return context

    windows_bundle = _load_windows_root_store()
    if windows_bundle:
        context.load_verify_locations(cadata=windows_bundle)

    return context


def open_url(request: Request, *, timeout: int):
    return urlopen(request, timeout=timeout, context=build_ssl_context())


def _bundled_ca_bundle() -> Path | None:
    bundle = _resource_base_path() / "assets" / "cacert.pem"
    return bundle if bundle.exists() else None


def _load_certifi_bundle() -> str | None:
    try:
        import certifi
    except ImportError:
        return None
    try:
        return certifi.where()
    except Exception:
        return None


def _looks_like_missing_openssl_bundle() -> bool:
    verify_paths = ssl.get_default_verify_paths()
    cafile = verify_paths.cafile
    if not cafile:
        return True
    return not Path(cafile).exists()


def _resource_base_path() -> Path:
    bundled_base = getattr(sys, "_MEIPASS", None)
    if bundled_base:
        return Path(str(bundled_base))
    return Path(__file__).resolve().parent.parent


def _load_windows_root_store() -> str | None:
    if not hasattr(ssl, "enum_certificates"):
        return None

    pem_chunks: list[str] = []
    try:
        certificates = ssl.enum_certificates("ROOT")
    except Exception:
        return None

    for cert_bytes, encoding, _trust in certificates:
        if encoding != "x509_asn":
            continue
        try:
            pem_chunks.append(ssl.DER_cert_to_PEM_cert(cert_bytes))
        except Exception:
            continue

    if not pem_chunks:
        return None
    return "".join(pem_chunks)

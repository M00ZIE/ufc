"""Validação de URLs para domínios oficiais UFC (evento e imagens)."""

from __future__ import annotations

from urllib.parse import urlparse


def allowed_ufc_hostname(hostname: str) -> bool:
    """
    Hosts oficiais UFC (evita typosquat tipo evilufc.com).
    Aceita apex, www e subdomínios (ex.: cdn.ufc.com).
    """
    h = (hostname or "").lower().rstrip(".")
    if h in ("ufc.com", "www.ufc.com", "ufc.com.br", "www.ufc.com.br"):
        return True
    return h.endswith(".ufc.com") or h.endswith(".ufc.com.br")


def allowed_ufc_event_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        if p.scheme not in ("http", "https"):
            return False
        return allowed_ufc_hostname(p.hostname or "")
    except Exception:
        return False


def allowed_ufc_image_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        if p.scheme not in ("http", "https"):
            return False
        return allowed_ufc_hostname(p.hostname or "")
    except Exception:
        return False

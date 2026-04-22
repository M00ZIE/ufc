from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


class IptvError(Exception):
    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def mask_url_credentials(url: str) -> str:
    """
    Mascara credenciais comuns em querystring (username/password/user/pass/token/key).
    Mantém o host/caminho para o admin enxergar qual painel é.
    """
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u)
    except Exception:
        return ""
    q = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        lk = (k or "").lower()
        if lk in ("username", "user", "password", "pass", "token", "key", "api_key", "apikey"):
            q.append((k, "***"))
        else:
            q.append((k, v))
    nq = urlencode(q, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, nq, p.fragment))


def maybe_github_raw(url: str) -> str:
    """
    Converte URLs do GitHub no formato /blob/ para raw.githubusercontent.com.
    """
    try:
        u = (url or "").strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            return url
        if "github.com/" not in u:
            return url
        p = urlparse(u)
        host = (p.netloc or "").lower()
        if "github.com" not in host:
            return url
        parts = [x for x in (p.path or "").split("/") if x]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo = parts[0], parts[1]
            ref = parts[3]
            rest = "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rest}"
        return url
    except Exception:
        return url


def playlist_candidate_urls(url: str) -> list[str]:
    """
    Gera variações de URL para playlists M3U, com foco em links GitHub.
    """
    base = (url or "").strip()
    if not base:
        return []
    out: list[str] = []

    def _add(u: str) -> None:
        v = (u or "").strip()
        if v and v not in out:
            out.append(v)

    parsed = urlparse(base)
    host = (parsed.netloc or "").lower()
    path_parts = [x for x in (parsed.path or "").split("/") if x]
    converted = maybe_github_raw(base)
    # Preferir raw quando possível, mas manter fallbacks.
    _add(converted)
    if "github.com" in host and len(path_parts) >= 5 and path_parts[2] == "blob":
        owner, repo = path_parts[0], path_parts[1]
        ref = path_parts[3]
        rest = "/".join(path_parts[4:])
        _add(f"https://github.com/{owner}/{repo}/raw/{ref}/{rest}")
        sep = "&" if "?" in base else "?"
        _add(base + sep + "raw=1")
    _add(base)
    return out


def _is_ip_blocked(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except Exception:
        return True
    if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_multicast or addr.is_reserved:
        return True
    return False


def validate_public_http_url(url: str) -> None:
    """
    Anti-SSRF: aceita apenas http(s) e bloqueia hostnames que resolvem para IPs privados/localhost.
    """
    u = (url or "").strip()
    if not u:
        raise IptvError("URL vazia", 400)
    if not (u.startswith("http://") or u.startswith("https://")):
        raise IptvError("Apenas URLs http(s) são suportadas.", 400)
    try:
        p = urlparse(u)
    except Exception:
        raise IptvError("URL inválida.", 400)
    host = (p.hostname or "").strip()
    if not host:
        raise IptvError("Host inválido na URL.", 400)
    # bloqueios rápidos por nome (antes do DNS)
    lh = host.lower()
    if lh in ("localhost",) or lh.endswith(".localhost") or lh.endswith(".local"):
        raise IptvError("Host não permitido.", 400)
    if lh == "0.0.0.0" or lh == "127.0.0.1" or lh == "::1":
        raise IptvError("Host não permitido.", 400)

    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except Exception:
        raise IptvError("Falha ao resolver DNS do host.", 400)
    ips: set[str] = set()
    for fam, _, _, _, sockaddr in infos:
        if fam == socket.AF_INET:
            ips.add(sockaddr[0])
        elif fam == socket.AF_INET6:
            ips.add(sockaddr[0])
    if not ips:
        raise IptvError("Host não resolvido.", 400)
    if any(_is_ip_blocked(ip) for ip in ips):
        raise IptvError("Host não permitido (SSRF).", 400)


def fetch_with_redirect_limit(
    sess: requests.Session,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    max_redirects: int = 3,
    stream: bool = False,
) -> requests.Response:
    """
    requests com redirects controlados (evita seguir para IP interno).
    """
    u = url
    for _ in range(max_redirects + 1):
        validate_public_http_url(u)
        r = sess.request(
            method,
            u,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            stream=stream,
        )
        if r.is_redirect or r.is_permanent_redirect:
            loc = r.headers.get("Location") or ""
            if not loc:
                return r
            # location pode ser relativo
            u = requests.compat.urljoin(u, loc)
            continue
        return r
    raise IptvError("Muitos redirecionamentos.", 502)


@dataclass
class RateLimiter:
    per_ip_limit: int = 30
    window_seconds: int = 60
    _hits: dict[str, list[float]] = None  # type: ignore

    def __post_init__(self) -> None:
        if self._hits is None:
            self._hits = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        arr = self._hits.get(key) or []
        cut = now - float(self.window_seconds)
        arr = [t for t in arr if t >= cut]
        if len(arr) >= int(self.per_ip_limit):
            self._hits[key] = arr
            return False
        arr.append(now)
        self._hits[key] = arr
        return True


def _parse_extinf(line: str) -> dict[str, Any]:
    s = line.strip()
    attrs_part = s
    name_part = ""
    if "," in s:
        attrs_part, name_part = s.split(",", 1)
    attrs_part = attrs_part[len("#EXTINF:") :].strip()

    attrs: dict[str, str] = {}
    i = 0
    n = len(attrs_part)
    while i < n:
        while i < n and attrs_part[i].isspace():
            i += 1
        j = i
        while j < n and attrs_part[j] not in ("=", " ", "\t"):
            j += 1
        key = attrs_part[i:j].strip()
        i = j
        while i < n and attrs_part[i].isspace():
            i += 1
        if i >= n or attrs_part[i] != "=":
            while i < n and not attrs_part[i].isspace():
                i += 1
            continue
        i += 1
        while i < n and attrs_part[i].isspace():
            i += 1
        val = ""
        if i < n and attrs_part[i] == '"':
            i += 1
            start = i
            while i < n and attrs_part[i] != '"':
                i += 1
            val = attrs_part[start:i]
            if i < n and attrs_part[i] == '"':
                i += 1
        else:
            start = i
            while i < n and not attrs_part[i].isspace():
                i += 1
            val = attrs_part[start:i]
        if key:
            attrs[key] = val

    name = (name_part or "").strip()
    tvg_name = (attrs.get("tvg-name") or "").strip()
    if not name and tvg_name:
        name = tvg_name
    group = (attrs.get("group-title") or "").strip()
    return {
        "name": name or "Canal",
        "group": group,
        "tvg_id": (attrs.get("tvg-id") or "").strip(),
        "tvg_name": tvg_name,
        "tvg_logo": (attrs.get("tvg-logo") or "").strip(),
    }


def parse_m3u_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    channels: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            pending = _parse_extinf(line)
            continue
        if line.startswith("#"):
            continue
        if pending:
            ch = dict(pending)
            ch["url"] = line
            channels.append(ch)
            pending = None
    return channels


def looks_like_html(sample: str) -> bool:
    s = (sample or "").lower()
    if "<html" in s or "<!doctype html" in s:
        return True
    return False


class M3UCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str, ttl_seconds: int) -> dict[str, Any] | None:
        now = time.time()
        row = self._data.get(key)
        if not row:
            return None
        ts, val = row
        if ttl_seconds <= 0:
            return None
        if now - ts > float(ttl_seconds):
            self._data.pop(key, None)
            return None
        return val

    def set(self, key: str, val: dict[str, Any]) -> None:
        self._data[key] = (time.time(), val)


def fetch_m3u_parse_streaming(
    sess: requests.Session,
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_channels: int,
    headers: dict[str, str],
    max_redirects: int,
) -> dict[str, Any]:
    """
    Baixa e parseia em streaming.
    """
    r = fetch_with_redirect_limit(
        sess,
        url,
        method="GET",
        headers=headers,
        timeout=timeout,
        max_redirects=max_redirects,
        stream=True,
    )
    if r.status_code >= 400:
        raise IptvError(f"Falha ao baixar M3U (HTTP {r.status_code}).", 502)
    ct = (r.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()

    # Contagem de bytes aproximada (do payload) para proteger RAM/tempo.
    total = 0
    lines: list[str] = []
    # iter_lines faz split por \n e tenta decodificar; manter utf-8 com fallback.
    for bline in r.iter_lines(decode_unicode=False):
        if bline is None:
            continue
        total += len(bline) + 1
        if max_bytes > 0 and total > max_bytes:
            raise IptvError("Playlist muito grande (limite configurado).", 413)
        try:
            line = bline.decode("utf-8", errors="replace")
        except Exception:
            line = str(bline)
        lines.append(line)

    # tratar HTML (amostra do começo)
    sample = "\n".join(lines[:40])
    if ct in ("text/html", "application/xhtml+xml") or (looks_like_html(sample) and "#extm3u" not in sample.lower()):
        raise IptvError("A URL retornou HTML (parece uma página), não uma playlist M3U.", 422)

    chans = parse_m3u_lines(lines)
    if max_channels > 0 and len(chans) > max_channels:
        chans = chans[:max_channels]
    groups = sorted({(c.get("group") or "").strip() for c in chans if (c.get("group") or "").strip()})
    return {"content_type": ct, "channels": chans, "groups": groups}


def probe_url(
    sess: requests.Session,
    url: str,
    *,
    timeout_head: float,
    timeout_get: float,
    headers_base: dict[str, str],
    max_redirects: int,
) -> dict[str, Any]:
    def _norm_ct(ct: str | None) -> str:
        return ((ct or "").split(";", 1)[0]).strip().lower()

    # HEAD
    try:
        rh = fetch_with_redirect_limit(
            sess,
            url,
            method="HEAD",
            headers={k: v for k, v in (headers_base or {}).items() if k.lower() != "range"},
            timeout=timeout_head,
            max_redirects=max_redirects,
            stream=False,
        )
        ct = _norm_ct(rh.headers.get("Content-Type"))
        cl = rh.headers.get("Content-Length")
        return {
            "method": "HEAD",
            "status_code": rh.status_code,
            "final_url": rh.url,
            "content_type": ct,
            "content_length": int(cl) if cl and cl.isdigit() else None,
            "hint": "A resposta parece HTML (página), não um stream/manifest." if ct in ("text/html", "application/xhtml+xml") else None,
        }
    except Exception:
        pass

    # GET range
    rg = fetch_with_redirect_limit(
        sess,
        url,
        method="GET",
        headers=headers_base,
        timeout=timeout_get,
        max_redirects=max_redirects,
        stream=True,
    )
    ct = _norm_ct(rg.headers.get("Content-Type"))
    cl = rg.headers.get("Content-Length")
    chunk = b""
    try:
        chunk = next(rg.iter_content(chunk_size=2048), b"") or b""
    except Exception:
        chunk = b""
    finally:
        try:
            rg.close()
        except Exception:
            pass
    decoded = chunk.decode("utf-8", errors="replace")
    sample = ""
    if ct in ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl"):
        sample = decoded[:200]
    hint = None
    if ct in ("text/html", "application/xhtml+xml") or looks_like_html(decoded[:400]):
        hint = "A resposta parece HTML (página), não um stream/manifest."
    return {
        "method": "GET_RANGE",
        "status_code": rg.status_code,
        "final_url": rg.url,
        "content_type": ct,
        "content_length": int(cl) if cl and cl.isdigit() else None,
        "sample": sample,
        "hint": hint,
    }


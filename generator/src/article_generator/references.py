"""Fetch small public source excerpts; editorial judgment remains with the reviewer."""
from contextlib import contextmanager
import ipaddress
import json
import re
import signal
import socket
import threading
from html.parser import HTMLParser
from urllib.parse import urlsplit

from requests.certs import where
from urllib3 import HTTPSConnectionPool
from urllib3.exceptions import HTTPError

MAX_SOURCES = 5
MAX_BYTES = 200_000
MAX_EXCERPT = 2_500
TIMEOUT_SECONDS = 5


def _without_code_blocks(body: str) -> str:
    prose = []
    fence_char = None
    fence_length = 0
    for line in body.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        if fence_char:
            if re.fullmatch(
                rf" {{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*", candidate
            ):
                fence_char = None
            continue
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", candidate)
        if fence:
            fence_char = fence.group(1)[0]
            fence_length = len(fence.group(1))
        elif not re.match(r"^(?: {4,}|\t)", line):
            prose.append(line)
    return "".join(prose)


@contextmanager
def _wall_clock_deadline(seconds: float):
    sigalrm = getattr(signal, "SIGALRM", None)
    setitimer = getattr(signal, "setitimer", None)
    itimer_real = getattr(signal, "ITIMER_REAL", None)
    if (threading.current_thread() is not threading.main_thread() or sigalrm is None
            or not callable(setitimer) or itimer_real is None):
        raise ValueError("límite temporal no disponible en este contexto")

    def timeout_handler(signum, frame):
        raise TimeoutError("tiempo total de descarga agotado")

    previous_handler = signal.getsignal(sigalrm)
    signal.signal(sigalrm, timeout_handler)
    previous_timer = setitimer(itimer_real, seconds)
    try:
        yield
    finally:
        setitimer(itimer_real, 0)
        signal.signal(sigalrm, previous_handler)
        if previous_timer != (0.0, 0.0):
            setitimer(itimer_real, *previous_timer)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _source_urls(body: str) -> list[str]:
    # Sources in prose/reference definitions count; example URLs inside code do not.
    prose = _without_code_blocks(body)
    prose = re.sub(r"`+[^`]*`+", "", prose)
    urls = re.findall(r"https?://[^\s<>\"\)]+", prose)
    return list(dict.fromkeys(url.rstrip(".,;").partition("#")[0] for url in urls))


def _public_destination(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in {None, 443}):
        raise ValueError("solo HTTPS público sin credenciales, en puerto 443")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise ValueError("destino no público")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return parsed.hostname, addresses[0][4][0], target


def _excerpt(url: str) -> str:
    with _wall_clock_deadline(TIMEOUT_SECONDS):
        hostname, address, target = _public_destination(url)
        # Pin the validated address while preserving TLS SNI/certificate checks.
        # No credentials, environment proxies, redirects or retries on source requests.
        with HTTPSConnectionPool(address, server_hostname=hostname, assert_hostname=hostname,
                                 cert_reqs="CERT_REQUIRED", ca_certs=where()) as pool:
            response = pool.request(
                "GET", target, headers={"Host": hostname, "Accept-Encoding": "identity",
                                        "User-Agent": "Ctx-reference-review/1.0"},
                assert_same_host=False, redirect=False, retries=False,
                timeout=TIMEOUT_SECONDS, preload_content=False,
            )
            try:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                content_type = response.headers.get("Content-Type", "").lower()
                if not any(kind in content_type for kind in ("text/html", "text/plain")):
                    raise ValueError("formato no compatible; se lee HTML o texto")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("respuesta comprimida no admitida")
                chunks = bytearray()
                while len(chunks) < MAX_BYTES:
                    chunk = response.read1(
                        min(8192, MAX_BYTES - len(chunks)), decode_content=False
                    )
                    if not chunk:
                        break
                    chunks.extend(chunk)
                text = chunks.decode("utf-8", errors="replace")
                if "text/html" in content_type:
                    parser = _VisibleText()
                    parser.feed(text)
                    text = " ".join(parser.parts)
                excerpt = " ".join(text.split())[:MAX_EXCERPT]
                if not excerpt:
                    raise ValueError("sin texto legible")
                return excerpt
            finally:
                response.close()


def reference_context(body: str) -> str:
    urls = _source_urls(body)
    if not urls:
        return "No se han encontrado referencias HTTP en el texto."
    sources = []
    for url in urls[:MAX_SOURCES]:
        try:
            sources.append({"url": url, "extracto_parcial": _excerpt(url)})
        except (OSError, ValueError, HTTPError) as error:
            # An unavailable reference is not evidence that its claim is false.
            detail = str(error) if isinstance(error, ValueError) else "fallo de conexión o timeout"
            sources.append({"url": url, "estado": f"No verificable: {detail}"})
    result = json.dumps(sources, ensure_ascii=False)
    if len(urls) > MAX_SOURCES:
        result += f"\nReferencias omitidas por límite: {len(urls) - MAX_SOURCES}."
    return result

"""只连接校验后的公网地址；DNS 固定、逐跳重验、限时限量，不透传用户凭证。"""
import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from urllib.parse import urlsplit, urlunsplit, urljoin

MAX_BYTES = 2_000_000


def public_url(value):
    if len(value) > 2000 or any(ord(c) < 33 or c == '\\' for c in value):
        raise ValueError("网页地址格式不合法")
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError()
        if url.port not in (None, 80 if url.scheme == "http" else 443):
            raise ValueError()
        host = url.hostname.encode('idna').decode('ascii')
        if host.lower() in {'localhost', 'metadata.google.internal'} or host.endswith('.local'):
            raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError()
        except ValueError as exc:
            if ':' in host or host.replace('.', '').isdigit():
                raise ValueError("禁止访问非公网地址") from exc
        return urlunsplit((url.scheme, url.netloc, url.path or '/', url.query, ''))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("仅允许无凭据的公网HTTP/HTTPS默认端口地址") from exc


def resolve_public(host, port):
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("域名解析包含非公网地址，已阻止访问")
    return addresses[0]


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, ip, secure, timeout):
        super().__init__(host, 443 if secure else 80, timeout=timeout)
        self.ip, self.secure = ip, secure

    def connect(self):
        sock = socket.create_connection((self.ip, self.port), self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host) if self.secure else sock
        except Exception:
            sock.close()
            raise


def fetch_page(url, *, resolver=resolve_public, connection= PinnedConnection):
    started = time.monotonic()
    for _ in range(4):
        url = public_url(url)
        parsed = urlsplit(url)
        host = parsed.hostname.encode('idna').decode('ascii')
        ip = resolver(host, 443 if parsed.scheme == 'https' else 80)
        client = connection(host, ip, parsed.scheme == 'https', 8)
        try:
            client.request('GET', urlunsplit(('', '', parsed.path, parsed.query, '')), headers={
                'User-Agent': '01Writing/3.0 research-fetcher', 'Accept-Encoding': 'identity',
                'Accept': 'text/html,text/plain,application/pdf'})
            response = client.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                target = response.getheader('Location')
                if not target:
                    raise ValueError('网页跳转缺少目标')
                url = urljoin(url, target)
                continue
            if response.status != 200:
                raise ValueError(f'网页返回状态 {response.status}，不绕过访问限制')
            mime = response.getheader('Content-Type', '').split(';')[0].lower()
            if mime not in {'text/html', 'text/plain', 'text/markdown', 'application/pdf'}:
                raise ValueError('网页类型不支持')
            encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
            if encoding not in {'identity', 'gzip', 'deflate'}:
                raise ValueError('网页压缩格式不支持')
            decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15) if encoding != 'identity' else None
            length = response.getheader('Content-Length')
            if length and int(length) > MAX_BYTES:
                raise ValueError('网页超过2MB限制')
            body = bytearray()
            wire_size = 0
            while True:
                if time.monotonic() - started > 25:
                    raise ValueError('网页抓取超时')
                part = response.read1(32768)
                if not part:
                    break
                wire_size += len(part)
                if wire_size > MAX_BYTES:
                    raise ValueError('网页传输超过2MB限制')
                if decoder:
                    part = decoder.decompress(part, MAX_BYTES - len(body) + 1)
                body.extend(part)
                if len(body) > MAX_BYTES:
                    raise ValueError('网页超过2MB限制')
            if decoder and (not decoder.eof or decoder.unused_data):
                raise ValueError('网页压缩数据不完整或包含多段内容')
            return bytes(body), mime, url
        except (OSError, http.client.HTTPException, zlib.error) as exc:
            raise ValueError('网页连接失败或超时') from exc
        finally:
            client.close()
    raise ValueError('网页跳转次数过多')

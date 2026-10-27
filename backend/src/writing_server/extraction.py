"""文件与网页提取。PDF 在受限子进程中处理；不执行文档脚本或外链。"""
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile

MAX_TEXT = 100_000


class Article(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'nav', 'noscript', 'svg', 'iframe'}:
            self.hidden.append(tag)
        if tag in {'p', 'div', 'br', 'h1', 'h2', 'h3', 'li', 'tr'} and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
        if tag in {'p', 'div', 'h1', 'h2', 'h3', 'li', 'tr'} and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract(raw, name, mime=''):
    if len(raw) > 2_000_000:
        raise ValueError('文件不能超过2MB')
    if mime == 'application/pdf' or name.lower().endswith('.pdf'):
        if not raw.startswith(b'%PDF-'):
            raise ValueError('文件不是有效PDF')
        with tempfile.TemporaryDirectory(prefix='writing-pdf-') as directory:
            source, target = Path(directory)/'input.pdf', Path(directory)/'output.json'
            source.write_bytes(raw)
            try:
                result = subprocess.run([sys.executable, '-m', 'writing_server.pdf_extract', str(source), str(target)],
                                        timeout=20, capture_output=True)
                if result.returncode or not target.exists() or target.stat().st_size > 600_000:
                    raise ValueError('PDF无法解析、资源超限或为扫描件；请改用可提取文字的文件')
                return json.loads(target.read_text(encoding='utf-8'))
            except subprocess.TimeoutExpired as exc:
                raise ValueError('PDF解析超过20秒，已停止') from exc
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = raw.decode('gb18030')
        except UnicodeDecodeError as exc:
            raise ValueError('请使用UTF-8或GB18030文本文件') from exc
    if mime == 'text/html':
        parser = Article()
        parser.feed(text)
        text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    if not text.strip() or '\x00' in text or len(text) > MAX_TEXT:
        raise ValueError('资料为空、含二进制内容或超过100,000字符')
    return {'content': text.strip(), 'pages': []}

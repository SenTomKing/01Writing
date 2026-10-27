"""仅作为受限解析子进程入口。"""
import json
import sys
from pathlib import Path


def main():
    if sys.platform != 'win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512*1024**2, 512*1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_FSIZE, (600_000, 600_000))
    else:
        # 正式worker是Linux；避免在没有内存隔离的Windows进程解析不可信PDF。
        raise ValueError('PDF导入需要Linux容器worker')
    from pypdf import PdfReader
    reader = PdfReader(sys.argv[1], strict=True)
    if reader.is_encrypted or len(reader.pages) > 80:
        raise ValueError('加密PDF或超过80页')
    content, pages = '', []
    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or '').strip()
        if text:
            if content:
                content += '\n\n'
            pages.append({'page': number, 'offset': len(content)})
            content += text
        if len(content) > 100_000:
            raise ValueError('文字过长')
    if not content:
        raise ValueError('没有可提取文字')
    Path(sys.argv[2]).write_text(json.dumps({'content': content, 'pages': pages}, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()

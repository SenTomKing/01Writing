"""固定位置片段、字词匹配与可选向量排名融合；不把降级说成语义检索。"""
import hashlib
import math
import re
import time


def split_content(content, pages=(), *, max_chars=900, overlap=0):
    if not 200 <= max_chars <= 2000 or not 0 <= overlap < max_chars // 2:
        raise ValueError('分块长度须为200至2000字符，重叠须小于块长的一半')
    result, offset = [], 0
    pages = sorted(pages, key=lambda p: p['offset'])
    while offset < len(content):
        page_end = next((p['offset'] for p in pages if p['offset'] > offset), len(content))
        end = min(offset + max_chars, page_end, len(content))
        if end < min(page_end, len(content)):
            # 优先段落，其次句末；没有合适边界时才按长度截断。
            lower = offset + max_chars // 2
            boundary = content.rfind('\n', lower, end)
            if boundary >= lower:
                end = boundary + 1
            else:
                matches = list(re.finditer(r'[。！？!?；;]|\.(?=\s)', content[lower:end]))
                if matches:
                    end = lower + matches[-1].end()
        page = next((p['page'] for p in reversed(pages) if p['offset'] <= offset), None)
        result.append({'offset': offset, 'text': content[offset:end], 'page': page})
        # 页码边界不重叠；其余位置带少量前文，保留准确的原文偏移。
        offset = end if end >= min(page_end, len(content)) else max(offset + 1, end - overlap)
    return result


def valid_index(source):
    index = source.get('chunk_index') or {}
    if index.get('hash') != hashlib.sha256(source['content'].encode()).hexdigest():
        return {}
    return index


def embedding_id(settings):
    if not settings.embedding_key or not settings.embedding_model or not settings.embedding_url:
        return ''
    return hashlib.sha256((settings.embedding_url.rstrip('/')+'|'+settings.embedding_model).encode()).hexdigest()[:20]


def embed(settings, texts):
    from openai import OpenAI
    if not texts:
        return []
    with OpenAI(api_key=settings.embedding_key, base_url=settings.embedding_url, timeout=20, max_retries=0) as client:
        response = client.embeddings.create(model=settings.embedding_model, input=texts, encoding_format="float")
    if len(response.data) != len(texts):
        raise ValueError('向量服务返回数量错误')
    ordered = sorted(response.data, key=lambda row: row.index)
    if [row.index for row in ordered] != list(range(len(texts))):
        raise ValueError('向量索引不完整')
    vectors = [row.embedding for row in ordered]
    dim = len(vectors[0])
    if not 1 <= dim <= 4096 or any(len(v) != dim or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors):
        raise ValueError('向量维度或数值不合法')
    return vectors


def build_index(settings, content, pages=()):
    pieces = split_content(content, pages, overlap=120)
    started = time.monotonic()
    dimension = None
    fingerprint = hashlib.sha256(content.encode()).hexdigest()
    identity = embedding_id(settings)
    if identity:
        for offset in range(0, len(pieces), 32):
            if time.monotonic() - started > 100:
                raise ValueError('向量索引耗时过长，请拆分资料后重试')
            batch = pieces[offset:offset+32]
            vectors = embed(settings, [p['text'] for p in batch])
            for piece, vector in zip(batch, vectors, strict=True):
                dimension = dimension or len(vector)
                if len(vector) != dimension:
                    raise ValueError('向量服务在不同批次返回了不同维度')
                piece['vector'] = vector
    return {'version': 2, 'hash': fingerprint, 'embedding_id': identity, 'dimension': dimension,
            'splitter': {'max_chars': 900, 'overlap': 120, 'boundary': 'paragraph-sentence-page'}, 'chunks': pieces}


def cosine(a, b):
    if len(a) != len(b):
        return -1
    return sum(x*y for x,y in zip(a,b)) / (math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b)))


def retrieve(sources, query, settings=None):
    from .knowledge import terms, chunks
    pieces = list(chunks(sources))
    q = terms(query)
    lexical = sorted(((len(q & terms(p['title']+' '+p['text'])), p) for p in pieces), key=lambda x: (-x[0], x[1]['ref']))
    ranked = [p for score,p in lexical if score]
    scores = {p['ref']: 1/(60+i) for i,p in enumerate(ranked, 1)}
    mode, note = 'keyword', '关键词检索；未启用匹配的语义索引'
    identity = embedding_id(settings) if settings else ''
    vectors = {}
    for i,source in enumerate(sources, 1):
        index = valid_index(source)
        if identity and index.get('embedding_id') == identity:
            vectors.update({f"S{i}:{p['offset']}": p['vector'] for p in index.get('chunks', []) if p.get('vector')})
    if vectors:
        try:
            vector = embed(settings, [query])[0]
            if any(len(v) != len(vector) or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors.values()):
                raise ValueError('已存向量与查询向量不兼容，请重建索引')
            semantic = sorted(((cosine(vector,v), ref) for ref,v in vectors.items()), reverse=True)[:10]
            for i,(_,ref) in enumerate(semantic,1):
                scores[ref] = scores.get(ref,0) + 1/(60+i)
            mode, note = 'hybrid', '关键词与语义排名融合（RRF）'
        except Exception:
            note = '语义服务不可用，已降级为关键词检索'
    selected = sorted((p for p in pieces if p['ref'] in scores), key=lambda p: (-scores[p['ref']], p['ref']))[:5]
    return {'mode': mode, 'note': note, 'chunks': selected}

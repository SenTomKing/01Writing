"""搜索只返回线索，正文仍通过受限抓取取得；凭证只发给固定供应商。"""
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .network import public_url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 避免供应商跳转把认证头转发到其他域名。
        return None


def search_web(settings, query):
    if not settings.search_available:
        raise ValueError('未配置搜索服务，请在资料区导入网页链接')
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
        raise ValueError('搜索词必须为1至128个字符')
    provider = settings.effective_search_provider
    if provider == 'deepseek':
        endpoint = 'https://api.deepseek.com/v1/tools/search'
        key = settings.api_key
        params = {'query': query.strip(), 'count': 5, 'need_content': False}
    else:
        endpoint = 'https://api.tavily.com/search'
        key = settings.search_key
        params = {'query': query.strip(), 'search_depth': 'basic', 'max_results': 5,
                  'include_answer': False, 'include_raw_content': False}
    request = Request(endpoint, data=json.dumps(params).encode(), headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(250_001)
        if len(raw) > 250_000:
            raise ValueError()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            raise ValueError()
        results, seen = [], set()
        for row in payload['results'][:5]:
            if not isinstance(row, dict):
                continue
            try:
                url = public_url(row['url'])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if url in seen:
                continue
            seen.add(url)
            summary = row.get('snippet') if provider == 'deepseek' else row.get('content')
            results.append({'url': url, 'title': str(row.get('title') or '')[:200],
                            'snippet': str(summary or '')[:500]})
        return results
    except Exception:
        # 不向网页、任务错误或日志返回供应商响应及认证细节。
        raise ValueError('搜索服务暂不可用；请检查服务端配置或改用手动导入网址') from None

"""搜索只返回候选网址；摘要不作为正文证据，需另行读取网页。"""
import json
from urllib.request import Request, urlopen
from .network import public_url


def search_web(settings, query):
    if not settings.search_key:
        raise ValueError('未配置搜索服务，请在资料区导入网页链接')
    body=json.dumps({'query':query,'search_depth':'basic','max_results':5,'include_answer':False,'include_raw_content':False}).encode()
    req=Request('https://api.tavily.com/search',data=body,headers={
        'Authorization':'Bearer '+settings.search_key,'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(req,timeout=12) as response:
            raw=response.read(250_001)
        if len(raw)>250_000:
            raise ValueError()
        payload=json.loads(raw)
        results=[]
        for row in payload.get('results',[])[:5]:
            try:
                url=public_url(row['url'])
            except (ValueError,KeyError):
                continue
            results.append({'url':url,'title':str(row.get('title',''))[:200],
                            'snippet':str(row.get('content',''))[:500]})
        return results
    except Exception as exc:
        raise ValueError('搜索服务暂不可用；请检查服务端配置或改用手动导入网址') from exc

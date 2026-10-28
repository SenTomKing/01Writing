"""从真实实验的逐题结果生成中文报告和 CSV；不调用模型。"""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/src'))
sys.path.insert(0,str(ROOT))
from evals.retrieval.core import SPLITTERS, split_documents
from evals.retrieval.run import BASELINE, percentile


def percent(value):
    return f'{value*100:.2f}%'


def name(key):
    split,model,method=key.split('/')
    splits={'fixed300':'固定300字／无重叠','boundary300_60':'边界300字／重叠60',
            'boundary600_90':'边界600字／重叠90','boundary900_0':'边界900字／无重叠',
            'boundary900_120':'边界900字／重叠120'}
    models={'none':'','bge-m3':'BGE-M3＋','qwen3-plain':'Qwen原始查询＋','qwen3-instruct':'Qwen查询指令＋'}
    methods={'keyword':'关键词','bm25':'BM25','dense':'纯向量','rrf_keyword':'关键词混合','rrf_bm25':'BM25混合'}
    return splits[split]+'；'+models[model]+methods[method]


def table(r,keys,split='test'):
    lines=['| 方案 | 证据召回@1 | 证据召回@5 | 1500字内召回 | 完整取证题比例@5 | 平均正文字符 |',
           '|---|---:|---:|---:|---:|---:|']
    for key in keys:
        s=r['summaries'][key][split]
        lines.append(f"| {name(key)} | {percent(s['evidence_recall@1'])} | {percent(s['evidence_recall@5'])} | {percent(s['evidence_recall_budget1500'])} | {percent(s['all_evidence@5'])} | {s['context_chars']:.1f} |")
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'evals/results/retrieval-v1.json')
    parser.add_argument('--output',type=Path,default=ROOT/'evals/检索切分与向量模型对照实验.md')
    args=parser.parse_args()
    r=json.loads(args.input.read_text(encoding='utf-8'))
    data=json.loads(Path(__file__).with_name('dataset.json').read_text(encoding='utf-8'))
    summaries=r['summaries']; selected=r['selected_on_dev']
    output=args.input.with_suffix('.csv')
    flat=[]
    for config,splits in summaries.items():
        for split,metrics in splits.items():flat.append({'config':config,'split':split,**metrics})
    with output.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
    usage=defaultdict(list)
    for row in r['api_requests']:usage[row['model']].append(row)
    usage_table=['| 模型 | 请求数 | 实际总token | 发送UTF-8字节 | 返回维度 |','|---|---:|---:|---:|---|']
    for model,rows in usage.items():
        usage_table.append(f"| {model} | {len(rows)} | {sum((x.get('usage') or {}).get('total_tokens',0) for x in rows)} | {sum(x['input_bytes'] for x in rows)} | {sorted({x.get('dimension') for x in rows if x.get('dimension')})} |")
    latency=['| 查询方式 | 独立单条请求样本数 | 请求P50毫秒 | 请求P95毫秒 |','|---|---:|---:|---:|']
    for variant in ('bge-m3','qwen3-plain','qwen3-instruct'):
        values=[x['elapsed_ms'] for x in r['api_requests'] if x['purpose']=='single_query:'+variant and x['status']=='ok']
        if values:latency.append(f'| {variant} | {len(values)} | {percentile(values,.5):.1f} | {percentile(values,.95):.1f} |')
    chunk_table=['| 切分配置 | 全部16篇块数 | 索引正文字符总量（含重叠） |','|---|---:|---:|']
    for key,cfg in SPLITTERS.items():
        groups=split_documents(data['documents'],cfg)
        pieces=[p for group in groups.values() for p in group]
        chunk_table.append(f"| {key} | {len(pieces)} | {sum(len(p['text']) for p in pieces)} |")
    categories=['| 方案 | 题型 | 测试题数 | 证据召回@5 | 1500字内召回 |','|---|---|---:|---:|---:|']
    for config in (BASELINE,selected):
        for category in ('精确事实','同义表达','多处证据'):
            rows=[x for x in r['rows'] if x['split']=='test' and x['config']==config and x['category']==category]
            categories.append(f"| {name(config)} | {category} | {len(rows)} | {percent(statistics.mean(x['metrics']['evidence_recall@5'] for x in rows))} | {percent(statistics.mean(x['metrics']['evidence_recall_budget1500'] for x in rows))} |")
    # 公开逐题失败记录，保留正确证据以及被返回片段的原文位置，便于独立核查。
    questions={q['id']:q for q in data['queries']}
    failures=[]
    for row in r['rows']:
        if row['config'] in (BASELINE,selected) and row['split']=='test' and row['answerable'] and row['metrics']['evidence_recall_budget1500']<1:
            q=questions[row['id']]
            failures.append({'config':row['config'],'id':row['id'],'query':q['query'],'gold':q['gold'],
                'top5':row['top5'],'metrics':row['metrics']})
    failure_path=args.input.with_name('retrieval-v1-failures.json')
    failure_path.write_text(json.dumps(failures,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    delta=r['paired_test_delta']
    principal=[BASELINE,selected,'fixed300/none/bm25','boundary900_120/bge-m3/dense',
               'fixed300/bge-m3/dense','fixed300/qwen3-plain/dense','fixed300/qwen3-instruct/dense']
    text=f'''# 检索切分与向量模型对照实验：真实调用结果与复现方法

本次新增一套独立检索评测：固定资料、问题和原文证据位置，真实调用硅基流动的两种向量模型，比较5种切分配置和不同排序方法，保存逐题排名、评分、调用用量与失败案例。业务数据库和网站默认检索配置没有改变；本报告不代替最终改写质量评测。

## 1. 先看实际结论

已完成 **{len(summaries)}组配置 × 72个问题 = {len(r['rows'])}条检索结果**。真实Embedding请求 **{r['actual_api_calls']}次**，供应商累计返回 **{r['actual_total_tokens']:,} tokens**，接口失败{sum(x['status']!='ok' for x in r['api_requests'])}次。没有调用聊天生成模型，没有把3960条离线排序误写成3960次API请求。

以下都是留出的测试集结果：32道有答案题用于召回评分，4道无答案题另行统计。

{table(r,principal)}

**这次实验没有证明“向量模型一定比关键词好”。** 当前900字重叠120字的BGE混合方案，前5块包含全部所需证据；但在同样的1500正文字符预算下，召回明显下降。开发集按事先规则选出的方案是 **{name(selected)}**，测试集1500字内证据召回为 **{percent(summaries[selected]['test']['evidence_recall_budget1500'])}**。

当前混合方案平均返回约{summaries[BASELINE]['test']['context_chars']:.0f}正文字符。大块前面包含的背景和失效版本会占用预算，让后面真正需要的限定语进不了上下文。这是本次测出的具体优化方向：先改证据选择和上下文装配，再评估换模型是否有效。

这里的1500字是**对照实验另设的正文预算**，不是声称目前网站已经按这个规则截断。正文不含标题和提示词开销，字符也不等于token。当前产品直接返回前5块；实验分别展示不限正文预算的前5块与受预算限制的结果。

## 2. 实验资料及公平性

- 数据集16篇、8个主题家族，每个主题一篇2026现行版与一篇2024失效存档；总计{sum(len(d['content']) for d in data['documents']):,}个正文字符。
- 72题中64题有指定证据、8题资料没有答案。有答案题包含精确事实、同义提问、多处证据；每题检索8篇事先固定并打乱顺序的资料，模拟产品的授权选择范围。
- 前4个主题家族用于开发，后4个用于留出测试；各32道有答案题和4道无答案题。不是同一主题的相邻问题随机一半训练一半测试。
- 文档是AI编写的虚构案例，答案由原文字符位置确定，不使用同一个聊天模型给自己的答案打分。**尚未经过独立人工标注复核**，属于受控开发基准，不是公共权威榜单或真实用户满意度调查。
- 问题明确给出主题名称和现行版本，文档有大量相似的说明段落。关键词方案可以利用标题；向量方案按当前产品行为只向量化正文，未将标题重复拼到每块。因此这里只比较这些实现，不能单独据此断言模型的纯语义能力高低。
- 每篇约1000字，900字方案每篇只有两块；授权范围只有8篇。取前5块很容易达到高召回，**不是大规模知识库检索性能结论**。后续需要长资料、跨文档问答、真实释义和不同噪声分布。
- 所有方案使用完全相同的问题、候选来源范围和原文证据标注。改变切分后不会重新把答案“挪”到有利的块里；证据必须完整被检索片段覆盖。
- 留出仅指问题所属主题；检索候选仍允许其他主题做干扰，因此开发/测试的候选资料并非完全隔离。没有训练Embedding权重。

材料在调用前冻结，数据SHA256：`{r['protocol']['dataset_sha256']}`。源码和协议哈希在结果JSON的`protocol`里；不能看了测试结果再改题目、改评分或重新选冠军。

## 3. 到底比较了什么

{chr(10).join(chunk_table)}

“边界切分”调用项目的`split_content`：在长度上限附近优先找换行或句末，再按长度截取；有PDF页码时不跨页。本次都是普通文字，**没有测PDF解析或OCR**。300/600/900均为字符上限，实际片段可能短于这个值。

每种切分配11种排序配置：关键词和BM25两条不使用模型的基线；BGE-M3原始查询、Qwen3-Embedding-0.6B原始查询、Qwen带查询指令三种向量方式，各自再测纯向量、关键词与向量混合、BM25与向量混合。

关键词按中文二元字符组合与英文数字词的集合交集计分。BM25保留词频，用词的稀有程度、词频饱和与长度归一化计分；本实验固定k1=1.2、b=0.75，没有额外搜索参数。参数含义及常见默认值见[Apache Lucene官方说明](https://lucene.apache.org/core/7_7_0/core/org/apache/lucene/search/similarities/BM25Similarity.html)。这是小集合Python实现，不等于部署了Elasticsearch。

纯向量使用归一化向量点积，等价于余弦相似度，按分数降序。两种模型实测均返回1024维，未量化或截断维度，未使用ANN近似索引。BGE这里仅使用API返回的dense向量，没有使用其稀疏或多向量功能。[BGE官方模型说明](https://huggingface.co/BAAI/bge-m3)

Qwen的第三种方式只给**查询**加固定英文检索指令，资料正文不加；用法依据[Qwen官方模型说明](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)。这是一个单因素对照，不代表已经把指令调到最优，也没有把官方榜单提升幅度当作本项目成绩。具体输入格式保存在`run.py`的`MODEL_VARIANTS`。

混合使用RRF：某个片段在一个通道排第r，获得`1/(60+r)`分，两路相加后排序。关键词混合复现当前产品：所有非零关键词结果与前10个向量结果融合；BM25混合固定两路各取前10。两者除了词法算法，还存在候选截断差别，**不能把二者差异全部归因于BM25**。已有测试核对实验关键词混合与生产函数的前5条引用一致。

## 4. 这些指标怎么算

信息检索的一般定义中，Recall关心相关内容找回多少，Precision关心返回内容有多少相关；排序位置也会影响评价。[Stanford信息检索教材](https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-unranked-retrieval-sets-1.html) 本项目为了跨切分公平，专门把正确答案标成原文证据区间，以下公式和边界以`core.py`为准。

| 指标 | 本实验的具体算法 | 能回答的问题 |
|---|---|---|
| 证据Recall@1/@3/@5 | 每题：前k块完整覆盖的正确证据条数 ÷ 该题正确证据总数；再对有答案题平均 | 要找的依据找回了多少 |
| Hit@5 | 每题前5块至少完整找回一条证据记1，否则0，再平均 | 是否至少找到了部分依据 |
| 完整取证题比例@5 | 每题所需证据全部完整覆盖才记1 | 多条件问题是否漏了一项 |
| 证据MRR@5 | 最早在哪个排名前缀中完整覆盖任一证据，记该排名倒数；五条内没覆盖记0 | 有用依据是否排得靠前 |
| 片段Precision@5 | 前5块中与任一正确证据有交集的块数 ÷ 5 | 返回块中多少涉及答案 |
| 1500字符预算证据召回 | 按前5块排序装入1500正文字符，最后一块截断；再算完整证据召回 | 在相同上下文容量下还能找回多少 |
| 上下文字符数 | 前5块原文长度相加 | 需要把多少正文交给生成阶段 |
| 重叠冗余率 | 1 − 同来源区间合并后的长度 ÷ 返回块总长度 | 相同位置被反复带入了多少 |
| 无答案仍返回率 | 无答案题中返回至少一块的比例 | 检索是否天然会拒绝无关问题 |
| 本地排序P50/P95 | 查询向量已在缓存时的评分、排序耗时分位数 | 当前小候选集合的本地计算开销 |

例如一题要求“保修多久”和“境外购买是否适用”，标注有两条原文证据，只找到保修条款：证据召回=1/2=50%，Hit=1，完整取证=0。若第一条正确证据首次在第2块才完整出现，证据MRR=1/2。若五块中两块触及证据，片段Precision=2/5=40%。

同一证据被两个重叠块命中，Recall仍只算一条。证据横跨相邻两块，两块一起覆盖完整原文时也算找到。中间有缺口、找到了失效版本或者只有前半句，都不能冒充找全。

注意三个细节：①这是逐题宏平均，不是把所有题的证据数量直接合并；②这里MRR按“前缀完整覆盖证据”定义，跨块时不同于传统“首个相关文档”的MRR；③Precision只要求片段与证据相交，Recall要求完整覆盖，二者判定单位不同，不能直接拿来算F1。重复块会影响Precision，因此它不是本次选优主指标。

这轮没有为nDCG强行指定主观相关等级，也没有把检索召回称为“答案准确率”。最终引用是否支持结论、数字是否保留、是否产生幻觉、用户是否接受，需要后面的生成评测和人工复核。

## 5. 固定其他因素后，拆开看影响

### 5.1 只改切分：固定BGE-M3与当前关键词混合

{table(r,[s+'/bge-m3/rrf_keyword' for s in SPLITTERS])}

### 5.2 只改模型或查询指令：固定300字硬切分与纯向量

{table(r,['fixed300/bge-m3/dense','fixed300/qwen3-plain/dense','fixed300/qwen3-instruct/dense'])}

### 5.3 固定900字重叠120与BGE：比较排序方式

{table(r,['boundary900_120/none/keyword','boundary900_120/none/bm25','boundary900_120/bge-m3/dense','boundary900_120/bge-m3/rrf_keyword','boundary900_120/bge-m3/rrf_bm25'])}

### 5.4 按题型检查，不能只报一个总分

{chr(10).join(categories)}

## 6. 怎样选方案，以及为什么还不自动替换网站默认值

调用前固定规则：**只看开发集**，按1500字符预算证据召回降序，再按证据MRR降序、上下文长度升序，最后配置名打破平局。选中`{selected}`，之后才报告这一个方案在测试集上的成绩。完整55组测试成绩作为描述性对照公开，不允许看完再换一个“测试冠军”宣传。

与当前默认基线相比，选中方案的测试集预算召回差为 **{delta['delta']*100:.2f}个百分点**。按4个测试主题家族进行2000次成组重采样，描述性95%区间为 **[{delta['ci95'][0]*100:.2f}, {delta['ci95'][1]*100:.2f}]个百分点**。独立家族只有4个，区间不稳定；不是跨真实业务的显著性保证。

证据尚不足以把网站全部切成300字关键词。短片段会拆散限定语；这里题目带有明确主题词，天然有利于关键词；当前产品也未采用本实验的1500字正文截断策略。合理行动是保留两类候选，用新增真实资料验证“标题随块保留、现行版本优先、重叠去重与相邻证据补全”是否改善受预算检索，再决定生产参数。

## 7. 实际失败在哪里

**当前方案示例：`citation-q6`。** 题目要找资料缺失时如何处理，正确依据位于现行引用规范末段。当前排序前两块分别是现行版前892字和失效版前861字，所需末段在第3块。取完整前5块能找到；1500字预算在第2块中间就用完，所以这个题的预算召回为0。问题不是向量API没接通，而是预算被前面的背景占满。

**选中方案示例：`return-q8`。** 问保修期限和境外购买范围，需要两条分散的证据。300字关键词找到了保修条款，却把境外适用条件所在末块排到前5之外，最终只覆盖一条，证据召回50%。高平均成绩仍会漏关键限制。

完整失败记录：[逐题失败JSON](results/retrieval-v1-failures.json)。每条包含问题、正确原文、证据位置和返回片段位置，可直接对照[数据集](retrieval/dataset.json)。同一题可能在两种配置各出现一次，不把条目数当成独立题数。

**无答案测试：** 55组方案在留出的4个无答案问题上都返回了片段，仍返回率100%。这说明当前检索只负责排序，不能凭“有结果”判定资料充足。这个数字不是100%幻觉率，也不是最终Agent拒答率；生成阶段可能继续读资料后停下。下一轮应单独评估证据充分性判定和最终拒答。

## 8. 耗时与费用：记录真实用量，分清测量范围

{chr(10).join(usage_table)}

{chr(10).join(latency)}

网络延迟是每种查询方式8个独立单条请求的端到端客户端时间，包含网络、服务及本地响应处理；每个主题取第1题，顺序执行。P95采用线性插值。样本很少、没有并发压测、不同模型并非同时请求，不能据此宣称稳定生产SLA或吞吐量。

正文和其余查询按批请求，**没有把批请求时间除以条数冒充单用户延迟**。完整请求日志见结果JSON的`api_requests`。调用全部顺序执行，未重建容器或加载本地Embedding模型。

本地排序不含API、网络、数据库读取和缓存文件加载。当前方案测试题本地排序P50={summaries[BASELINE]['test']['rank_ms_p50']:.2f}毫秒、P95={summaries[BASELINE]['test']['rank_ms_p95']:.2f}毫秒；选中关键词方案P50={summaries[selected]['test']['rank_ms_p50']:.2f}毫秒、P95={summaries[selected]['test']['rank_ms_p95']:.2f}毫秒。候选集很小，不能当百万向量性能。

费用只报告供应商实际usage字段，不把字符数估成token，也不猜人民币扣费。账户优惠、免费额度及实际结算以硅基流动账单为准。[硅基流动Embedding接口说明](https://docs.siliconflow.cn/docs/api/embeddings-post)

预算预先限制为96次请求、累计900000 UTF-8输入字节；每次调用前落盘计数，SDK自动重试关闭。相同模型和输入按哈希缓存，Qwen的两种查询方式复用正文向量。缓存命中不再次收费。本次未达到预算上限。

## 9. 文件与复现

| 文件 | 用途 |
|---|---|
| `evals/retrieval/dataset.json` | 16篇公开合成资料、72个问题、来源范围与原文证据 |
| `evals/retrieval/core.py` | 不联网的切分、排序和指标计算 |
| `evals/retrieval/run.py` | 冻结协议、限额真实请求、缓存、逐题实验 |
| `evals/retrieval/replay.py` | 读取本机向量缓存重新核对全部逐题排名和分数 |
| `evals/retrieval/report.py` | 从结果重新生成本报告、汇总CSV和失败记录 |
| `evals/results/retrieval-v1.json` | 3960条原始检索结果、55组汇总、实际调用账本 |
| `evals/results/retrieval-v1.csv` | Excel可打开的110行开发/测试汇总 |
| `backend/tests/test_retrieval_eval.py` | 评分、跨块覆盖、预算、排序与生产逻辑一致性测试 |
| `private/retrieval-eval-v1/` | 本机向量缓存和冻结协议，被Git忽略，不含密钥副本 |

在项目根目录PowerShell运行。以下前四项均不会调用API：

```powershell
Set-Location F:\\01Writing
.\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_retrieval_eval.py -q
.\\.venv\\Scripts\\python.exe -X utf8 evals/retrieval/run.py
.\\.venv\\Scripts\\python.exe -X utf8 evals/retrieval/replay.py
.\\.venv\\Scripts\\python.exe -X utf8 evals/retrieval/report.py
```

`run.py`不加`--live`只检查数据并冻结方案，**没有运行模型或产生质量分数**。`replay.py`需要这台电脑已有的私有缓存；其他人克隆Git仓库没有缓存，可看公开逐题结果，或自行配置密钥真实运行。离线复算不会重新测网络时间。

重新调用服务时：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 evals/retrieval/run.py --live --cache private/retrieval-eval-rerun --output evals/results/retrieval-rerun.json
```

这条会使用本机`.env.deploy`里的硅基流动配置并产生新的真实请求，务必使用新输出名保存新实验，别覆盖本轮结果。不必为了看报告再次调用。当前运行器固定使用本项目的硅基流动服务，只发送合成数据；真实私有文档不要直接塞进公开数据集。

同一缓存目录会校验数据和程序哈希：修改了数据或评分，就用新的实验编号和新缓存目录；不能删除协议文件来伪装成旧实验。

## 10. 下一轮应怎样提高可信度

1. 增加由独立人员复核的实际写作资料和问题，去掉题目中明显的标题提示，覆盖长文、跨来源和矛盾依据；保留一批真正不用于选参的测试集。
2. 针对本次失败，把“标题/版本随块保留、去重、相邻补全、证据重排”拆成控制变量对照，不能同时改一堆再把收益归给某个框架。
3. 验证资料充分性：无答案时该询问还是拒答，报告无答案拒答正确率和有答案误拒率；阈值只能在开发集选。
4. 固定检索方案后接上最终改写：评价证据支持、数字和限定语保持、引用正确性，再加人工接受率。检索好不等于写得正确。
5. 资料量增长后再评估pgvector/HNSW近似检索：与精确余弦的前k结果比较近似召回，记录并发、P95、索引大小与构建时间。当前生产是PostgreSQL JSON保存向量、Python在授权快照中精确比较，尚非pgvector。

以后每次检索相关改动都应附“固定数据集与版本 → 对照 → 逐题结果 → 成本和失败解释”。这轮已经提供第一套可复算基线；它的价值既包含高分，也包含证据不足、预算浪费和方案退化的明确记录。

## 附录：全部55组配置的测试集描述性结果

下表按配置名排列，不按测试集成绩重新选冠军。完整MRR、Precision、冗余率、延迟及开发集成绩见CSV。

{table(r,sorted(summaries))}
'''
    args.output.write_text(text,encoding='utf-8')
    print(f'已生成中文实验报告、{len(flat)}行汇总CSV、{len(failures)}条失败配置记录。')


if __name__=='__main__':
    main()

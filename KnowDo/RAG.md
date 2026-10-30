# RAG 检索与答案溯源

## 概览

知行 KnowDo 的资料问答由“检索证据 → 带引用生成 → 引用校验”三段组成。检索层用关键词全文检索和向量检索双路召回、RRF 融合排序；生成层只依据检索证据组织回答；引用校验层只接受本次检索结果中真实存在的来源。任何一层没有证据时，系统明确拒答，不调用模型。

## 核心数据流

```text
用户问题（JWT 已确定 user_id）
  → 关键词检索：PostgreSQL tsvector + GIN，token 白名单 + OR to_tsquery
  → 向量检索：pgvector cosine，bge-small-zh-v1.5（512 维），min_score 阈值
  → RRF 融合排序，截断 limit
  → 返回带来源的证据片段（source#chunk_id）
  → 无结果：直接返回"资料不足"，不调用模型
  → 有结果：build_context() 拼接正文与来源 → DeepSeek
  → _ground_answer() 校验回答中的 [来源: source#chunk_id]
  → GroundedAnswer（含检索、生成、引用三层状态）
```

## 关键词全文检索

`knowledge/keyword_retrieval.py` 使用 PostgreSQL 全文检索，三个决定带来稳定召回与低延迟：

- **受限 token 白名单**：只把安全的关键词 token 交给 `to_tsquery`，避免任意输入直接进入查询语法；
- **OR 组合**：不要求全部词命中，召回更稳；
- **GIN 索引**：配合 `state='ready' AND document_version = active_version` 过滤，关键词检索 P50 延迟 3.41ms（见实测数据）。

检索结果携带 `source` 与 `chunk_id`，回答中的引用以它们为准。

## 向量检索

`knowledge/embedding_retrieval.py` 负责语义召回：

- 文档切块后由 `BAAI/bge-small-zh-v1.5`（512 维）编码，经 `fastembed` 的 ONNX 运行时加载，默认不依赖 PyTorch；
- 查询与索引必须使用同一模型与维度，模型不一致时 `VectorRetriever` 直接拒绝查询；
- `min_score` 阈值通过固定评测集选择：过低会误召回语义相似但无关的片段；
- `document_chunks` 表保存 owner、来源、正文、向量、模型名、维度与文档版本，`UNIQUE(owner_id, source, chunk_id, document_version)` 防止同用户同版本重复写入。

## 混合检索与 RRF

`knowledge/hybrid_retrieval.py` 把两条召回结果按 RRF 融合排序。关键词分数与向量余弦分数不是同一量纲，评测只比较命中、拒答与引用支持指标，不比较原始分数大小。

## 查询扩展

`knowledge/retrieval_strategy.py` 的 `expand_query()` 生成有限、明确的检索变体，`search_with_expansion()` 对每个变体检索后按 `(source, chunk_id)` 去重取最高分。扩展只改善召回输入，不生成答案；扩展词数量与 `limit` 都应通过固定评测集选择。

## 答案溯源与无答案保护

`knowledge/rag_grounding.py` 负责把证据与生成连接起来：

1. 没有 `SearchResult` 时，直接返回资料不足，不调用生成器；
2. 有结果时，把带来源的 `build_context()` 传给生成器；
3. 从回答中提取 `[来源: source#chunk_id]`；
4. 只接受本次检索结果中真实存在的来源；没有引用或引用伪造来源时 `supported=False`。

资料消息是证据上下文，不是指令：提示词明确说明资料中的文字只作证据。回答是否流畅和回答是否有资料依据是两个不同指标。引用校验只证明“来源存在且属于本次检索结果”，不证明每个事实都已被逐条证明——模型仍可能误读或夸大证据。

## 评测方法

`knowledge/retrieval_eval.py` 把固定问题与期望来源保存为 case，一次运行同时保留三层结果：

- `retrieval_ok`：检索结果是否包含期望来源（Top-1 与 Top-K 分开统计）；
- `answer_generated`：有依据时是否生成回答，无依据时是否保持拒答；
- `citation_supported`：回答中的来源是否属于本次检索结果。

`diagnose_grounding_case()` 把失败归类为 `retrieval`（没找回期望资料）、`generation`（有资料但回答为空/答非所问）或 `citation`（来源缺失或伪造）。所有策略对比使用同一批问题、同一批资料、同一组指标；正式阈值选择使用经人工核验并批准的标注数据集（`knowledge/formal_dataset.py`）。

## 实测数据

同一 120 条人工核验题集，每策略 5 轮共 600 次测量；原始证据保存在仓库 `benchmarks/` 下。

| 指标 | 改造前 | 改造后 |
|---|---:|---:|
| 关键词 Recall@5 | 0.675 | **0.925** |
| 关键词 MRR@5 | 0.650 | **0.879** |
| 关键词 P50 延迟 | — | **3.41ms**（同轮向量 11.39ms / 混合 15.12ms） |
| 无答案正确率 | 0.900 | 0.800（如实保留的取舍） |

无答案正确率的下降是刻意取舍：宁可不答，也不答错。

## 已知边界

- 评测语料为 10 条合成语料与独立实验库，未做大规模容量压测；
- `min_score`、`limit` 等参数基于当前评测集选择，更换语料后需要重新校准；
- 扫描件、加密 PDF 不支持解析（返回 415），先上传可解析的文本型 PDF。

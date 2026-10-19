# RAG 系统代码走读

> 这篇跟着**一份文档的上传**和**一个问题的回答**，把代码从头走一遍。
> 先读 [导读](./RAG系统架构导读.md) 了解全貌，再读这篇。

**每一步的格式都一样：**

- **做什么**：一句话说清这一步的目的
- **代码**：源码的精简版。删掉了日志、异常处理和次要分支，变量名和调用关系与源码一致；改写得比较多的片段会标注“（精简）”或“（伪代码）”。点“代码：”后面的链接可以跳到源码原文
- **解释**：这段代码在干什么
- **实测**：在运行中的系统里跑出来的真实输入输出

**用到的例子：** 知识库里已经上传了两份研报《润本股份.pdf》《国电电力.pdf》，切成了 60 块。提问的例子是“润本股份的毛利率是多少”。

---

## 目录

**第一部分：上传一份文档**

- [1.1 接口收到文件](#11-接口收到文件)
- [1.2 按文件类型选解析器](#12-按文件类型选解析器)
- [1.3 PDF 怎么变成文字](#13-pdf-怎么变成文字)
- [1.4 把段落合并成块](#14-把段落合并成块)
- [1.5 给每块分词](#15-给每块分词)
- [1.6 组装成 ES 文档并写入](#16-组装成-es-文档并写入)

**第二部分：回答一个问题**

- [2.1 接口收到问题](#21-接口收到问题)
- [2.2 检索入口](#22-检索入口)
- [2.3 把问题变成关键词检索语句](#23-把问题变成关键词检索语句)
- [2.4 关键词和向量一起查 ES](#24-关键词和向量一起查-es)
- [2.5 重排给候选重新打分](#25-重排给候选重新打分)
- [2.6 拼提示词](#26-拼提示词)
- [2.7 调大模型并流式返回](#27-调大模型并流式返回)
- [2.8 前端读流](#28-前端读流)
- [2.9 前端渲染引用角标](#29-前端渲染引用角标)

**附录**

- [A. 中文分词器是怎么分词的](#a-中文分词器是怎么分词的)
- [B. 想调效果，改哪里](#b-想调效果改哪里)
- [C. 自己动手跑一遍](#c-自己动手跑一遍)

---

# 第一部分：上传一份文档

## 1.1 接口收到文件

**做什么：** 把上传的文件存到磁盘，然后交给解析流程。

**代码：** [api/chat.py 第 216、295、298 行](../backend/app/api/chat.py#L216)

```python
# 前端不传 session_id，所以这里 session_id 就等于 user_id
if session_id is None:
    session_id = user_id

# ……检查重名、校验文件、存到 engine/storage/file/{user_id}/ ……

execute_insert_process(file_url, file_name, session_id)   # 解析并写入 ES
insert_knowledgebase(user_id, file_name)                  # 在 PostgreSQL 记一笔
```

**解释：**

- `session_id` 在这里会被当作 ES 的**索引名**传下去。因为前端不传，它就是 `user_id`。所以**每个用户一个索引**，用户 1 的所有文档都在索引 `1` 里。
- 真正干活的是 `execute_insert_process()`，下面几步都在它里面。

**代码：** [services/parsing.py 第 90 行](../backend/app/services/parsing.py#L90)

```python
def execute_insert_process(file_path, file_name, index_name):
    documents = parse(file_path)                                       # 1.2 ~ 1.5：解析、切块、分词
    processed_documents = process_items(documents, file_name, index_name)  # 1.6：向量化、组装
    for i in range(0, len(processed_documents), 100):                  # 每 100 条写一次 ES
        es_connection.insert(documents=processed_documents[i:i+100], indexName=index_name)
```

这几行就是整个上传流程。`parse()` 只是调了一下 `chunk()`，见下一步。

---

## 1.2 按文件类型选解析器

**做什么：** 看扩展名，决定用哪个解析器把文件读成“段落列表”。

**代码（精简）：** [engine/rag/app/naive.py 第 193 行](../backend/app/engine/rag/app/naive.py#L193)

```python
def chunk(filename, binary=None, ..., callback=None, **kwargs):
    parser_config = {"chunk_token_num": 128, "delimiter": "\n!?。；！？", "layout_recognize": "DeepDOC"}

    doc = {
        "docnm_kwd": filename,
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename)),  # 文件名分词，检索时权重很高
    }

    if   re.search(r"\.docx$", filename): sections, tables = Docx()(filename, binary)
    elif re.search(r"\.pdf$",  filename): sections, tables = Pdf()(filename, ...)
    elif re.search(r"\.xlsx?$", filename): sections = ExcelParser()(binary)
    elif re.search(r"\.(txt|py|js|…)$", filename): sections = TxtParser()(filename, ...)
    elif re.search(r"\.(md|markdown)$", filename): sections, tables = Markdown(128)(filename, binary)
    # ……html、json、doc 同理……

    res = tokenize_table(tables, doc, is_english)          # 表格：每张表直接成一块
    chunks = naive_merge(sections, 128, ...)               # 正文：合并成块（1.4）
    res.extend(tokenize_chunks(chunks, doc, is_english, pdf_parser))  # 分词（1.5）
    return res
```

**解释：**

- 每种解析器都输出两样东西：`sections`（正文段落列表）和 `tables`（表格列表）。
- **表格和正文走不同的路**：表格每张直接成一块；正文段落要先合并（1.4）。
- 各解析器的特点：

| 格式 | 怎么读 |
|---|---|
| PDF | 最复杂，见 1.3 |
| Word | 逐段读，表格转成 HTML |
| Excel | 每一行变成一句话：`表头1：值1; 表头2：值2` |
| TXT | 按标点切句 |
| Markdown | 标题和下一行合在一起 |

> **注意：** 这里的 `filename` 实际传进来的是**完整路径** `/app/engine/storage/file/1/润本股份.pdf`，所以 `title_tks` 实测是 `app engin storag file 1 润本股份`，混进了路径里的词。应该传 `os.path.basename(...)`。

---

## 1.3 PDF 怎么变成文字

**做什么：** 把 PDF 的每一页变成按阅读顺序排好的段落，表格还原成 HTML。

**代码：** [engine/rag/app/naive.py 第 127 行](../backend/app/engine/rag/app/naive.py#L127)

```python
class Pdf(PdfParser):
    def __call__(self, filename, ...):
        self.__images__(filename, zoomin)          # ① 渲染成图片 + OCR，得到一行一行的文本框
        self._layouts_rec(zoomin)                  # ② 版面识别：这个框是正文、标题、表格还是页眉？
        self._table_transformer_job(zoomin)        # ③ 表格结构识别：表格里每个框在第几行第几列？
        self._text_merge()                         # ④ 横向合并：同一行的框拼起来
        tbls = self._extract_table_figure(...)     # ⑤ 把表格和图单独拿出来，表格转成 HTML
        self._concat_downward()                    # ⑥ 纵向合并：上下行拼成段落
        return [(b["text"], self._line_tag(b, zoomin)) for b in self.boxes], tbls
```

**解释：** 一共 6 步，全部在 [deepdoc/parser/pdf_parser.py](../backend/app/engine/deepdoc/parser/pdf_parser.py)。整个过程操作的都是“文本框”，每个框是一个字典（下面的数值是示意）：

```python
{"x0": 72.1, "x1": 523.4, "top": 218.5, "bottom": 230.0,   # 在页面上的位置
 "page_number": 3,
 "text": "25H1公司毛利率同比+0.4pct至59.0%",
 "layout_type": "text"}                                    # 第 ② 步加上
```

逐步说明：

**① 渲染 + OCR**（[`__ocr`](../backend/app/engine/deepdoc/parser/pdf_parser.py#L279)）：每页渲染成图片，用 OCR 模型找出所有文本行的位置。然后优先用 PDF 自带的文字填进去；只有找不到文字的（扫描件、图片里的字）才用 OCR 识别。所以电子版 PDF 的文字是准确的。

**② 版面识别**（[layout_recognizer.py](../backend/app/engine/deepdoc/vision/layout_recognizer.py#L60)）：用模型判断每个区域是正文、标题、表格、图、页眉、页脚等 10 类之一。**页眉页脚直接丢掉**，“资料来源：”、版权声明、页码这类行也用正则删掉。

**③ 表格结构识别**：把表格区域裁成小图，用模型识别出行、列、表头、合并单元格，给表格里的每个框标上行号和列号。

**④ 横向合并**：同一版面区域里，垂直位置几乎一样的相邻框，左右拼成一个。

**⑤ 抽出表格和图**：表格按行列号拼成 HTML，跨页的同一张表会合并；表标题、图标题归给最近的表或图。

**⑥ 纵向合并**：判断上下两行是不是同一段话。这一步用了一个小模型：

**代码：** [pdf_parser.py 第 511 行](../backend/app/engine/deepdoc/parser/pdf_parser.py#L511)

```python
fea = self._updown_concat_features(up, down)            # 算 32 个特征
if self.updown_cnt_mdl.predict(xgb.DMatrix([fea]))[0] <= 0.5:
    i += 1          # 模型说不该接，看下一个
    continue
dfs(down, i + 1)    # 模型说该接，接上，继续往下找
```

32 个特征（[第 108 行](../backend/app/engine/deepdoc/parser/pdf_parser.py#L108)）都是人能理解的规则，比如：上一行是不是以句号结尾、下一行是不是以“（一）”“1.”这种编号开头、两行的垂直距离是行高的几倍、是不是跨页了。XGBoost 模型根据这些特征给出“该不该接”的概率。

**最后的 `_line_tag`** 给每段文字末尾加一个位置标签，形如 `@@3\t72.1\t523.4\t218.5\t230.0##`（页码、左、右、上、下），后面切块时会用它算出每块的页码。

---

## 1.4 把段落合并成块

**做什么：** 解析出来的段落有长有短，把它们合并成长度差不多的块。

**代码（精简，省略了位置标签的处理）：** [engine/rag/nlp/\_\_init\_\_.py 第 86 行](../backend/app/engine/rag/nlp/__init__.py#L86)

```python
def naive_merge(sections, chunk_token_num=128, delimiter="\n。；！？"):
    cks = [""]          # 所有块
    tk_nums = [0]       # 每块的 token 数

    def add_chunk(t, pos):
        tnum = num_tokens_from_string(t)
        if tk_nums[-1] > chunk_token_num:   # 当前块已经超过 128 了
            cks.append(t)                   #   → 开一个新块
            tk_nums.append(tnum)
        else:                               # 还没超过
            cks[-1] += t                    #   → 接在当前块后面
            tk_nums[-1] += tnum

    for sec, pos in sections:
        add_chunk(sec, pos)
    return cks
```

**解释：** 逻辑非常简单：一段一段往当前块里塞，**塞到超过 128 token，下一段才开新块**。

由此得出三个结论：

1. **128 不是上限。** 当前块 120 token 时再塞一段 300 token 的，这块就是 420 token。
2. **段落不会被切断。** 一段话一定完整地在某一块里。
3. **`delimiter` 参数没用到。** 函数签名里有，函数体里没用。

**实测：** 两份研报的块长度（单位 token）：

| 文档 | 块数 | 最短 | 中位数 | 最长 |
|---|---|---|---|---|
| 润本股份.pdf | 22（含 7 张表） | 51 | 251 | 1582 |
| 国电电力.pdf | 38（含 7 张表） | 38 | 201 | 1257 |

中位数是 128 的两倍左右。最长的块都是财务报表：表格不走 `naive_merge`，一张表无论多大都是一块。

---

## 1.5 给每块分词

**做什么：** 每块除了保存原文，还要保存分好词的版本，用于关键词检索。

**代码：** [engine/rag/nlp/\_\_init\_\_.py 第 188 行](../backend/app/engine/rag/nlp/__init__.py#L188)

```python
def tokenize(d, t, eng):
    d["content_with_weight"] = t                                          # 原文
    t = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", t)  # 去掉表格的 HTML 标签
    d["content_ltks"] = rag_tokenizer.tokenize(t)                         # 粗粒度分词
    d["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(d["content_ltks"])  # 细粒度分词
```

**解释：** 每块最终有三个文本字段：

| 字段 | 内容 | 用途 |
|---|---|---|
| `content_with_weight` | 原文（表格是 HTML） | 给大模型看，给前端展示 |
| `content_ltks` | 粗分词，空格隔开 | 关键词检索 |
| `content_sm_ltks` | 细分词，把长词再拆开 | 关键词检索，提高召回 |

**实测：** 一个表格块

```
content_with_weight:  <table><caption>基本数据2025年08月19日盈利预测与估值（人民币）</caption>
                      <tr><td>收盘价（元）</td><td>30.79</td></tr> ……
content_ltks:         基本 数据 2025 年 08 月 19 日 盈利 预测 与 估值 人民币 收盘价 元 30 79 ……
```

分词器的工作原理见 [附录 A](#a-中文分词器是怎么分词的)。

> **注意：** PDF 的块在这一步还会用 1.3 里的位置标签算出页码（`page_num_int`）和坐标（`position_int`），见 [`tokenize_chunks()` 第 280 行](../backend/app/engine/rag/nlp/__init__.py#L280)。但下一步写 ES 时没带上它们。

---

## 1.6 组装成 ES 文档并写入

**做什么：** 给每块算向量，拼成 ES 能存的字典，批量写入。

**代码：** [services/parsing.py 第 47 行](../backend/app/services/parsing.py#L47)

```python
texts = [item["content_with_weight"] for item in items]
embeddings = batch_generate_embeddings(texts)          # 所有块一起向量化

for item, embedding in zip(items, embeddings):
    d = {
        "id": xxhash.xxh64((item["content_with_weight"] + index_name).encode()).hexdigest(),
        "content_ltks":        item["content_ltks"],
        "content_with_weight": item["content_with_weight"],
        "content_sm_ltks":     item["content_sm_ltks"],
        "create_time": ..., "create_timestamp_flt": ...,
    }
    d["kb_id"]     = index_name                         # 就是 user_id
    d["docnm_kwd"] = item["docnm_kwd"]                  # 文件路径
    d["title_tks"] = item["title_tks"]
    d["doc_id"]    = xxhash.xxh64(file_name.encode()).hexdigest()
    d["docnm"]     = file_name
    d[f"q_{len(embedding)}_vec"] = embedding            # 向量，字段名是 q_1024_vec
```

向量化的代码在 [nlp/model.py 第 137 行](../backend/app/engine/rag/nlp/model.py#L137)：每 10 块一批调用阿里云百炼的 `qwen3.7-text-embedding`，任何一批失败，整个文件就上传失败。

**解释：**

- 块的 ID 是“原文 + 索引名”的哈希，所以同一用户重复上传相同内容会覆盖，不会重复。
- 向量字段名里带维度（`q_1024_vec`），检索时也按这个规则拼字段名。

> **注意：字典是手动一个个字段写的，漏了几个。** 上一步算好的 `page_num_int`（页码）、`position_int`（坐标）、`image`（表格截图）、`title_sm_tks` 都没写进来。在运行中的索引里查过，60 块都没有页码字段，所以前端引用卡片的页码永远显示 `-`。修法：在这个字典里把这几个字段从 `item` 带上。

**写入 ES 时还有一个问题。** [es_conn.py](../backend/app/engine/rag/utils/es_conn.py#L55) 在初始化时读了 `conf/mapping.json`（字段该怎么建索引的配置），但从来没用它建过索引。索引是第一次写入时 ES 自动建的，用的是默认规则。后果在 [2.4](#24-关键词和向量一起查-es) 讲。

---

# 第二部分：回答一个问题

## 2.1 接口收到问题

**做什么：** 先检索，再把检索结果交给大模型，以流的形式返回。

**代码：** [api/chat.py 第 160 行](../backend/app/api/chat.py#L160)

```python
@router.post("/chat_on_docs")
async def chat_on_docs(session_id: str = Query(...), request: ChatRequest = Body(...), credentials = Security(access_security)):
    user_id = str(credentials.subject.get("user_id"))
    question = request.message

    try:
        references = retrieve_content(user_id, question)    # 检索，得到 5 块
    except Exception:
        references = []                                     # 检索失败也不报错，当作没有参考内容

    return StreamingResponse(
        get_chat_completion(session_id, question, references, user_id),   # 生成回答
        media_type="text/event-stream",
    )
```

**解释：**

- 用 `user_id` 检索，也就是去索引 `1` 里找。
- 检索失败**不会报错**，只是参考内容为空。这时提示词里的参考内容一栏会写成“暂无相关参考内容”，大模型通常会回答资料里没有相关信息。
- `get_chat_completion()` 是一个生成器，每 `yield` 一次，`StreamingResponse` 就往浏览器推一条消息。

---

## 2.2 检索入口

**做什么：** 调检索核心 `Dealer.retrieval()`，只保留前端需要的字段。

**代码（精简）：** [services/retrieval.py 第 13 行](../backend/app/services/retrieval.py#L13)

```python
def retrieve_content(indexNames, question):
    results = dealer.retrieval(
        question=question,
        tenant_ids=indexNames,            # 索引名 = user_id
        vector_similarity_weight=0.6,     # 重排阶段：重排模型分占 60%，关键词分占 40%
        page=1, page_size=5,              # 只要前 5 条
        ...)
    return [{"id": i, "document_id": c["doc_id"], "document_name": c["docnm_kwd"].split("/")[-1],
             "content_with_weight": c["content_with_weight"]}
            for i, c in enumerate(results["chunks"], start=1)]
```

**`Dealer.retrieval()` 内部分三步：**

**代码（精简）：** [nlp/search_v2.py 第 297 行](../backend/app/engine/rag/nlp/search_v2.py#L297)

```python
def retrieval(self, question, ..., page_size, similarity_threshold=0.1, vector_similarity_weight=0.3, ...):
    req = {"question": question, "size": max(page_size * 3, 128), "similarity": similarity_threshold, ...}

    sres = self.search(req, [index_name(tid) for tid in tenant_ids], ...)        # 第一步：查 ES，取 128 条候选

    sim, tsim, vsim = self.rerank_by_model(sres, question,                        # 第二步：重排打分
                                           1 - vector_similarity_weight, vector_similarity_weight)
    idx = np.argsort(sim * -1)[0:page_size]                                       # 按分数从高到低排

    for i in idx:                                                                 # 第三步：取前 5，低于 0.1 的丢掉
        if sim[i] < similarity_threshold:
            break
        ranks["chunks"].append({..., "similarity": sim[i], "term_similarity": tsim[i], "vector_similarity": vsim[i]})
```

**解释：** 先粗筛（ES 返回 128 条），再精排（重排模型逐条打分），最后取前 5。下面 2.3、2.4 讲粗筛，2.5 讲精排。

---

## 2.3 把问题变成关键词检索语句

**做什么：** 把用户的一句话变成 ES 能执行的关键词查询，并给每个词一个权重。

**代码（伪代码，只保留中文分支的主干）：** [nlp/query.py 第 70 行](../backend/app/engine/rag/nlp/query.py#L70)

```python
def question(self, txt, min_match=0.6):
    txt = FulltextQueryer.rmWWW(txt)                  # ① 删掉疑问词："请问""什么""多少"……

    for tt in self.tw.split(txt):
        twts = self.tw.weights([tt])                  # ② 分词，并给每个词算权重
        tms = []
        for tk, w in sorted(twts, key=lambda x: x[1] * -1):
            sm = rag_tokenizer.fine_grained_tokenize(tk).split()          # ③ 长词再拆成细粒度词
            tk = f'{tk} OR "{" ".join(sm)}" OR ("{" ".join(sm)}"~2)^0.5'  #    原词 OR 细粒度词
            tms.append((tk, w))
        tms = " ".join([f"({t})^{w}" for t, w in tms])                    # ④ 每个词乘上权重
        tms += ' ("%s"~2)^1.5' % rag_tokenizer.tokenize(tt)               # ⑤ 整句短语匹配，额外加分

    return MatchTextExpr(self.query_fields, query, 100, {"minimum_should_match": min_match}), keywords
```

**解释：** 一共五步，看实测最直观。

**实测：** 输入“请问国电电力2023年的营业收入增长了多少？”

```
① 删疑问词后：国电电力2023年的营业收入增长了？

② 分词并算权重（"的""了"是停用词，被去掉）：
   2023 → 0.27     营业 → 0.25     国电电力 → 0.14
   收入 → 0.12     增长 → 0.12     年 → 0.11

③④⑤ 最终的检索语句：
   (2023)^0.27 (营业)^0.25 (国电电力 OR "国电 电力" OR ("国电 电力"~2)^0.5)^0.14
   (收入)^0.12 (增长)^0.12 (年)^0.11
   ("国电电力 2023 年 的 营业 收入 增长 了"~2)^1.5
```

- `^0.27` 是权重，越大越重要。
- `"国电 电力"` 是细粒度拆分，这样原文写“电力”也能沾上边。
- `~2` 表示词之间最多隔 2 个词也算匹配。
- 最后一行是整句匹配：原文里这些词按差不多的顺序挨着出现时，额外加分（权重 1.5）。

**这个语句会在哪些字段里查？** [query.py 第 29 行](../backend/app/engine/rag/nlp/query.py#L29)：

```python
self.query_fields = [
    "title_tks^10",       # 文件名，权重 10
    "title_sm_tks^5",
    "important_kwd^30",
    "important_tks^20",
    "question_tks^20",
    "content_ltks^2",     # 正文，权重 2
    "content_sm_ltks",
]
```

**问题里的词出现在文件名里，得分是出现在正文里的 5 倍。** 另外 `important_*`、`question_*`、`title_sm_tks` 这几个字段本系统从没写入过，查了也是空。

> **注意：** 上面“国电电力”的权重只排第三，比“2023”“营业”都低，这不太合理。原因是权重计算依赖的两个词典 `ner.json`（实体词典，会给公司名 ×3）和 `term.freq`（文档频率）不在 `engine/rag/res/` 里。权重公式见 [term_weight.py 第 223 行](../backend/app/engine/rag/nlp/term_weight.py#L223)。

---

## 2.4 关键词和向量一起查 ES

**做什么：** 把问题也转成向量，和 2.3 的关键词语句一起发给 ES，取 128 条候选。

**代码：** [nlp/search_v2.py 第 100 行](../backend/app/engine/rag/nlp/search_v2.py#L100)

```python
matchText, keywords = self.qryr.question(qst, min_match=0.3)          # 2.3 的关键词语句，至少匹配 30%
matchDense = self.get_vector(qst, emb_mdl, topk, 0.1)                 # 问题转向量，相似度下限 0.1
fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05, 0.95"})   # 关键词占 5%，向量占 95%
res = self.dataStore.search(src, ..., [matchText, matchDense, fusionExpr], ...)

if total == 0:                                                        # 一条都没查到
    matchText, _ = self.qryr.question(qst, min_match=0.1)             #   → 放宽到 10% 再查一次
```

`dataStore.search()` 把这三样翻译成 ES 查询：

**代码：** [utils/es_conn.py 第 224 行](../backend/app/engine/rag/utils/es_conn.py#L224)

```python
for m in matchExprs:
    if isinstance(m, MatchTextExpr):                        # 关键词部分
        bqry.must.append(Q("query_string", fields=m.fields, query=m.matching_text, ...))
        bqry.boost = 1.0 - vector_similarity_weight         # 1 - 0.95 = 0.05

    elif isinstance(m, MatchDenseExpr):                     # 向量部分
        s = s.knn(m.vector_column_name, m.topn, m.topn * 2,
                  query_vector=list(m.embedding_data),
                  filter=bqry.to_dict(),                    # ← 注意这里
                  similarity=similarity)
```

**实测：** 问“润本股份的毛利率是多少”时，发给 ES 的请求（截取主干）：

```json
{
  "query": { "bool": {
      "must": [{ "query_string": { "query": "(毛利率 OR \"利率\" …)^0.65 (润本股份 OR \"股份\" …)^0.35 …",
                                   "minimum_should_match": "30%" } }],
      "boost": 0.05 } },
  "knn": {
      "field": "q_1024_vec", "k": 1024, "num_candidates": 2048,
      "query_vector": [0.0236, -0.0271, 0.0423, …],
      "filter": { "bool": { "must": [{ "query_string": "……和上面一模一样……" }] } },
      "similarity": 0.1 },
  "size": 128
}
```

**解释：** ES 会分别算关键词分和向量分，然后**相加**。关键词分乘了 0.05，所以这一步的排序**基本由向量决定**。

> **问题一：向量检索被关键词“卡住”了。** 看代码里标“注意这里”的那行：`filter=bqry.to_dict()` 把关键词查询也塞进了向量检索的过滤条件。结果是**只有关键词至少匹配 30% 的块，才有资格参加向量检索**。用户换了个说法（问“赚钱能力”，原文写“毛利率”），关键词对不上，向量检索本该靠语义找回来，但这些块已经被过滤掉了。修法：`filter` 里只放 `kb_id` 这类结构化条件，不放 `query_string`。

> **问题二：ES 把中文拆成了单字。** 1.6 提到索引是 ES 自动建的。实测 `content_ltks` 字段用的是 ES 默认的 `standard` 分析器，它不认识中文词，会把每个汉字拆开：
>
> ```
> 代码分好的词： 国电电力 营业 收入 增长
> ES 实际存的：  国 电 电 力 营 业 收 入 增 长
> ```
>
> 所以 2.3 精心构造的“国电电力”，到了 ES 里变成了 4 个单字分别匹配，精度明显下降。`mapping.json` 里本来设计的是 `whitespace` 分析器（按空格切，保留代码分好的词）。修法：写入前用 `mapping.json` 建索引；改完要删掉旧索引、重新上传文档。
>
> 向量字段 `q_1024_vec` 被 ES 正确识别成了向量类型，这部分没问题。

---

## 2.5 重排给候选重新打分

**做什么：** 对 128 条候选，用“关键词匹配度 + 重排模型分”重新打分。

**代码（精简，省略了 `important_kwd` 和排序特征）：** [nlp/search_v2.py 第 268 行](../backend/app/engine/rag/nlp/search_v2.py#L268)

```python
def rerank_by_model(self, sres, query, tkweight=0.3, vtweight=0.7, ...):
    _, keywords = self.qryr.question(query)                       # 问题的关键词

    ins_tw = []
    for i in sres.ids:                                            # 每条候选的词 = 正文词 + 标题词
        ins_tw.append(sres.field[i]["content_ltks"].split() + sres.field[i].get("title_tks", "").split())

    tksim = self.qryr.token_similarity(keywords, ins_tw)          # 关键词匹配度：问题的词有多少出现在块里
    vtsim, _ = rerank_similarity(query, [rmSpace(" ".join(tks)) for tks in ins_tw])   # 重排模型打分

    return tkweight * np.array(tksim) + vtweight * vtsim, tksim, vtsim
```

2.2 里传入的 `vector_similarity_weight=0.6`，所以这里 `tkweight=0.4`、`vtweight=0.6`：

```
最终分 = 0.4 × 关键词匹配度 + 0.6 × 重排模型分
```

重排模型的调用在 [nlp/model.py 第 43 行](../backend/app/engine/rag/nlp/model.py#L43)：把问题和所有候选一起发给阿里云百炼的 `qwen3.7-text-rerank`，模型对每一对“问题—候选”给出 0～1 的相关度。

**解释：**

- **关键词匹配度**只看词有没有出现，不理解意思。
- **重排模型**理解整句话的意思，能区分“润本的毛利率”和“国电的毛利率”。
- 返回值里叫 `vector_similarity` 的那一项，**其实是重排模型分**，不是向量相似度，名字有误导性。

**实测：** 写文档时恰好碰到一次重排接口超时，于是有了一组对照。同一个问题“润本股份的毛利率是多少”：

**重排失败时**（重排分全是 0，只剩关键词）：

| 排名 | 总分 | 来源 | 内容 |
|---|---|---|---|
| 1 | 0.25 | 润本股份 | 25H1公司毛利率同比+0.4pct至59.0%… |
| 2 | 0.20 | **国电电力** | 图5：国电电力毛利率、净利率变化情况… |
| 3 | 0.20 | **国电电力** | 2025年上半年，公司毛利率为16.27%… |
| 4 | 0.16 | 润本股份 | 附录：财务预测摘要资产负债表… |
| 5 | 0.15 | 润本股份 | 会计年度 2024 2025E… |

**重排正常时：**

| 排名 | 总分 | 来源 | 内容 |
|---|---|---|---|
| 1 | 0.79 | 润本股份 | 25H1公司毛利率同比+0.4pct至59.0%… |
| 2 | 0.66 | 润本股份 | 附录：财务预测摘要资产负债表… |
| 3 | 0.44 | 润本股份 | 会计年度 2024 2025E… |
| 4 | 0.43 | 国电电力 | 关键财务与估值指标… |
| 5 | 0.37 | 国电电力 | 2025年上半年，公司毛利率为16.27%… |

重排失败时，关键词“毛利率”在国电的研报里也有，所以国电的内容排到了第 2、3 名，大模型很可能拿国电的数据回答润本的问题。

> **注意：重排失败时不报错。** 看 [model.py 第 80 行](../backend/app/engine/rag/nlp/model.py#L80)：失败时只打印一行日志，返回全 0 的分数，系统照常运行。如果回答里混进了别的公司的数据，先搜后端日志里的 `重排失败`。

---

## 2.6 拼提示词

**做什么：** 把检索到的 5 块、会话临时文档、用户问题拼成一段提示词。

**代码（精简）：** [services/chat.py 第 295 行](../backend/app/services/chat.py#L295)

```python
reference_parts = []
reference_id = 1

# 知识库检索到的块，从 [1] 开始编号
if retrieved_content:
    refs = []
    for ref in retrieved_content:
        refs.append(f"[{reference_id}] {ref['content_with_weight']}")
        reference_id += 1
    reference_parts.append("**知识库内容：**\n" + "\n".join(refs))

# 会话里临时上传的小文档（从 Redis 读），截到 4000 字，作为一条
if quick_parse_content:
    reference_parts.append(f"**当前会话文档内容：**\n[{reference_id}] {quick_parse_content[:4000]}")
```

[第 325 行](../backend/app/services/chat.py#L325) 把它们拼进模板：

```python
prompt = f"""
你是一个专业的智能助手，擅长基于提供的参考资料回答用户问题。请遵循以下原则：

**回答要求：**
1. 优先基于参考内容回答，确保答案准确可靠
2. 在回答中，每一块内容都必须标注引用的来源，格式为：##引用编号$$。例如：##1$$ 表示引用自第1条参考内容。
……
**参考内容：**
{formatted_references}

**用户问题：**
{question}
"""
```

**解释：**

- 大模型被要求在回答里写 `##1$$` 这样的标记，表示“这句话来自第 1 条参考”。前端会把它变成可点的角标（2.9）。
- **会话临时文档不参与检索**，整篇（截到 4000 字）直接塞进提示词。所以它适合“帮我总结这份材料”；知识库适合“在一堆资料里找答案”。

> **注意：只支持单轮问答。** 整个提示词只有这一轮的问题，没有历史对话。用户追问“那它的净利率呢”，模型不知道“它”是谁。

---

## 2.7 调大模型并流式返回

**做什么：** 调用大模型，每收到一小段就推给前端；结束后生成推荐问题、写数据库。

**代码（精简）：** [services/chat.py 第 354 行](../backend/app/services/chat.py#L354)

```python
completion = client.chat.completions.create(model="gpt-6.1-sol", messages=[{"role": "user", "content": prompt}], stream=True)

# 第一条消息：把参考内容发给前端（用于显示来源和引用）
yield f"event: message\ndata: {json.dumps({'documents': all_documents})}\n\n"

for chunk in completion:
    if chunk.choices[0].finish_reason == "stop":                       # 回答结束
        recommended_questions = generate_recommended_questions(question, ...)
        yield f"event: message\ndata: {json.dumps({'recommended_questions': recommended_questions})}\n\n"
        yield "event: end\ndata: [DONE]\n\n"
        write_chat_to_db(session_id, question, model_answer, all_documents, ...)   # 存 messages 表
        update_session_name(session_id, question, user_id)                         # 首轮：让大模型给会话起名
        break
    else:                                                               # 回答进行中
        content = chunk.choices[0].delta.content or ""
        model_answer += content
        yield f"event: message\ndata: {json.dumps({'role': 'assistant', 'content': content, 'thinking': False})}\n\n"
```

**解释：** 前端按顺序会收到这些消息：

```
event: message   data: {"documents": [...5 块 + 临时文档...]}        ← 先发参考内容
event: message   data: {"role":"assistant","content":"润本","thinking":false}
event: message   data: {"role":"assistant","content":"股份","thinking":false}
……（一个字一个字）……
event: message   data: {"recommended_questions": ["…", "…", "…"]}    ← 再发 3 个推荐问题
event: end       data: [DONE]                                        ← 结束
```

- 如果模型返回了思考过程（`reasoning_content`），会以 `thinking: true` 发出，前端单独显示。
- **推荐问题要等一次额外的大模型调用**，所以回答完后还要等一两秒才结束。
- **会话名是在第一轮问答结束后才写库的**。只打开没提问的会话，不会出现在历史列表里。

---

## 2.8 前端读流

**做什么：** 一行一行读后端推来的数据，拼成完整的回答。

**代码（伪代码，“按 document_id 去重”那一行是对原代码的概括）：** [frontend/src/pages/chat/index.tsx 第 194 行](../frontend/src/pages/chat/index.tsx#L194)

```tsx
function parseData(slice: string) {
  const str = slice.replace(/^data\: /, '').trim()
  if (str === '[DONE]') return

  const json = JSON.parse(str)

  if (json?.content) {                                   // 回答片段：拼起来
    if (json.thinking) target.think = (target.think || '') + json.content
    else               target.content = (target.content || '') + json.content
  }

  if (json?.documents?.length) {                         // 参考内容
    target.reference = json.documents                    //   原样保存，引用角标按下标取它
    target.documents = 按 document_id 去重(json.documents) //   右侧"来源"面板显示它
  }

  if (json?.recommended_questions?.length) {             // 推荐问题
    target.recommended_questions = json.recommended_questions
  }
}
```

**解释：**

- 前端只看以 `data: ` 开头的行，**不看 `event:` 行**。
- `target` 是用 valtio 包装的响应式对象，直接改它的属性，界面就会自动刷新。

> **注意：** 因为不看 `event:`，后端出错时发的 `event: error` 消息（内容是 `{"role":"error","content":"错误信息"}`）也有 `content` 字段，会被**当成回答正文拼进去**显示。

---

## 2.9 前端渲染引用角标

**做什么：** 把回答里的 `##1$$` 变成可以点击的引用角标，点击后显示对应的原文。

**代码（精简）：** [frontend/src/pages/chat/component/result.tsx 第 43 行](../frontend/src/pages/chat/component/result.tsx#L43)

```tsx
{
  name: 'reference',
  tokenizer(src) {
    const match = /^##(\d+?)\$\$/.exec(src)        // 匹配 ##数字$$
    if (match) return { type: 'reference', raw: match[0], index: match[1] }
  },
  renderer(token) {
    const index = token.index
    return `<span data-refrence-index="${index}">[${Number(index) + 1}]</span>`   // 显示成 [数字+1]
  },
}
```

点击时 [chat-message.tsx 第 106 行](../frontend/src/pages/chat/component/chat-message.tsx#L106)：

```tsx
onRefrence={(index) => {
  const target = item.reference?.[index]          // 按下标取：reference[数字]
  if (target) onRefrence?.(target)
}}
```

> **问题：编号错位一位。** 把三处代码放在一起看：
>
> | 位置 | 第一条参考 |
> |---|---|
> | 提示词（2.6） | 编号 `[1]`，模型写 `##1$$` |
> | 角标显示（本节） | `1 + 1` → 显示 `[2]` |
> | 点击取值（本节） | `reference[1]` → 取到的是**第二条** |
>
> 用户看到的是 `[2]`，点开的是第二条，但模型说的是第一条。修法：提示词改成从 `[0]` 开始编号（RAGFlow 原版就是这样），前端不用动。
>
> 另外，会话临时文档在提示词里算一条，在 `documents` 里却按 2000 字拆成了多条，也会让它后面的编号对不上。

---

# 附录

## A. 中文分词器是怎么分词的

分词器在 [engine/rag/nlp/rag_tokenizer.py](../backend/app/engine/rag/nlp/rag_tokenizer.py)，1.5 写入和 2.3 检索用的都是它。

**词典：** [engine/rag/res/huqie.txt](../backend/app/engine/rag/res/huqie.txt)，55 万个词，每行“词 频次 词性”：

```
金童云商 3 nr
低速 176 d
```

**算法（伪代码）：** [`tokenize()` 第 266 行](../backend/app/engine/rag/nlp/rag_tokenizer.py#L266)

```python
def tokenize(self, line):
    line = self._strQ2B(line).lower()          # 全角转半角、小写
    line = self._tradi2simp(line)              # 繁体转简体

    for L in re.split(self.SPLIT_CHAR, line):  # 按标点、英文、数字切成片段
        tks,  s  = self.maxForward_(L)         # 正向最大匹配：从左往右，每次取最长的词
        tks1, s1 = self.maxBackward_(L)        # 逆向最大匹配：从右往左
        # 两种结果一样的部分：直接用
        # 不一样的部分：用 dfs_ 枚举所有切法，取得分最高的
```

不一样的部分怎么打分？[`score_()` 第 179 行](../backend/app/engine/rag/nlp/rag_tokenizer.py#L179)：

```python
def score_(self, tfts):
    B = 30
    F, L, tks = 0, 0, []
    for tk, (freq, tag) in tfts:
        F += freq                        # 所有词的频次（对数）之和：词越常见越好
        L += 0 if len(tk) < 2 else 1     # 长度 ≥2 的词的个数
        tks.append(tk)
    L /= len(tks)                        # 长词占比：长词越多越好
    return tks, B / len(tks) + L + F     # B / 词数：词数越少越好
```

**实测：**

| 输入 | 输出 |
|---|---|
| 请问国电电力2023年的营业收入增长了多少？ | `请问 国电电力 2023 年 的 营业 收入 增长 了 多少` |
| 中华人民共和国成立于1949年，Apple发布了iPhones | `中华人民共和国 成立 于 1949 年 appl 发布 了 iphon` |

英文会做词干提取（`Apple` → `appl`），这样 `iPhone` 和 `iPhones` 能匹配上。

**细粒度分词** [`fine_grained_tokenize()` 第 339 行](../backend/app/engine/rag/nlp/rag_tokenizer.py#L339)：对 3～10 个字的词再拆一次，取得分第二高的切法。`国电电力` → `国电 电力`，`毛利率` → `毛 利率`。

---

## B. 想调效果，改哪里

| 想要 | 改什么 | 位置 |
|---|---|---|
| 块更大或更小 | `chunk_token_num`，默认 128 | [naive.py 第 205 行](../backend/app/engine/rag/app/naive.py#L205) |
| 给模型更多或更少参考 | `page_size`，默认 5 | [retrieval.py 第 22 行](../backend/app/services/retrieval.py#L22) |
| 更看重语义或关键词 | `vector_similarity_weight`，默认 0.6 | [retrieval.py 第 20 行](../backend/app/services/retrieval.py#L20) |
| 过滤掉不相关的块 | `similarity_threshold`，默认 0.1，可试 0.3 | [search_v2.py 第 297 行](../backend/app/engine/rag/nlp/search_v2.py#L297) |
| 粗筛时关键词多占一点 | 融合权重 `0.05, 0.95` | [search_v2.py 第 111 行](../backend/app/engine/rag/nlp/search_v2.py#L111) |
| 关键词匹配更宽松 | `min_match=0.3` | [search_v2.py 第 100 行](../backend/app/engine/rag/nlp/search_v2.py#L100) |
| 文件名和正文的权重 | `title_tks^10`、`content_ltks^2` | [query.py 第 29 行](../backend/app/engine/rag/nlp/query.py#L29) |
| 换对话模型 | `model="gpt-6.1-sol"`，共三处 | [chat.py 第 355 行](../backend/app/services/chat.py#L355) |

**改了切块、分词、索引相关的参数后，已经上传的文档不会自动更新**，要在知识库页删掉重传。

---

## C. 自己动手跑一遍

后端容器在运行时，下面的命令可以直接复制执行，只读不写。

**看一个问题会被分成哪些词、变成什么检索语句：**

```bash
docker exec -w /app swxy_api python -c "
import logging; logging.disable(logging.CRITICAL)
from engine.rag.nlp import rag_tokenizer
from engine.rag.nlp.query import FulltextQueryer
q = '请问国电电力2023年的营业收入增长了多少？'
print('分词:', rag_tokenizer.tokenize(q))
m, kw = FulltextQueryer().question(q, min_match=0.3)
print('检索语句:', m.matching_text)
"
```

**看一个问题能检索到哪 5 块、各自得多少分：**

```bash
docker exec -w /app swxy_api python -c "
import logging; logging.disable(logging.CRITICAL)
from services.retrieval import dealer
r = dealer.retrieval(question='润本股份的毛利率是多少', embd_mdl=None, tenant_ids='1',
                     kb_ids=None, vector_similarity_weight=0.6, page=1, page_size=5)
for c in r['chunks']:
    print('总分', round(float(c['similarity']),2), '关键词', round(float(c['term_similarity']),2),
          '重排', round(float(c['vector_similarity']),2), c['content_with_weight'][:40].replace(chr(10),' '))
"
```

`tenant_ids='1'` 是用户 ID，换成你自己的。

**看 ES 是怎么切词的（验证 2.4 的问题二）：**

```bash
docker exec swxy_api python -c "
from elasticsearch import Elasticsearch
es = Elasticsearch(['http://es01:9200'], basic_auth=('elastic','infini_rag_flow'))
r = es.indices.analyze(index='1', body={'field':'content_ltks','text':'国电电力 营业 收入'})
print([t['token'] for t in r['tokens']])
"
```

现在会输出单字。修好之后应该输出 `['国电电力', '营业', '收入']`。

**检查重排接口是否正常：**

```bash
docker exec -w /app swxy_api python -c "
from engine.rag.nlp.model import rerank_similarity
print(rerank_similarity('润本股份的毛利率是多少', ['公司毛利率同比+0.4pct至59.0%', '沪深300']))
"
```

正常输出一组 0～1 之间的数；如果全是 0 并打印“重排失败”，就是密钥、额度或网络的问题。

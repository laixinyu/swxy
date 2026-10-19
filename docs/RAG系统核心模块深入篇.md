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

**做什么：** 校验文件、保存到磁盘、写一条“排队中”的记录、放进任务队列，然后**立即返回**。解析不在这个请求里做。

**代码（精简）：** [api/chat.py 第 146 行](../backend/app/api/chat.py#L146)

```python
@router.post("/upload_files")
def upload_files(files: List[UploadFile] = File(...), user_id: str = Depends(current_user_id), db = Depends(get_db)):
    for file in files:
        file_name = storage.safe_filename(file.filename)                        # 去掉路径部分和控制字符
        content = storage.read_upload(file, settings.max_upload_bytes)          # 超过 10MB 立即中止
        doc = document_service.create_document(db, user_id, file_name, content)
```

**代码（精简）：** [services/documents.py 第 54 行](../backend/app/services/documents.py#L54)

```python
def create_document(db, user_id, file_name, content):
    ext = storage.file_extension(file_name)
    if ext not in settings.allowed_extension_set: raise HTTPException(400, ...)   # 扩展名白名单
    storage.validate_content(ext, content)                                       # 文件头和扩展名必须对得上

    doc_id = uuid.uuid4().hex
    relative = storage.document_path(user_id, doc_id, ext)     # 磁盘路径只由 ID 决定：{用户}/{文档ID}.pdf
    storage.save_file(relative, content)
    db.add(Document(id=doc_id, user_id=user_id, kb_id=user_id, file_name=file_name,
                    storage_path=relative, status=DocumentStatus.PENDING, ...))
    db.commit()
    enqueue_ingest(doc_id)                                     # 放进 Redis 队列，由 worker 处理
```

**解释：**

- **接口用的是普通** `def`**，不是** `async def`**。** FastAPI 会把普通函数放进线程池执行；如果在 `async def` 里直接做读文件、查数据库这类同步操作，会卡住整个进程，所有用户都得排队。
- **用户给的文件名只用于展示。** 磁盘上的文件名是文档 ID，所以文件名里写 `../../` 也写不到别的目录。
- **同一用户不能有同名文件**，由数据库的唯一约束 `uq_documents_user_file` 保证。
- `kb_id` 目前就是用户 ID。检索时按它过滤，不同用户的文档存在同一个 ES 索引里，但互相查不到。

真正的解析在 worker 进程里，入口是 [services/ingestion.py 第 102 行](../backend/app/services/ingestion.py#L102) 的 `ingest_document()`：

```python
def ingest_document(doc_id):
    _set_status(doc_id, DocumentStatus.PARSING)
    binary = absolute_path(doc.storage_path).read_bytes()
    items = chunk(doc.file_name, binary=binary, ...)            # 1.2 ~ 1.5：解析、切块、分词
    embeddings = generate_embedding([...])                      # 1.6：向量化
    es_docs = build_es_documents(doc, items, embeddings)        # 1.6：组装
    es.delete_by_doc(index, doc.id)                             # 先清掉这个文档的旧切片，重复执行结果一样
    es.insert(...); 
    es.refresh(index)
    _set_status(doc_id, DocumentStatus.DONE, chunk_count=len(es_docs))
```

出错时会清掉已经写入的切片，把状态改成 `failed` 并记下原因（知识库页上能看到），然后由任务队列自动重试，最多 2 次。如果是**重新解析**失败、而且旧切片还没被替换，就保留上次的结果：状态仍是“已完成”，旁边显示 ⚠，鼠标移上去能看到失败原因。

**解析进度**由 [`ProgressReporter`](../backend/app/services/ingestion.py#L37) 写回数据库：解析器自己报告的进度映射到 5%～70%，生成向量是 72%，写入索引是 90%，完成是 100%。为了不每页都写一次库，最多每秒更新一次。前端知识库页每 3 秒刷新，显示“解析中 45%”，鼠标移上去能看到当前阶段（识别文字、分析版面、生成向量……）。

**重新解析**：失败或已完成的文档可以在知识库页点“重新解析”（[`POST /documents/{id}/reparse`](../backend/app/api/history.py#L40)）。旧切片要等新的解析和向量化都成功后才会被替换，期间在“全部文档”范围下仍然能检索到；如果会话限定了检索范围，没解析完的文档会暂时排除。

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

> **注意：** `filename` 要传纯文件名，不能带路径。`title_tks` 由它分词而来，检索时权重是正文的 5 倍。以前传的是完整存储路径，`title_tks` 变成了 `app engin storag file 1 润本股份`；现在 worker 传的是 `doc.file_name`，实测为 `润本股份`。

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

**最后的** `_line_tag` 给每段文字末尾加一个位置标签，形如 `@@3\t72.1\t523.4\t218.5\t230.0##`（页码、左、右、上、下），后面切块时会用它算出每块的页码。

---

## 1.4 把段落合并成块

**做什么：** 解析出来的段落有长有短，把它们合并成长度差不多的块。

**代码（精简，省略了位置标签的处理）：** [engine/rag/nlp/init.py 第 86 行](../backend/app/engine/rag/nlp/__init__.py#L86)

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
3. `delimiter` **参数没用到。** 函数签名里有，函数体里没用。

**实测：** 两份研报的块长度（单位 token）：

| 文档 | 块数 | 最短 | 中位数 | 最长 |
|---|---|---|---|---|
| 润本股份.pdf | 22（含 7 张表） | 51 | 251 | 1582 |
| 国电电力.pdf | 38（含 7 张表） | 38 | 201 | 1257 |

中位数是 128 的两倍左右。最长的块都是财务报表：表格不走 `naive_merge`，一张表无论多大都是一块。

---

## 1.5 给每块分词

**做什么：** 每块除了保存原文，还要保存分好词的版本，用于关键词检索。

**代码：** [engine/rag/nlp/init.py 第 188 行](../backend/app/engine/rag/nlp/__init__.py#L188)

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

> **注意：** PDF 的块在这一步还会用 1.3 里的位置标签算出页码（`page_num_int`）和坐标（`position_int`），见 `tokenize_chunks()` [第 280 行](../backend/app/engine/rag/nlp/__init__.py#L280)。下一步会把它们原样写进 ES，前端引用卡片上的页码就来自这里。

---

## 1.6 组装成 ES 文档并写入

**做什么：** 给每块算向量，拼成 ES 能存的字典，批量写入。

**代码：** [services/ingestion.py 第 82 行](../backend/app/services/ingestion.py#L82)

```python
# 切块阶段算好、需要原样写入 ES 的字段
CHUNK_FIELDS = (
    "content_with_weight", "content_ltks", "content_sm_ltks",
    "title_tks", "title_sm_tks", "page_num_int", "position_int", "top_int",
)

def build_es_documents(doc, items, embeddings):
    for i, (item, embedding) in enumerate(zip(items, embeddings)):
        d = {field: item[field] for field in CHUNK_FIELDS if item.get(field) not in (None, "", [])}
        d.update({
            "id": xxhash.xxh64(f"{doc.id}:{i}:{item['content_with_weight']}".encode()).hexdigest(),
            "doc_id": doc.id,                    # 删除、过滤都按它
            "kb_id": doc.kb_id,                  # 检索时按它隔离用户
            "docnm_kwd": doc.file_name,
            "available_int": 1,
            f"q_{len(embedding)}_vec": embedding,   # 向量，字段名是 q_1024_vec
            ...
        })
```

向量化在 [nlp/model.py 第 103 行](../backend/app/engine/rag/nlp/model.py#L103)：每 10 块一批调用阿里云百炼，单批失败会自动重试，间隔 1 秒、2 秒。

**解释：**

- 块 ID 由“文档 ID + 序号 + 原文”算出，同一个文档重新入库时 ID 不变；不同文档里内容相同的段落也不会互相覆盖。
- 页码、坐标这些字段由切块阶段算好，这里**统一按字段清单带上**，不再一个个手写，所以不会漏。

**写入前先按** `mapping.json` **建索引。** [es_conn.py 第 56 行](../backend/app/engine/rag/utils/es_conn.py#L56)：

```python
def ensure_index(self, indexName):
    """按 conf/mapping.json 建索引。不能依赖写入时自动建索引，否则分词字段会被默认分析器拆成单字。"""
    if not self.es.indices.exists(index=indexName):
        self.es.indices.create(index=indexName, settings=self.mapping["settings"],
                               mappings=self.mapping["mappings"])
```

`mapping.json` 规定了每类字段怎么存：`*_ltks`、`*_tks` 用按空格切的 `whitespace` 分析器，保留分词器分好的词；`*_kwd`、`*_id` 存成精确匹配的 `keyword`；`*_1024_vec` 存成向量。

**实测：** 在运行中的索引上查到的映射

| 字段 | 类型 |
|---|---|
| `content_ltks` | `text`，分析器 `whitespace` |
| `title_tks` | `text`，分析器 `whitespace`，相似度 `scripted_sim` |
| `kb_id`、`doc_id`、`docnm_kwd` | `keyword` |
| `page_num_int`、`position_int` | `integer` |
| `q_1024_vec` | `dense_vector`，余弦相似度 |

60 个块全部带有页码，例如 `{'page_num_int': [2], 'position_int': [[2, 63, 282, 338, 475]]}`。

> **以前的问题：** 原来的代码只加载了 `mapping.json`，从来没用它建索引，索引是第一次写入时 ES 按默认规则自动建的。结果 `content_ltks` 用了默认的 `standard` 分析器，`国电电力` 在 ES 里被拆成了 `国/电/电/力`。原来的组装代码也是手写字段，漏掉了页码和坐标。

---

# 第二部分：回答一个问题

## 2.1 接口收到问题

**做什么：** 检查会话归属，结合历史对话改写问题，在检索范围内检索，再把检索结果交给大模型，以流的形式返回。

**代码（精简）：** [api/chat.py 第 109 行](../backend/app/api/chat.py#L109)

```python
@router.post("/chat_on_docs")
def chat_on_docs(session_id: str = Query(...), request: ChatRequest = Body(...),
                 user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    session = get_owned_session(db, session_id, user_id, create=True)   # 不是自己的会话 → 404
    question = request.message.strip()                                   # 1～4000 个字符

    # 多轮对话：带上最近几轮历史，并把追问改写成可以单独检索的问题
    history = history_messages(list_messages(db, session_id))
    retrieval_query = rewrite_question(question, history)

    # 检索范围：只用还存在、已解析完成的文档。一个都不剩时不检索，避免悄悄扩大到全部文档
    scope = get_scope(session)
    doc_ids = None
    if scope:
        doc_ids = [doc.id for doc in document_service.owned_documents(db, user_id, scope)
                   if doc.status == DocumentStatus.DONE]

    references = []
    if doc_ids is None or doc_ids:
        references = retrieve_content(user_id, retrieval_query, doc_ids)    # 用改写后的问题检索

    return StreamingResponse(stream_chat(session_id, question, references, need_name,
                                         history=history, retrieval_query=retrieval_query),
                             media_type="text/event-stream")
```

**解释：**

- **会话归属校验**在 [services/session.py 第 26 行](../backend/app/services/session.py#L26)。所有按会话访问的接口（问答、历史消息、检索范围、临时文档）都先过这一关，不是自己的会话一律返回 404，连“这个会话存在”都不透露。
- **问题改写**在 [services/conversation.py 第 44 行](../backend/app/services/conversation.py#L44)，下面单独讲。
- **检索范围**存在会话上（`sessions.doc_ids`），由 [`PUT /sessions/{id}/scope`](../backend/app/api/chat.py#L59) 设置。设置时会校验每个文档 ID 都属于当前用户，别人的文档 ID 放不进来。
- 检索失败**不会中断回答**，只是参考内容为空，但会在日志里留下完整的报错。

### 追问为什么要先改写

用户先问“青松科技上半年营业收入是多少”，接着问“那它的净利率呢？”。第二个问题如果直接拿去检索，里面根本没有“青松科技”，关键词和向量都对不上。

[`rewrite_question()`](../backend/app/services/conversation.py#L44) 把最近几轮对话和新问题一起交给大模型，要求它补全代词和省略的部分，返回一个能单独理解的问题：

```python
REWRITE_PROMPT = """你负责把用户在多轮对话中的最新问题，改写成一个不依赖上下文、可以单独拿去检索资料的问题。
1. 把代词和省略的内容补全，例如“它”“那家公司”“净利率呢”要补成具体的对象和指标。
2. 保留原问题的意图、时间范围和专有名词，不要回答问题，也不要添加对话里没有的信息。
3. 如果最新问题本身已经完整，原样返回。
……"""
```

**实测：**

| 用户原问题 | 改写后（实际检索用的） |
|---|---|
| 那它的净利率呢？（上一轮问的是青松科技） | 青松科技2025年上半年的净利率是多少？ |
| 那净利率呢？（上一轮问的是润本股份毛利率） | 润本股份上半年净利率是多少？ |
| 国电电力的装机容量有多大？ | 国电电力的装机容量有多大？（本身完整，原样返回） |

几个设计要点：

- **第一轮不改写**，没有历史就直接用原问题，不多花一次调用。
- **改写失败就用原问题**，不影响回答，只写一条告警日志。
- **改写后的问题会推给前端**，显示在回答上方（“按……检索”），也保存在 `messages.retrieval_query`，方便排查“为什么检索到了这些”。
- **代价**：每次追问多一次大模型调用，实测 2～6 秒。可以设 `REWRITE_MODEL` 换一个更快的小模型专门做改写。

---

## 2.2 检索入口

**做什么：** 调检索核心 `Dealer.retrieval()`，只保留前端需要的字段。

**代码：** [services/retrieval.py 第 13 行](../backend/app/services/retrieval.py#L13)

```python
def retrieve_content(kb_id, question, doc_ids=None):
    results = dealer.retrieval(
        question=question,
        tenant_ids=settings.es_index,                               # 所有用户共用一个索引 swxy_chunks
        kb_ids=[kb_id],                                             # 只查当前用户的块
        doc_ids=doc_ids or None,                                    # 限定检索范围：只查这些文档
        page=1, page_size=settings.retrieval_top_k,                 # 只要前 5 条
        similarity_threshold=settings.similarity_threshold,         # 低于 0.1 的丢掉
        vector_similarity_weight=settings.vector_similarity_weight, # 重排阶段：重排模型分占 60%
    )
    return [{"document_id": c["doc_id"], "document_name": c["docnm_kwd"],
             "content_with_weight": c["content_with_weight"], "positions": c["positions"]}
            for c in results["chunks"]]
```

`Dealer.retrieval()` **内部分三步：**

**代码（精简）：** [nlp/search_v2.py 第 310 行](../backend/app/engine/rag/nlp/search_v2.py#L310)

```python
def retrieval(self, question, ..., page_size, similarity_threshold=0.1, vector_similarity_weight=0.3, ...):
    req = {"question": question, "kb_ids": kb_ids, "size": max(page_size * 3, settings.retrieval_candidates), ...}

    sres = self.search(req, [index_name(tid) for tid in tenant_ids], kb_ids, ...)  # 第一步：查 ES，取 128 条候选

    sim, tsim, vsim = self.rerank_by_model(sres, question,                          # 第二步：重排打分
                                           1 - vector_similarity_weight, vector_similarity_weight)
    idx = np.argsort(sim * -1)[0:page_size]                                         # 按分数从高到低排

    for i in idx:                                                                   # 第三步：取前 5，低于阈值的丢掉
        if sim[i] < similarity_threshold:
            break
        ranks["chunks"].append({..., "similarity": sim[i], "term_similarity": tsim[i], "vector_similarity": vsim[i]})
```

**解释：** 先粗筛（ES 返回 128 条），再精排（重排模型逐条打分），最后取前 5。下面 2.3、2.4 讲粗筛，2.5 讲精排。

`kb_ids` 和 `doc_ids` 都会变成 ES 的 `terms` 过滤条件，同时作用在关键词检索和向量检索上（2.4 里的 `knn_filter`）。所以限定范围后，范围外的文档在两路检索里都不会出现。

所有参数都在 [core/config.py](../backend/app/core/config.py#L79)，改 `.env` 就能调，见 [附录 B](#b-想调效果改哪里)。

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

**问题里的词出现在文件名里，得分是出现在正文里的 5 倍。** 另外 `important_`*、`question_*`、`title_sm_tks` 这几个字段本系统从没写入过，查了也是空。

> **注意：** 上面“国电电力”的权重只排第三，比“2023”“营业”都低，这不太合理。原因是权重计算依赖的两个词典 `ner.json`（实体词典，会给公司名 ×3）和 `term.freq`（文档频率）不在 `engine/rag/res/` 里。权重公式见 [term_weight.py 第 223 行](../backend/app/engine/rag/nlp/term_weight.py#L223)。

---

## 2.4 关键词和向量一起查 ES

**做什么：** 把问题也转成向量，和 2.3 的关键词语句一起发给 ES，取 128 条候选。

**代码：** [nlp/search_v2.py 第 113 行](../backend/app/engine/rag/nlp/search_v2.py#L113)

```python
matchText, keywords = self.qryr.question(qst, min_match=0.3)          # 2.3 的关键词语句，至少匹配 30%
matchDense = self.get_vector(qst, emb_mdl, topk, 0.1)                 # 问题转向量，相似度下限 0.1
fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05, 0.95"})   # 关键词占 5%，向量占 95%
res = self.dataStore.search(src, ..., [matchText, matchDense, fusionExpr], ...)

if total == 0:                                                        # 一条都没查到
    matchText, _ = self.qryr.question(qst, min_match=0.1)             #   → 放宽到 10% 再查一次
```

`dataStore.search()` 把这三样翻译成 ES 查询：

**代码（精简）：** [utils/es_conn.py 第 240 行](../backend/app/engine/rag/utils/es_conn.py#L240)

```python
# kb_id、available_int 等结构化条件已经放进了 bqry.filter
# kNN 只能带结构化过滤。如果把下面的 query_string 也放进去，
# 只有关键词先命中的块才有资格参加向量检索，换了说法的问题就找不回来。
knn_filter = Q("bool", filter=list(bqry.filter)).to_dict() if bqry.filter else None

for m in matchExprs:
    if isinstance(m, MatchTextExpr):                        # 关键词部分
        bqry.must.append(Q("query_string", fields=m.fields, query=m.matching_text, ...))
        bqry.boost = 1.0 - vector_similarity_weight         # 1 - 0.95 = 0.05

    elif isinstance(m, MatchDenseExpr):                     # 向量部分
        s = s.knn(m.vector_column_name, m.topn, m.topn * 2,
                  query_vector=list(m.embedding_data), similarity=similarity,
                  filter=knn_filter)                        # 只带结构化过滤
```

发给 ES 的请求结构是这样的（截取主干）：

```json
{
  "query": { "bool": {
      "must":   [{ "query_string": { "query": "(毛利率 OR \"利率\" …)^0.65 (润本股份 OR \"股份\" …)^0.35 …",
                                     "minimum_should_match": "30%" } }],
      "filter": [{ "terms": { "kb_id": ["1"] } }, …],
      "boost": 0.05 } },
  "knn": {
      "field": "q_1024_vec", "k": 1024, "num_candidates": 2048,
      "query_vector": [0.0236, -0.0271, 0.0423, …],
      "filter": { "bool": { "filter": [{ "terms": { "kb_id": ["1"] } }, …] } },
      "similarity": 0.1 },
  "size": 128
}
```

**解释：** 关键词查询和向量查询**各自独立地**找块，ES 把两边的结果合并，分数相加。关键词分乘了 0.05，所以这一步的排序**基本由向量决定**。两边都只在 `kb_id` 属于当前用户的块里找。

> **以前的问题：向量检索被关键词“卡住”。** 原来的代码是 `filter=bqry.to_dict()`，把关键词查询也塞进了向量检索的过滤条件，只有关键词至少匹配 30% 的块才有资格参加向量检索。
>
> **实测对比：** 问“婴童护肤品牌的赚钱能力怎么样”，原文里写的是“毛利率”“净利率”，关键词几乎对不上。修复前只有 **8 条**候选进入重排，修复后是 **59 条**（库里一共 60 块）。

---

## 2.5 重排给候选重新打分

**做什么：** 对 128 条候选，用“关键词匹配度 + 重排模型分”重新打分。

**代码（精简，省略了** `important_kwd` **和排序特征）：** [nlp/search_v2.py 第 281 行](../backend/app/engine/rag/nlp/search_v2.py#L281)

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

重排模型的调用在 [nlp/model.py 第 55 行](../backend/app/engine/rag/nlp/model.py#L55)：把问题和所有候选一起发给阿里云百炼的 `qwen3.7-text-rerank`，模型对每一对“问题—候选”给出 0～1 的相关度。

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

> **注意：重排失败时不会中断回答。** 看 [model.py 第 92 行](../backend/app/engine/rag/nlp/model.py#L92)：重排接口失败会先自动重试一次，还失败就返回全 0 的分数，同时写一条告警日志，排序退化成只看关键词分。如果回答里混进了别的公司的数据，先查日志：`docker compose logs swxy_api | grep 重排失败`。

---

## 2.6 拼提示词

**做什么：** 把检索到的 5 块、会话临时文档、用户问题拼成发给大模型的消息。

**代码：** [services/chat.py 第 26 行](../backend/app/services/chat.py#L26)

```python
def build_references(session_id, retrieved):
    """提示词里的编号和返回给前端的 documents 必须一一对应，前端按下标取引用。"""
    references = list(retrieved)
    quick_content = quick_parse.get_content(session_id)        # 会话里临时上传的小文档（Redis）
    if quick_content:
        references.append({"document_name": "当前会话文档", "content_with_weight": quick_content[:4000], ...})
    return references

def format_references(references):
    return "\n\n".join(f"[{i}] 《{ref['document_name']}》\n{ref['content_with_weight']}"
                       for i, ref in enumerate(references))  # 从 [0] 开始编号
```

规则放在系统消息里，[第 17 行](../backend/app/services/chat.py#L17)：

```python
SYSTEM_PROMPT = """你是一个专业的智能助手，擅长基于提供的参考资料回答用户问题。请遵循以下原则：
1. 优先基于参考内容回答，确保答案准确可靠。
2. 参考内容按 [0]、[1]、[2] …… 编号。回答中用到参考内容的地方，要在该句末尾标注来源，
   格式为 ##编号$$，例如 ##0$$ 表示来自第 [0] 条参考内容。
……"""

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": f"参考内容：\n{format_references(references)}\n\n用户问题：\n{question}"},
]
```

**解释：**

- **提示词的编号和返回给前端的参考列表是同一个列表**，`##0$$` 就是列表里的第 0 项。会话临时文档在两边都算一条，编号不会错开（2.9 讲前端怎么用这个编号）。
- **规则放系统消息，资料和问题放用户消息。** 这是对话模型的标准用法，模型对系统消息里的约束遵守得更好。
- **会话临时文档不参与检索**，整篇（截到 4000 字）直接塞进提示词。所以它适合“帮我总结这份材料”；知识库适合“在一堆资料里找答案”。

**多轮对话：历史也要交给模型。** 改写只解决了“检索什么”，回答时模型还要知道前面聊了什么。[`history_messages()`](../backend/app/services/conversation.py#L27) 取最近 5 轮问答，按 user / assistant 交替放在系统消息和本轮问题之间：

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    *history,                                   # 最近 5 轮：user 问题、assistant 回答……
    {"role": "user", "content": f"参考内容：\n……\n\n用户问题：\n{question}"},
]
```

- **历史回答里的引用标记会被去掉。** 旧回答里的 `##0$$` 指向的是那一轮的参考列表，留着的话模型会把它和本轮的 `[0]` 混淆。
- **每条历史回答最多保留 1500 字**，避免几轮之后提示词过长。
- 轮数和长度都可以在 `.env` 里调：`CHAT_HISTORY_TURNS`、`CHAT_HISTORY_ANSWER_CHARS`。

---

## 2.7 调大模型并流式返回

**做什么：** 调用大模型，每收到一小段就推给前端；回答完成后保存对话，再告诉前端结束。

**代码（精简）：** [services/chat.py 第 95 行](../backend/app/services/chat.py#L95)

```python
def stream_chat(session_id, question, retrieved, need_name):
    references = build_references(session_id, retrieved)
    recommended_future = _executor.submit(generate_recommended_questions, question, ...)  # 和回答同时开始生成

    try:
        yield _sse("message", {"documents": references})           # 第一条：参考内容
        stream = get_llm_client().chat.completions.create(model=settings.chat_model, messages=messages, stream=True)
        for chunk in stream:
            content = chunk.choices[0].delta.content or ""
            answer.append(content)
            yield _sse("message", {"role": "assistant", "content": content, "thinking": False})
    except Exception:
        logger.exception(...)
        yield _sse("error", {"error": "生成回答失败，请稍后重试"})   # 不把内部报错发给前端
        return

    recommended = recommended_future.result(timeout=15)            # 这时通常已经生成好了
    yield _sse("message", {"recommended_questions": recommended})
    save_message(db, session_id, question, "".join(answer), references, recommended, ...)   # 先存库
    if need_name:
        _executor.submit(_name_session, session_id, question)      # 会话起名放到后台，不耽误结束
    yield "event: end\ndata: [DONE]\n\n"                          # 再结束
```

**解释：** 前端按顺序会收到这些消息：

```
event: message   data: {"retrieval_query": "青松科技2025年上半年的净利率是多少？"}   ← 追问时才有：改写后的问题
event: message   data: {"documents": [...5 块 + 临时文档...]}        ← 参考内容
event: message   data: {"role":"assistant","content":"润本","thinking":false}
event: message   data: {"role":"assistant","content":"股份","thinking":false}
……（一段一段）……
event: message   data: {"recommended_questions": ["…", "…", "…"]}    ← 再发 3 个推荐问题
event: end       data: [DONE]                                        ← 结束
```

- **推荐问题和回答同时生成。** 只依赖问题和文档名，没必要等回答写完再开始，回答结束时它通常已经好了。
- **先存库，再发** `[DONE]`**。** 前端收到结束信号时，这轮对话一定已经保存了。
- **大模型客户端全进程复用**（[`get_llm_client()`](../backend/app/services/llm.py#L15)），不用每次请求都重新建连接。
- 如果模型返回了思考过程（`reasoning_content`），会以 `thinking: true` 发出，前端单独显示。

---

## 2.8 前端读流

**做什么：** 一行一行读后端推来的数据，拼成完整的回答。

**代码（伪代码，“按 document_id 去重”那一行是对原代码的概括）：** [frontend/src/pages/chat/index.tsx 第 198 行](../frontend/src/pages/chat/index.tsx#L198)

```tsx
function parseData(slice: string) {
  const str = slice.replace(/^data\: /, '').trim()
  if (str === '[DONE]') return

  const json = JSON.parse(str)

  if (json?.error) {                                     // 出错：用错误样式显示
    target.error = json.error
    return
  }

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

- 前端只看以 `data:`  开头的行，**不看** `event:` **行**。
- `target` 是用 valtio 包装的响应式对象，直接改它的属性，界面就会自动刷新。
- 出错时后端发 `{"error": "…"}`，前端识别 `error` 字段，用错误样式显示，不会混进回答正文。

---

## 2.9 前端渲染引用角标

**做什么：** 把回答里的 `##0$$` 变成可以点击的引用角标，点击后显示对应的原文。

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

点击时 [chat-message.tsx 第 114 行](../frontend/src/pages/chat/component/chat-message.tsx#L114)：

```tsx
onRefrence={(index) => {
  const target = item.reference?.[index]          // 按下标取：reference[数字]
  if (target) onRefrence?.(target)
}}
```

**解释：** 把三处放在一起看，第一条参考内容的编号是这样对应的：

| 位置 | 第一条参考 |
|---|---|
| 提示词（2.6） | 编号 `[0]`，模型写 `##0$$` |
| 角标显示（本节） | `0 + 1` → 显示 `[1]` |
| 点击取值（本节） | `reference[0]` → 第一条 |

三处对得上：用户看到 `[1]`，点开的就是模型引用的那一条。

> **以前的问题：编号错位一位。** 原来提示词从 `[1]` 开始编号，前端却按 0 开始取，用户看到 `[2]`、点开的是第二条。会话临时文档在提示词里算一条，返回给前端时却拆成了多条，也会让后面的编号错开。现在两边用的是同一个列表。冒烟测试里专门检查了“第一个引用指向的块确实包含答案”。

---

# 附录

## A. 中文分词器是怎么分词的

分词器在 [engine/rag/nlp/rag_tokenizer.py](../backend/app/engine/rag/nlp/rag_tokenizer.py)，1.5 写入和 2.3 检索用的都是它。

**词典：** [engine/rag/res/huqie.txt](../backend/app/engine/rag/res/huqie.txt)，55 万个词，每行“词 频次 词性”：

```
金童云商 3 nr
低速 176 d
```

**算法（伪代码）：** `tokenize()` [第 266 行](../backend/app/engine/rag/nlp/rag_tokenizer.py#L266)

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

不一样的部分怎么打分？`score_()` [第 179 行](../backend/app/engine/rag/nlp/rag_tokenizer.py#L179)：

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

**细粒度分词** `fine_grained_tokenize()` [第 339 行](../backend/app/engine/rag/nlp/rag_tokenizer.py#L339)：对 3～10 个字的词再拆一次，取得分第二高的切法。`国电电力` → `国电 电力`，`毛利率` → `毛 利率`。

---

## B. 想调效果，改哪里

下面的参数都在 [core/config.py](../backend/app/core/config.py#L79) 里定义，在 `backend/.env` 里设置对应的环境变量（名字不区分大小写），重启后端即可，不用改代码。

| 想要 | 环境变量 | 默认值 |
|---|---|---|
| 块更大或更小 | `CHUNK_TOKEN_NUM` | 128 |
| 给模型更多或更少参考 | `RETRIEVAL_TOP_K` | 5 |
| 重排的候选池大小 | `RETRIEVAL_CANDIDATES` | 128 |
| 更看重语义或关键词 | `VECTOR_SIMILARITY_WEIGHT` | 0.6 |
| 过滤掉不相关的块 | `SIMILARITY_THRESHOLD` | 0.1，可试 0.3 |
| 换对话模型 | `CHAT_MODEL` | `gpt-6.1-sol` |
| 换向量模型 | `EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS` | `qwen3.7-text-embedding`、1024 |

还有几个参数在代码里：

| 想要 | 改什么 | 位置 |
|---|---|---|
| 粗筛时关键词多占一点 | 融合权重 `0.05, 0.95` | [search_v2.py 第 124 行](../backend/app/engine/rag/nlp/search_v2.py#L124) |
| 关键词匹配更宽松 | `min_match=0.3` | [search_v2.py 第 113 行](../backend/app/engine/rag/nlp/search_v2.py#L113) |
| 文件名和正文的权重 | `title_tks^10`、`content_ltks^2` | [query.py 第 29 行](../backend/app/engine/rag/nlp/query.py#L29) |

**改了切块、分词、向量模型后，已经入库的文档不会自动更新**，执行一次：

```bash
docker exec -w /app swxy_api python manage.py reindex-all
```

所有文档会重新排队解析，知识库页上能看到进度。

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
from core.config import settings
from services.retrieval import dealer
r = dealer.retrieval(question='润本股份的毛利率是多少', embd_mdl=None, tenant_ids=settings.es_index,
                     kb_ids=['1'], vector_similarity_weight=0.6, page=1, page_size=5)
for c in r['chunks']:
    print('总分', round(float(c['similarity']),2), '关键词', round(float(c['term_similarity']),2),
          '重排', round(float(c['vector_similarity']),2), c['content_with_weight'][:40].replace(chr(10),' '))
"
```

`kb_ids=['1']` 是用户 ID，换成你自己的。

**看 ES 是怎么切词的：**

```bash
docker exec swxy_api python -c "
import logging; logging.disable(logging.CRITICAL)
from core.config import settings
from engine.rag.utils.es_conn import ESConnection
r = ESConnection().es.indices.analyze(index=settings.es_index, body={'field':'content_ltks','text':'国电电力 营业 收入'})
print([t['token'] for t in r['tokens']])
"
```

应该输出 `['国电电力', '营业', '收入']`。

**检查重排接口是否正常：**

```bash
docker exec -w /app swxy_api python -c "
from engine.rag.nlp.model import rerank_similarity
print(rerank_similarity('润本股份的毛利率是多少', ['公司毛利率同比+0.4pct至59.0%', '沪深300']))
"
```

正常输出一组 0～1 之间的数；如果全是 0，日志里会有一条“重排失败”，说明是密钥、额度或网络的问题。

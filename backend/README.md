# 后端

FastAPI 接口服务 + 后台入库 worker + RAG 引擎（解析、切块、检索，裁剪自 [RAGFlow](https://github.com/infiniflow/ragflow)）。

系统原理和代码讲解见 [docs/](../docs/)，建议从 [导读](../docs/RAG系统架构导读.md) 开始。

## 🚀 快速启动

### 环境要求

- Docker 和 Docker Compose
- 至少 4GB 可用内存
- 10GB 可用磁盘空间

### 启动步骤

1. **进入后端目录**
```bash
cd backend
```

2. **.env 配置文件**
```bash
cp .env.example .env
```
至少填写 `.env.example` 里“必填”部分：`JWT_SECRET_KEY`、`POSTGRES_PASSWORD`、`ELASTIC_PASSWORD`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`EMBEDDING_API_KEY`。缺少必填项时后端会拒绝启动并提示缺了哪一项。所有配置项的说明见 `app/core/config.py`。


3. **启动所有服务**
```bash
# 启动所有服务（首次启动会自动构建镜像）
docker compose up -d --build

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f swxy_api
```

4. **等待服务完全启动**
```bash
# 检查服务健康状态（数据库、ES、Redis 都可用时返回 200）
curl http://localhost:8000/readyz
```

### 服务说明

项目包含以下服务：
- **swxy_api**: 接口服务，只监听本机 127.0.0.1:8000。启动时自动执行数据库迁移
- **swxy_worker**: 后台入库进程，负责文档解析、切块、向量化、写入 ES
- **gsk_pg**: PostgreSQL 数据库
- **es01**: Elasticsearch 搜索引擎
- **redis**: 入库任务队列 + 会话临时文档缓存

上传的原始文件保存在 Docker 数据卷 `file_data`（容器内 `/data/files`）。

### 运维命令

```bash
# 所有文档重新入库（改了切块、分词、向量模型之后）
docker exec -w /app swxy_api python manage.py reindex-all

# 端到端冒烟测试，共 40 项：上传、解析进度、问答、引用、追问、检索范围、权限、重新解析、删除
# 会调用几次大模型，结束时清理测试数据
docker cp scripts/. swxy_api:/tmp/scripts
docker exec -w /app swxy_api python /tmp/scripts/smoke_test.py

# 检索效果评测
docker exec -w /app swxy_api python /tmp/scripts/eval_retrieval.py /tmp/scripts/eval_set.json --user 1
```

### 常用配置

所有配置项都在 [app/core/config.py](./app/core/config.py) 里定义，在 `.env` 里设置同名环境变量（不区分大小写），重启后端生效。

| 配置项 | 默认值 | 作用 |
|---|---|---|
| `CHAT_MODEL` | `gpt-6.1-sol` | 生成回答的模型 |
| `REWRITE_MODEL` | 空（使用 `CHAT_MODEL`） | 改写追问的模型。换成更快的小模型能缩短追问的等待 |
| `CHAT_HISTORY_TURNS` | 5 | 多轮对话带上最近几轮历史 |
| `RETRIEVAL_TOP_K` | 5 | 每次给模型几段参考内容 |
| `VECTOR_SIMILARITY_WEIGHT` | 0.6 | 重排阶段语义分的权重，其余是关键词分 |
| `SIMILARITY_THRESHOLD` | 0.1 | 低于这个分数的块不给模型 |
| `CHUNK_TOKEN_NUM` | 128 | 切块大小。改了之后要执行 `reindex-all` |
| `API_WORKERS` | 2 | API 进程数，每个约 400MB 内存 |
| `MAX_UPLOAD_MB` | 10 | 单个文件大小上限 |

### 接口一览

除登录、注册和健康检查外，都需要 `Authorization: Bearer <令牌>`。按会话访问的接口会检查会话是否属于当前用户，不是一律返回 404。

| 接口 | 作用 |
|---|---|
| `POST /register`、`POST /login` | 注册、登录 |
| `POST /create_session` | 新建会话 |
| `POST /chat_on_docs?session_id=` | 提问，返回 SSE 流。追问会先结合历史改写问题 |
| `GET /get_sessions`、`GET /get_messages?session_id=` | 会话列表、消息历史 |
| `GET` / `PUT /sessions/{id}/scope` | 读取 / 设置会话的检索范围，`doc_ids` 为空表示全部文档 |
| `POST /upload_files` | 上传知识库文档，立即返回，后台解析 |
| `GET /get_files` | 文档列表，带状态、进度、切片数、失败原因 |
| `POST /documents/{id}/reparse` | 重新解析 |
| `DELETE /delete_file/{文件名}` | 删除文档及其切片 |
| `POST /quick_parse?session_id=` | 上传会话临时文档 |
| `WS /asr?token=` | 语音识别 |
| `GET /healthz`、`GET /readyz` | 存活检查；就绪检查（数据库、ES、Redis） |

完整的参数说明在 http://localhost:8000/docs。

### 停止服务

```bash
# 停止所有服务
docker compose down

# 停止并删除数据卷（注意：这会删除所有数据）
docker compose down -v
```

## 🔧 开发调试

### 查看日志
```bash
# 查看所有服务日志
docker compose logs

# 查看特定服务日志
docker compose logs swxy_api
docker compose logs swxy_worker
docker compose logs gsk_pg
docker compose logs es01
docker compose logs redis

# 实时跟踪日志
docker compose logs -f swxy_api
```

### 进入容器调试
```bash
# 进入主应用容器
docker compose exec swxy_api bash

# 进入数据库容器
docker compose exec gsk_pg psql -U postgres -d gsk
```

### 重新构建服务
```bash
# 重新构建并启动
docker compose up --build -d

# 仅重新构建特定服务
docker compose build swxy_api
docker compose up -d swxy_api
```

## 📋 常见问题

1. **端口被占用**: 确保8000端口未被其他程序占用
2. **内存不足**: Elasticsearch需要至少1GB内存，建议系统有4GB+可用内存
3. **首次启动慢**: 首次启动需要下载镜像和初始化数据，请耐心等待
4. **服务连接失败**: 等待所有服务完全启动后再测试API

## 🎯 访问地址

- API文档: http://localhost:8000/docs（只能在服务器本机访问）

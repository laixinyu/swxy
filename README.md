# 深维学院智能文档问答系统

基于 RAG（检索增强生成）的文档问答系统：上传 PDF、Word、Excel 等文档，用自然语言提问，系统从文档里找出相关段落，生成带引用编号的回答。

## 主要功能

- **知识库**：上传文档后在后台解析，知识库页显示排队中 / 解析中（带进度）/ 已完成 / 异常，失败可以重新解析
- **问答**：混合检索（关键词 + 向量）+ 重排，回答里每句话都标注来源，点击可查看原文和页码
- **多轮对话**：支持追问，“那它的净利率呢？”会结合前文补全后再检索
- **检索范围**：每个会话可以选只在哪几份文档里检索
- **会话临时文档**：在对话里临时上传一份小文档（不超过 4 页或 4000 字），直接作为参考
- **语音输入**、推荐问题、会话历史

## 快速启动

### 1. 后端

```bash
cd backend
cp .env.example .env    # 填写里面“必填”的几项
docker compose up -d --build
docker compose ps       # 五个服务都是 healthy / Up 即可
```

必填项：`JWT_SECRET_KEY`、`POSTGRES_PASSWORD`、`ELASTIC_PASSWORD`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`EMBEDDING_API_KEY`。缺少时后端会拒绝启动，并提示缺了哪一项。

后端接口只监听本机 `127.0.0.1:8000`，不对外暴露。详见 [backend/README.md](./backend/README.md)。

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

打开 `https://localhost:5181/`。开发服务器会把接口请求转发到本机后端。

> 开发服务器默认监听所有网卡，公网也能访问到它转发的接口。只通过 Tailscale 或本机访问时，先设置监听地址：`DEV_HOST=<Tailscale IP> npm run dev`。详见 [frontend/README.md](./frontend/README.md)。

## 文档

按这个顺序读：

1. [入门篇](./docs/RAG入门篇.md)：RAG 是什么、术语表、关键设计的“为什么”。RAG 新手先读这篇
2. [导读](./docs/RAG系统架构导读.md)：系统全貌、两条主线、去哪看代码
3. [代码走读](./docs/RAG系统核心模块深入篇.md)：跟着一次上传、一次提问，逐步看代码和实测数据
4. [实战篇](./docs/RAG实战篇.md)：回答不对时怎么定位问题、怎么衡量检索效果、下一步学什么

## 目录结构

```
backend/    后端：FastAPI 接口、后台入库 worker、RAG 引擎（解析、切块、检索）
frontend/   前端：React + Vite
docs/       项目文档
```

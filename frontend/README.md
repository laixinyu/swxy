# 前端

React 18 + TypeScript + Vite，组件库是 Ant Design，状态管理用 valtio。

## 开发

```bash
npm install
npm run dev
```

打开 `https://localhost:5181/`。开发服务器使用 `certs/` 下的自签名证书走 HTTPS，因为浏览器只在 HTTPS 或 localhost 下允许使用麦克风（语音输入）。

### 接口转发

开发时前端请求发到当前页面的地址，由 Vite 转发给本机后端 `http://127.0.0.1:8000`（见 [vite.config.ts](./vite.config.ts) 的 `proxy`）。所以 `.env` 里的 `VITE_API_BASE` 留空即可。

新增后端接口时，要把路径前缀加进 `vite.config.ts` 里那条正则，否则请求会落到前端路由上，返回的是页面而不是数据。

### 监听地址

开发服务器默认监听所有网卡（`0.0.0.0`）。它会把所有接口转发给后端，所以只要它在运行，公网就能通过 5181 端口访问接口。

只通过 Tailscale 或本机访问时，启动前指定监听地址：

```bash
DEV_HOST=100.101.76.39 npm run dev   # 只监听 Tailscale 地址
DEV_HOST=127.0.0.1 npm run dev       # 只允许本机访问
```

## 构建

```bash
npx vite build
```

产物在 `dist/`。生产部署时，在 `.env` 里把 `VITE_API_BASE` 写成后端地址；如果前端和后端不同域，后端 `.env` 还要设置 `CORS_ORIGINS`。

`npm run build` 会先跑 `tsc -b` 类型检查。`src/api/session.ts` 里有一处早就存在的类型报错（axios 参数类型），会让这条命令失败，但不影响 `npx vite build` 打包和运行。

## 目录

```
src/
├── api/                  接口封装
│   ├── request/plugins/  axios 插件：自动带令牌、401 跳登录、统一错误提示、取消重复请求
│   ├── session.ts        会话、问答（流式）、临时文档、检索范围
│   └── repository.ts     知识库文档：列表、上传、删除、重新解析
├── pages/
│   ├── chat/             聊天页：发问题、读流、渲染回答和引用角标
│   ├── repository/       知识库页：上传、解析状态和进度、重新解析、删除
│   └── login/            登录、注册
├── components/sender/    输入框：文字、语音、临时文档、检索范围选择
└── store/                valtio 状态（令牌持久化在本地存储）
```

问答流的解析和引用渲染的原理，见 [代码走读 2.8、2.9](../docs/RAG系统核心模块深入篇.md#28-前端读流)。

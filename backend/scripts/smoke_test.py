#!/usr/bin/env python3
"""端到端冒烟测试：注册、上传、后台解析与进度、问答、引用编号、多轮追问、检索范围、会话归属、文件校验、重新解析、删除。

在后端容器里运行（会调用一次大模型和向量模型，结束时清理测试数据）：
  docker cp backend/scripts/. swxy_api:/tmp/scripts
  docker exec -w /app swxy_api python /tmp/scripts/smoke_test.py
"""
import json
import logging
import os
import re
import secrets
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR) if APP_DIR.exists() else os.getcwd())
logging.disable(logging.WARNING)

import httpx  # noqa: E402

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000")
DOC2_TEXT = (
    "红枫集团冒烟测试文档。\n"
    "红枫集团 2025 年上半年的营业收入为 56.78 亿元，同比下降 3.2%。\n"
    "公司毛利率为 22.5%。\n"
)
DOC_TEXT = (
    "深维学院冒烟测试文档。\n"
    "青松科技公司 2025 年上半年的营业收入为 12.34 亿元，同比增长 18.5%。\n"
    "公司毛利率为 41.2%，净利率为 9.8%。\n"
    "公司计划在 2026 年推出代号为“银杏”的新一代产品。\n"
)

failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'通过' if ok else '失败'}  {name}{('  ' + detail) if detail and not ok else ''}")
    if not ok:
        failures.append(name)


def register_and_login(client: httpx.Client, username: str, password: str) -> dict:
    client.post("/register", json={"username": username, "password": password}).raise_for_status()
    token = client.post("/login", json={"username": username, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def read_sse(response: httpx.Response) -> dict:
    result = {"documents": None, "content": "", "recommended": None, "error": None, "done": False,
              "retrieval_query": None}
    for line in response.iter_lines():
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):]
        if data == "[DONE]":
            result["done"] = True
            continue
        payload = json.loads(data)
        if "documents" in payload:
            result["documents"] = payload["documents"]
        if payload.get("error"):
            result["error"] = payload["error"]
        if payload.get("content") and not payload.get("thinking"):
            result["content"] += payload["content"]
        if "recommended_questions" in payload:
            result["recommended"] = payload["recommended_questions"]
        if payload.get("retrieval_query"):
            result["retrieval_query"] = payload["retrieval_query"]
    return result


def wait_done(client: httpx.Client, headers: dict, file_name: str) -> dict:
    status = {}
    for _ in range(90):
        files = client.get("/get_files", headers=headers).json()
        status = next((f for f in files if f["file_name"] == file_name), {})
        if status.get("status") in ("done", "failed"):
            break
        time.sleep(2)
    return status


def ask(client: httpx.Client, headers: dict, session_id: str, message: str) -> dict:
    with client.stream("POST", "/chat_on_docs", params={"session_id": session_id}, headers=headers,
                       json={"message": message}) as response:
        return read_sse(response)


def main() -> int:
    suffix = secrets.token_hex(3)
    user_a, user_b, password = f"smoke_a_{suffix}", f"smoke_b_{suffix}", "Smoke-" + secrets.token_hex(6)
    # 比服务端调用大模型的超时（CHAT_TIMEOUT，默认 120 秒）长，才能收到服务端的错误提示
    client = httpx.Client(base_url=BASE, timeout=180)

    check("存活检查 /healthz", client.get("/healthz").status_code == 200)
    ready = client.get("/readyz")
    check("就绪检查 /readyz", ready.status_code == 200, ready.text)

    weak = client.post("/register", json={"username": f"weak_{suffix}", "password": "123"})
    check("弱密码注册被拒绝", weak.status_code == 422 and "密码" in weak.json().get("detail", ""), weak.text)

    headers_a = register_and_login(client, user_a, password)
    headers_b = register_and_login(client, user_b, password)
    check("伪造令牌返回 401", client.get("/get_files", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401)

    session_a = client.post("/create_session", headers=headers_a).json()["session_id"]

    # 文件校验
    bad_ext = client.post("/upload_files", headers=headers_a, files={"files": ("virus.exe", b"MZ...")})
    check("不允许的扩展名被拒绝", bad_ext.status_code == 400, bad_ext.text)
    fake_pdf = client.post("/upload_files", headers=headers_a, files={"files": ("fake.pdf", b"not a pdf")})
    check("扩展名与内容不符被拒绝", fake_pdf.status_code == 400, fake_pdf.text)
    too_big = client.post("/upload_files", headers=headers_a,
                          files={"files": ("big.txt", b"x" * (11 * 1024 * 1024))})
    check("超过大小限制被拒绝", too_big.status_code in (400, 413), str(too_big.status_code))

    # 上传：文件名里的路径部分会被去掉，接口立即返回
    started = time.time()
    upload = client.post("/upload_files", headers=headers_a,
                         files={"files": ("../../etc/smoke_doc.txt", DOC_TEXT.encode("utf-8"))})
    elapsed = time.time() - started
    body = upload.json()
    doc = (body.get("documents") or [{}])[0]
    check("上传接口立即返回", upload.status_code == 200 and elapsed < 5, f"{upload.status_code} {elapsed:.1f}s")
    check("文件名中的路径被去掉", doc.get("file_name") == "smoke_doc.txt", str(doc))
    duplicate = client.post("/upload_files", headers=headers_a,
                            files={"files": ("smoke_doc.txt", DOC_TEXT.encode("utf-8"))})
    check("重名文件被拒绝", duplicate.status_code == 400 and "已存在" in duplicate.text, duplicate.text)

    # 后台解析
    status = wait_done(client, headers_a, "smoke_doc.txt")
    check("后台解析完成", status.get("status") == "done" and status.get("chunk_count", 0) > 0, str(status))
    check("解析进度到 100%", status.get("progress") == 1 and status.get("progress_msg") == "已完成", str(status))
    check("其他用户看不到这个文档",
          all(f["file_name"] != "smoke_doc.txt" for f in client.get("/get_files", headers=headers_b).json()))

    # 问答与引用编号
    with client.stream("POST", "/chat_on_docs", params={"session_id": session_a}, headers=headers_a,
                       json={"message": "青松科技上半年营业收入是多少？毛利率呢？"}) as response:
        answer = read_sse(response)
    docs = answer["documents"] or []
    check("问答流正常结束", answer["done"] and not answer["error"], str(answer["error"]))
    check("检索到上传的文档", any(d["document_name"] == "smoke_doc.txt" for d in docs), str(docs)[:200])
    check("回答包含文档里的数字", "12.34" in answer["content"], answer["content"][:200])
    cites = [int(n) for n in re.findall(r"##(\d+)\$\$", answer["content"])]
    check("引用编号都能对应到参考内容", bool(cites) and all(0 <= n < len(docs) for n in cites),
          f"cites={cites} docs={len(docs)}")
    first = cites[0] if cites else -1
    check("第一个引用指向的块确实包含答案",
          0 <= first < len(docs) and "12.34" in docs[first]["content_with_weight"], f"cite={first}")

    messages = client.get("/get_messages", params={"session_id": session_a}, headers=headers_a)
    check("对话已保存", messages.status_code == 200 and len(messages.json()) == 1, messages.text[:200])

    # 多轮追问：“它”要结合上一轮补全成青松科技
    follow = ask(client, headers_a, session_a, "那它的净利率呢？")
    query = follow["retrieval_query"] or ""
    check("追问被改写成完整的问题", "青松" in query and "净利率" in query, f"retrieval_query={query!r}")
    check("追问回答正确", "9.8" in follow["content"], follow["content"][:200])
    follow_cites = [int(n) for n in re.findall(r"##(\d+)\$\$", follow["content"])]
    check("追问的引用编号对应本轮参考内容", all(0 <= n < len(follow["documents"] or []) for n in follow_cites),
          f"cites={follow_cites}")
    saved = client.get("/get_messages", params={"session_id": session_a}, headers=headers_a).json()
    check("改写后的问题已保存", len(saved) == 2 and saved[1].get("retrieval_query") == query, str(saved[-1])[:200])
    sessions = client.get("/get_sessions", headers=headers_a).json()["sessions"]
    check("会话出现在历史列表", any(s["session_id"] == session_a for s in sessions))

    # 会话归属
    check("别人读不到我的对话",
          client.get("/get_messages", params={"session_id": session_a}, headers=headers_b).status_code == 404)
    check("别人读不到我的会话文档",
          client.get(f"/sessions/{session_a}/documents", headers=headers_b).status_code == 404)
    stolen = client.post("/chat_on_docs", params={"session_id": session_a}, headers=headers_b,
                         json={"message": "你好"})
    check("别人不能在我的会话里提问", stolen.status_code == 404, str(stolen.status_code))

    session_b = client.post("/create_session", headers=headers_b).json()["session_id"]
    with client.stream("POST", "/chat_on_docs", params={"session_id": session_b}, headers=headers_b,
                       json={"message": "青松科技上半年营业收入是多少？"}) as response:
        answer_b = read_sse(response)
    check("检索按用户隔离", not any(d["document_name"] == "smoke_doc.txt" for d in answer_b["documents"] or []))

    # 重新解析
    reparse = client.post(f"/documents/{doc.get('id')}/reparse", headers=headers_a)
    check("重新解析开始", reparse.status_code == 200 and reparse.json().get("status") == "pending", reparse.text)
    check("别人不能重新解析我的文档",
          client.post(f"/documents/{doc.get('id')}/reparse", headers=headers_b).status_code == 404)
    again = wait_done(client, headers_a, "smoke_doc.txt")
    check("重新解析完成", again.get("status") == "done" and again.get("chunk_count") == status.get("chunk_count"),
          str(again))

    # 检索范围：只在第二份文档里找，第一份文档的内容不应出现
    second = client.post("/upload_files", headers=headers_a,
                         files={"files": ("smoke_doc2.txt", DOC2_TEXT.encode("utf-8"))}).json()["documents"][0]
    check("第二份文档解析完成", wait_done(client, headers_a, "smoke_doc2.txt").get("status") == "done")
    session_scope_id = client.post("/create_session", headers=headers_a).json()["session_id"]
    scope = client.put(f"/sessions/{session_scope_id}/scope", headers=headers_a, json={"doc_ids": [second["id"]]})
    check("设置检索范围", scope.status_code == 200 and scope.json()["doc_ids"] == [second["id"]], scope.text)
    check("检索范围刷新后仍在",
          client.get(f"/sessions/{session_scope_id}/scope", headers=headers_a).json()["doc_ids"] == [second["id"]])
    scoped = ask(client, headers_a, session_scope_id, "青松科技和红枫集团上半年营业收入分别是多少？")
    names = {d["document_name"] for d in scoped["documents"] or []}
    check("只在选定的文档里检索", names == {"smoke_doc2.txt"}, str(names))
    check("不能把别人的文档加进检索范围",
          client.put(f"/sessions/{session_b}/scope", headers=headers_b, json={"doc_ids": [second["id"]]}).status_code == 404)
    check("不能改别人会话的检索范围",
          client.put(f"/sessions/{session_scope_id}/scope", headers=headers_b, json={"doc_ids": []}).status_code == 404)
    client.delete("/delete_file/smoke_doc2.txt", headers=headers_a)
    emptied = ask(client, headers_a, session_scope_id, "红枫集团上半年营业收入是多少？")
    check("范围内的文档都删了以后不会扩大到全部文档", not emptied["documents"], str(emptied["documents"])[:200])

    # 删除
    deleted = client.delete("/delete_file/smoke_doc.txt", headers=headers_a)
    check("删除文档", deleted.status_code == 200, deleted.text)
    from core.config import settings
    from engine.rag.utils.es_conn import ESConnection
    remaining = ESConnection().es.count(index=settings.es_index,
                                        query={"term": {"doc_id": doc.get("id", "")}})["count"]
    check("删除后 ES 里不留切片", remaining == 0, str(remaining))

    # 清理测试数据
    from db.database import session_scope
    from models import ChatSession, Message, User
    with session_scope() as db:
        ids = [s for (s,) in db.query(ChatSession.session_id)
               .filter(ChatSession.user_id.in_([str(u.id) for u in db.query(User)
                                                .filter(User.username.in_([user_a, user_b]))]))]
        db.query(Message).filter(Message.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(ChatSession).filter(ChatSession.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.username.in_([user_a, user_b])).delete(synchronize_session=False)

    print()
    print("全部通过" if not failures else f"{len(failures)} 项失败: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

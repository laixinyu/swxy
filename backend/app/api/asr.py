import base64
import json
import logging
import os
import struct

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.auth import access_security

logger = logging.getLogger(__name__)
router = APIRouter()

ASR_MODEL = os.getenv("ASR_MODEL", "qwen-audio-3.0-asr-flash")
# 套餐控制台给出的是 compatible-mode 地址；语音识别走同一主机上的多模态接口。
ASR_BASE_URL = os.getenv(
    "ASR_BASE_URL",
    "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)
MAX_PCM_BYTES = 9_000_000


def _authorized(token: str | None) -> bool:
    if not token:
        return False
    try:
        access_security.jwt_backend.decode(token, access_security.secret_key)
        return True
    except Exception:
        return False


def _generation_url(base: str) -> str:
    root = base.rstrip("/")
    for suffix in ("/compatible-mode/v1", "/compatible-mode"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return root + "/api/v1/services/aigc/multimodal-generation/generation"


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm


def _transcript(payload: dict) -> str:
    if isinstance(payload.get("text"), str):
        return payload["text"]
    sentence = payload.get("sentence") or {}
    if isinstance(sentence, dict) and sentence.get("text"):
        return sentence["text"]
    output = payload.get("output") or {}
    if isinstance(output, dict):
        if isinstance(output.get("text"), str):
            return output["text"]
        nested = output.get("sentence") or {}
        if isinstance(nested, dict) and nested.get("text"):
            return nested["text"]
    return ""


async def _transcribe(pcm: bytes, api_key: str) -> str:
    wav = _pcm_to_wav(pcm)
    data_uri = "data:audio/wav;base64," + base64.b64encode(wav).decode()
    body = {
        "model": ASR_MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": data_uri}}
                    ],
                }
            ]
        },
        "parameters": {
            "format": "wav",
            "sample_rate": "16000",
            "language_hints": ["zh"],
        },
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            _generation_url(ASR_BASE_URL),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-DashScope-SSE": "disable",
            },
            json=body,
        )
    if response.status_code >= 400:
        detail = response.text[:300]
        logger.warning("语音识别返回 %s %s", response.status_code, detail)
        raise RuntimeError("语音识别失败")
    return _transcript(response.json()).strip()


@router.websocket("/asr")
async def asr_proxy(websocket: WebSocket):
    await websocket.accept()
    if not _authorized(websocket.query_params.get("token")):
        await websocket.send_json({"type": "error", "message": "登录状态已失效，请重新登录"})
        await websocket.close()
        return

    api_key = os.getenv("ASR_API_KEY")
    if not api_key:
        await websocket.send_json({"type": "error", "message": "未配置语音识别密钥"})
        await websocket.close()
        return

    audio = bytearray()
    stopped = False
    try:
        await websocket.send_json({"type": "ready"})
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            chunk = message.get("bytes")
            if chunk:
                if len(audio) < MAX_PCM_BYTES:
                    audio.extend(chunk)
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "stop":
                stopped = True
                break

        if len(audio) < 3200:
            await websocket.send_json({"type": "finished"})
            return

        transcript = await _transcribe(bytes(audio), api_key)
        if transcript:
            await websocket.send_json(
                {"type": "transcript", "text": transcript, "final": True}
            )
        await websocket.send_json({"type": "finished"})
    except WebSocketDisconnect:
        logger.info("语音识别连接已断开")
    except Exception:
        logger.exception("语音识别失败")
        if stopped:
            try:
                await websocket.send_json({"type": "error", "message": "语音识别失败"})
            except Exception:
                pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

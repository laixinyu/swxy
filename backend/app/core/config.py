from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """所有配置都从环境变量或 backend/.env 读取，字段名即环境变量名（不区分大小写）。"""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务
    root_path: str = ""
    log_level: str = "INFO"
    # 逗号分隔。开发时前端经 Vite 代理同源访问，不需要配置。
    cors_origins: str = ""

    # 认证
    jwt_secret_key: str = Field(min_length=32)
    jwt_expire_days: int = 2

    # PostgreSQL
    database_url: str

    # Elasticsearch
    es_host: str = "http://es01:9200"
    es_user: str = "elastic"
    elastic_password: str
    es_index: str = "swxy_chunks"
    es_timeout: int = 60

    # Redis：临时文档缓存 + 入库任务队列
    redis_url: str = "redis://redis:6379/0"
    ingest_queue: str = "ingest"
    ingest_job_timeout: int = 1800

    # 文件存储
    storage_dir: Path = Path("/data/files")
    max_upload_mb: int = 10
    # 逗号分隔
    allowed_extensions: str = "pdf,docx,doc,xlsx,xls,txt,md,markdown,html,htm,json"

    # 对话模型（OpenAI 兼容接口）
    dashscope_api_key: str
    dashscope_base_url: str
    chat_model: str = "gpt-6.1-sol"
    chat_timeout: int = 120

    # 向量模型：阿里云百炼
    embedding_api_key: str
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = 10

    # 重排模型：默认与向量共用密钥
    rerank_api_key: str = ""
    rerank_model: str = "qwen3.7-text-rerank"
    rerank_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    rerank_timeout: int = 20

    # 语音识别
    asr_api_key: str = ""
    asr_model: str = "qwen-audio-3.0-asr-flash"
    asr_base_url: str = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"

    # 旧版火山引擎语音凭证接口
    volc_app_id: str = ""
    volc_access_key: str = ""

    # 切块与检索
    chunk_token_num: int = 128
    retrieval_top_k: int = 5
    retrieval_candidates: int = 128
    vector_similarity_weight: float = 0.6
    similarity_threshold: float = 0.1

    # 多轮对话：带最近几轮历史；历史回答截断长度；改写问题的超时
    chat_history_turns: int = 5
    chat_history_answer_chars: int = 1500
    query_rewrite_timeout: int = 15
    # 改写追问用的模型，留空则用 chat_model。改写任务简单，换成更快的小模型能明显缩短追问的等待
    rewrite_model: str = ""

    # 会话临时文档
    quick_parse_max_pages: int = 4
    quick_parse_max_chars: int = 4000
    quick_parse_ttl_seconds: int = 7200
    quick_parse_prompt_chars: int = 4000

    @property
    def cors_origin_list(self) -> list[str]:
        return _split_csv(self.cors_origins)

    @property
    def allowed_extension_set(self) -> set[str]:
        return {ext.lower().lstrip(".") for ext in _split_csv(self.allowed_extensions)}

    @property
    def effective_rerank_api_key(self) -> str:
        return self.rerank_api_key or self.embedding_api_key

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

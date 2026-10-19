"""RAG Agent 配置模块。

所有配置项从 .env 文件读取，使用 pydantic-settings 校验。
环境变量前缀：RAG_（如 RAG_MILVUS_URI、RAG_LLM_API_KEY）
"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class MilvusConfig(BaseSettings):
    """Milvus 向量数据库连接与 collection 配置。"""

    model_config = SettingsConfigDict(
        env_prefix="RAG_MILVUS_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    uri: str = Field(
        default="http://localhost:19530",
        description="Milvus gRPC 连接地址",
    )
    collection_name: str = Field(
        default="erp_knowledge",
        description="Milvus collection 名称",
    )
    collection_alias: str = Field(
        default="erp_knowledge_latest",
        description="Collection 别名，用于版本切换",
    )
    timeout: float = Field(default=30.0, description="连接超时（秒）")


class EmbeddingConfig(BaseSettings):
    """BGE-M3 嵌入模型配置。"""

    model_config = SettingsConfigDict(
        env_prefix="RAG_EMBEDDING_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model_path: str = Field(
        default=r"D:\huggingface_cache\modelscope\models\BAAI--bge-m3\snapshots\master",
        description="BGE-M3 模型本地路径",
    )
    device: str = Field(default="cpu", description="推理设备: cpu / cuda")
    use_fp16: bool = Field(default=False, description="是否使用 FP16 推理（GPU 时推荐 True）")
    batch_size: int = Field(default=8, description="批量嵌入的 batch 大小（CPU 内存受限时调小）")
    dense_dim: int = Field(default=1024, description="Dense 向量维度")
    max_length: int = Field(default=8192, description="输入文本最大 token 长度")
    num_threads: int = Field(
        default=4,
        description="PyTorch CPU 线程数；线程过多会显著抬高内存峰值",
    )


class LLMConfig(BaseSettings):
    """大语言模型配置（用于后续 Phase 3 AIGC 和 Reranker）。"""

    model_config = SettingsConfigDict(
        env_prefix="RAG_LLM_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(default="", description="LLM API Key")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="LLM API Base URL",
    )
    fast_model: str = Field(
        default="qwen-turbo",
        description="快速模型（用于常规任务、RAG 查询改写）",
    )
    strong_model: str = Field(
        default="qwen-plus",
        description="强力模型（用于代码生成、复杂推理）",
    )
    temperature: float = Field(default=0.1, description="生成温度")
    max_tokens: int = Field(default=4096, description="最大生成 token 数")


class SearchConfig(BaseSettings):
    """RAG 检索策略配置。"""

    model_config = SettingsConfigDict(
        env_prefix="RAG_SEARCH_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    top_k: int = Field(default=10, description="最终返回的检索结果数")
    dense_top_k: int = Field(default=20, description="Dense 召回粗排数量")
    sparse_top_k: int = Field(default=20, description="Sparse 召回粗排数量")
    rrf_k: int = Field(default=60, description="RRF 融合排序参数 k（值越大排序越平滑）")
    score_threshold: float = Field(
        default=0.52,
        description="最低 dense cosine 相关性阈值（实测：有效查询 0.53~0.75，无关查询 0.42~0.51）",
    )


class RAGConfig(BaseSettings):
    """RAG 全局配置，聚合所有子配置。"""

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 子配置
    milvus: MilvusConfig = Field(default_factory=MilvusConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)

    # 数据路径
    chunks_file: str = Field(
        default=str(PROJECT_ROOT / "cache" / "chunks.json"),
        description="全量 chunks 文件路径",
    )
    cache_dir: str = Field(
        default=str(PROJECT_ROOT / "cache"),
        description="缓存目录",
    )
    knowledge_store_dir: str = Field(
        default=str(PROJECT_ROOT / "knowledge-store"),
        description="知识库产出目录",
    )


def get_rag_config() -> RAGConfig:
    """获取 RAG 全局配置单例。"""
    return RAGConfig()

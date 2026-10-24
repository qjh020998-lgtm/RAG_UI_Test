"""BGE-M3 向量嵌入封装。

职责
----
- 把文本编码成 **dense（1024 维）+ sparse（token 权重）**
- 进程级复用模型（冷加载 22~30s，只付一次）
- 供 indexer 批量编码 chunk，供 retriever 编码 query

不负责
------
- 不写 Milvus（由 ``agent/milvus_connect.py`` 负责）
- 不切分文本（由 ``agent/chunking.py`` 负责）

配置来源（``.env``）
--------------------
| 变量 | 默认值 |
|---|---|
| ``RAG_EMBEDDING_MODEL_PATH`` | `D:\\huggingface_cache\\...\\BAAI--bge-m3\\snapshots\\master` |
| ``RAG_EMBEDDING_DEVICE`` | ``cpu`` |
| ``RAG_EMBEDDING_USE_FP16`` | ``false`` |
| ``RAG_EMBEDDING_BATCH_SIZE`` | ``8`` |
| ``RAG_EMBEDDING_MAX_LENGTH`` | ``8192`` |
| ``RAG_EMBEDDING_DENSE_DIM`` | ``1024`` |
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# 项目根 .env（未安装 python-dotenv 时跳过，不影响显式环境变量）
try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass

log = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = (
    r"D:\huggingface_cache\modelscope\models\BAAI--bge-m3\snapshots\master"
)
DEFAULT_DEVICE = "cpu"
DEFAULT_BATCH_SIZE = 8
DEFAULT_MAX_LENGTH = 8192
DEFAULT_DENSE_DIM = 1024

DenseVector = list[float]
SparseVector = dict[int, float]


class EmbeddingUnavailableError(RuntimeError):
    """模型不可用；异常信息里带排查步骤。"""

    def __init__(self, message: str, cause: Exception | None = None):
        self.cause = cause
        super().__init__(
            f"{message}\n"
            f"请确认：\n"
            f"  1) .env 的 RAG_EMBEDDING_MODEL_PATH 指向模型快照目录\n"
            f"  2) 已安装 flagembedding\n"
            f"  3) 磁盘/内存足够（BGE-M3 约 2.27GB，bge-small 约 100MB）\n"
            f"原始错误：{cause!r}"
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EmbeddingSettings:
    """嵌入参数。"""

    model_path: str = DEFAULT_MODEL_PATH
    device: str = DEFAULT_DEVICE
    use_fp16: bool = False
    batch_size: int = DEFAULT_BATCH_SIZE
    max_length: int = DEFAULT_MAX_LENGTH
    dense_dim: int = DEFAULT_DENSE_DIM

    @classmethod
    def from_env(cls) -> "EmbeddingSettings":
        return cls(
            model_path=os.getenv("RAG_EMBEDDING_MODEL_PATH", DEFAULT_MODEL_PATH),
            device=os.getenv("RAG_EMBEDDING_DEVICE", DEFAULT_DEVICE),
            use_fp16=_env_bool("RAG_EMBEDDING_USE_FP16", False),
            batch_size=int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))),
            max_length=int(os.getenv("RAG_EMBEDDING_MAX_LENGTH", str(DEFAULT_MAX_LENGTH))),
            dense_dim=int(os.getenv("RAG_EMBEDDING_DENSE_DIM", str(DEFAULT_DENSE_DIM))),
        )

    @property
    def model_dir(self) -> Path:
        return Path(self.model_path)


class BgeM3Embedder:
    """BGE-M3 的 dense + sparse 编码器（线程安全的懒加载）。"""

    def __init__(self, settings: EmbeddingSettings | None = None):
        self.settings = settings or EmbeddingSettings.from_env()
        self._model = None
        self._sparse_capable = True
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    @property
    def model(self):
        """首次访问时加载模型；之后复用同一实例。"""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = self._load_model()
        return self._model

    def _load_model(self):
        path = self.settings.model_dir
        if not path.exists():
            raise EmbeddingUnavailableError(f"嵌入模型目录不存在：{path}")

        try:
            from FlagEmbedding import BGEM3FlagModel, FlagModel
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise EmbeddingUnavailableError("未安装 FlagEmbedding", exc) from exc

        kwargs = {"use_fp16": self.settings.use_fp16}
        # BGE-M3 用 BGEM3FlagModel（dense + sparse）；普通 BGE 用 FlagModel（仅 dense）
        is_m3 = "bge-m3" in path.name.lower() or (path / "colbert_linear.pt").exists()
        model_cls = BGEM3FlagModel if is_m3 else FlagModel
        # FlagEmbedding 1.3+ 用 devices，旧版用 device
        try:
            model = model_cls(str(path), devices=self.settings.device, **kwargs)
        except TypeError:
            model = model_cls(str(path), device=self.settings.device, **kwargs)

        self._sparse_capable = is_m3
        log.info(
            "%s 已加载：%s（device=%s，sparse=%s）",
            "BGE-M3" if is_m3 else "BGE",
            path,
            self.settings.device,
            "on" if is_m3 else "off",
        )
        return model

    def is_available(self) -> bool:
        """只检查路径与依赖，不加载模型。"""
        if not self.settings.model_dir.exists():
            return False
        try:
            import FlagEmbedding  # noqa: F401
        except ImportError:
            return False
        return True

    # ------------------------------------------------------------------
    # 编码
    # ------------------------------------------------------------------

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
        max_length: int | None = None,
    ) -> tuple[list[DenseVector], list[SparseVector]]:
        """批量编码，返回 ``(dense_vectors, sparse_vectors)``。

        - dense：``list[list[float]]``，已 L2 归一化（配合 COSINE 索引）
        - sparse：``list[dict[int, float]]``，key 是 token id，value 是权重
        """
        if not texts:
            return [], []

        # 空文本会让 tokenizer 报错，用单空格占位；保证输出与输入一一对应
        prepared = [text if text and text.strip() else " " for text in texts]
        batch = batch_size or self.settings.batch_size
        length = max_length or self.settings.max_length
        model = self.model  # 触发加载，确定是否支持 sparse

        if self._sparse_capable:
            output = model.encode(
                prepared,
                batch_size=batch,
                max_length=length,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
            dense = output["dense_vecs"]
            sparse = [_to_sparse_dict(weights) for weights in output["lexical_weights"]]
        else:
            # bge-small 这类模型只输出 dense，sparse 用占位值满足 Milvus 字段约束
            dense = model.encode(prepared, batch_size=batch, max_length=length)
            sparse = [_placeholder_sparse() for _ in prepared]

        if hasattr(dense, "tolist"):
            dense = dense.tolist()
        return list(dense), sparse

    def encode_query(self, text: str) -> tuple[DenseVector, SparseVector]:
        """编码单条 query。"""
        dense_list, sparse_list = self.encode([text])
        return dense_list[0], sparse_list[0]

    @property
    def dim(self) -> int:
        return self.settings.dense_dim


def _to_sparse_dict(weights: dict) -> SparseVector:
    """BGE-M3 的 lexical_weights 是 ``{token_str: weight}``，转成 Milvus 需要的 int key。"""
    result: SparseVector = {}
    for key, value in (weights or {}).items():
        try:
            token_id = int(key)
        except (TypeError, ValueError):
            continue
        weight = float(value)
        if weight > 0:
            result[token_id] = weight
    return result


def _placeholder_sparse() -> SparseVector:
    """dense-only 模型没有 sparse；给 Milvus 一个非空占位值。

    Milvus 的 SPARSE 字段不接受空 dict。检索只走 dense，占位值不参与打分。
    """
    return {0: 1e-6}


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------

_embedder: BgeM3Embedder | None = None


def get_embedder(settings: EmbeddingSettings | None = None) -> BgeM3Embedder:
    """进程级单例；首次 ``encode`` 时才加载模型。"""
    global _embedder
    if _embedder is None:
        _embedder = BgeM3Embedder(settings)
    return _embedder


def reset_embedder() -> None:
    """清空单例（测试或切换模型时用）。"""
    global _embedder
    _embedder = None


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="BGE-M3 嵌入自检 / 单条编码")
    parser.add_argument("--check", action="store_true", help="只检查配置与依赖，不加载模型")
    parser.add_argument("--encode", default="", help="编码一条文本（会加载模型，慢）")
    args = parser.parse_args(argv)

    settings = EmbeddingSettings.from_env()
    embedder = get_embedder(settings)

    if args.check or not args.encode:
        print("model_path:", settings.model_path)
        print("model exists:", settings.model_dir.exists())
        print("device:", settings.device, "| fp16:", settings.use_fp16)
        print("batch_size:", settings.batch_size, "| max_length:", settings.max_length)
        print("dense_dim:", settings.dense_dim)
        print("is_available:", embedder.is_available())
        try:
            from importlib.metadata import version

            print("flagembedding:", version("FlagEmbedding"))
        except Exception:
            print("flagembedding: NOT INSTALLED")

    if args.encode:
        dense, sparse = embedder.encode_query(args.encode)
        print("text:", args.encode)
        print("dense dim:", len(dense))
        print("dense head:", [round(v, 4) for v in dense[:5]])
        print("sparse tokens:", len(sparse))
        top = sorted(sparse.items(), key=lambda item: -item[1])[:5]
        print("sparse top:", top)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

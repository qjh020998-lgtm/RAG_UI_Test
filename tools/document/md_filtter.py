"""Markdown文档处理器 - 基于LangChain的结构化切分"""

from pathlib import Path
from typing import List

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from tools.document.schema import DocumentChunk, ChunkType


class MarkdownProcessor:
    """Markdown文档处理器，支持多种文档类型的智能切分"""

    def __init__(self):
        """初始化处理器"""
        # 按标题层级切分
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "header_1"),
                ("##", "header_2"),
                ("###", "header_3"),
            ],
            strip_headers=False,
        )

        # 长文本二次切分
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=600,
            chunk_overlap=60,
            separators=["\n\n", "\n", "。", "；", "，", " "],
        )

    def parse(self, file_path: str | Path) -> List[DocumentChunk]:
        """解析MD文件，根据文件名自动选择切分策略

        Args:
            file_path: MD文件路径

        Returns:
            DocumentChunk列表
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        doc_name = path.stem

        # 根据文件名选择切分策略
        if "功能清单" in doc_name:
            return self._chunk_function_list(content, doc_name)
        elif "数据库设计" in doc_name:
            return self._chunk_database_design(content, doc_name)
        else:
            return self._chunk_testing_guide(content, doc_name)

    def _strip_md_link(self, text: str) -> str:
        """去除Markdown链接语法，提取纯文本

        例如: [零售管理](url) -> 零售管理

        Args:
            text: 可能包含Markdown链接的文本

        Returns:
            去除链接后的纯文本
        """
        import re
        # 匹配 [text](url) 格式，提取text部分
        match = re.match(r'\[([^\]]+)\]\([^)]+\)', text.strip())
        if match:
            return match.group(1)
        return text.strip()

    def _chunk_function_list(self, content: str, doc_name: str) -> List[DocumentChunk]:
        """功能清单切分：每个功能项（H3 + 列表项描述）为一个chunk

        Args:
            content: 文档内容
            doc_name: 文档名称

        Returns:
            DocumentChunk列表
        """
        chunks = []
        lines = content.split('\n')

        current_h2 = ""
        current_h3 = ""
        current_content = []
        chunk_index = 0

        for line in lines:
            if line.startswith('## '):
                # 遇到新的H2，保存之前的H3内容
                if current_h3 and current_content:
                    chunk = self._build_chunk(
                        doc_name=doc_name,
                        module=current_h2,
                        section=current_h3,
                        content='\n'.join(current_content),
                        chunk_type=ChunkType.TEXT,
                        chunk_index=chunk_index,
                    )
                    chunks.append(chunk)
                    chunk_index += 1

                # 更新H2
                current_h2 = self._strip_md_link(line[3:].strip())
                current_h3 = ""
                current_content = []

            elif line.startswith('### '):
                # 遇到新的H3，保存之前的H3内容
                if current_h3 and current_content:
                    chunk = self._build_chunk(
                        doc_name=doc_name,
                        module=current_h2,
                        section=current_h3,
                        content='\n'.join(current_content),
                        chunk_type=ChunkType.TEXT,
                        chunk_index=chunk_index,
                    )
                    chunks.append(chunk)
                    chunk_index += 1

                # 更新H3
                current_h3 = self._strip_md_link(line[4:].strip())
                current_content = []

            else:
                # 普通内容行
                if current_h3:  # 只在有H3标题时收集内容
                    current_content.append(line)

        # 保存最后一个H3
        if current_h3 and current_content:
            chunk = self._build_chunk(
                doc_name=doc_name,
                module=current_h2,
                section=current_h3,
                content='\n'.join(current_content),
                chunk_type=ChunkType.TEXT,
                chunk_index=chunk_index,
            )
            chunks.append(chunk)

        return chunks

    def _chunk_database_design(self, content: str, doc_name: str) -> List[DocumentChunk]:
        """数据库设计切分：每张表（H2）为一个chunk，表格整体保留

        Args:
            content: 文档内容
            doc_name: 文档名称

        Returns:
            DocumentChunk列表
        """
        chunks = []
        lines = content.split('\n')

        current_h2 = ""
        current_content = []
        chunk_index = 0

        for line in lines:
            if line.startswith('## '):
                # 遇到新的H2，保存之前的表
                if current_h2 and current_content:
                    # 从H2标题提取表名（去除链接）
                    table_name = self._strip_md_link(current_h2)

                    chunk = self._build_chunk(
                        doc_name=doc_name,
                        module="数据库设计",
                        section=table_name,
                        content='\n'.join(current_content),
                        chunk_type=ChunkType.TABLE,
                        chunk_index=chunk_index,
                    )
                    chunks.append(chunk)
                    chunk_index += 1

                # 更新H2
                current_h2 = line[3:].strip()
                current_content = []

            else:
                # 普通内容行
                if current_h2:  # 只在有H2标题时收集内容
                    current_content.append(line)

        # 保存最后一张表
        if current_h2 and current_content:
            table_name = self._strip_md_link(current_h2)

            chunk = self._build_chunk(
                doc_name=doc_name,
                module="数据库设计",
                section=table_name,
                content='\n'.join(current_content),
                chunk_type=ChunkType.TABLE,
                chunk_index=chunk_index,
            )
            chunks.append(chunk)

        return chunks

    def _chunk_testing_guide(self, content: str, doc_name: str) -> List[DocumentChunk]:
        """测试指南切分：按H2/H3切分，保留代码块和表格完整性

        Args:
            content: 文档内容
            doc_name: 文档名称

        Returns:
            DocumentChunk列表
        """
        # 第一步：按标题切分
        splits = self.header_splitter.split_text(content)

        chunks = []
        chunk_index = 0

        for split in splits:
            # 从metadata提取标题
            h2 = split.metadata.get("header_2", "")
            h3 = split.metadata.get("header_3", "")

            # 去除Markdown链接
            module = self._strip_md_link(h2) if h2 else ""
            section = self._strip_md_link(h3) if h3 else module

            text = split.page_content.strip()

            # 短文本直接作为一个chunk
            if len(text) <= 600:
                chunk = self._build_chunk(
                    doc_name=doc_name,
                    module=module,
                    section=section,
                    content=text,
                    chunk_type=ChunkType.TEXT,
                    chunk_index=chunk_index,
                )
                chunks.append(chunk)
                chunk_index += 1
            else:
                # 长文本二次切分
                sub_splits = self.recursive_splitter.split_text(text)
                for sub_split in sub_splits:
                    chunk = self._build_chunk(
                        doc_name=doc_name,
                        module=module,
                        section=section,
                        content=sub_split,
                        chunk_type=ChunkType.TEXT,
                        chunk_index=chunk_index,
                    )
                    chunks.append(chunk)
                    chunk_index += 1

        return chunks

    def _build_chunk(
        self,
        doc_name: str,
        module: str,
        section: str,
        content: str,
        chunk_type: ChunkType,
        chunk_index: int,
    ) -> DocumentChunk:
        """构建DocumentChunk

        Args:
            doc_name: 文档名称
            module: 模块名称
            section: 小节名称
            content: 内容
            chunk_type: chunk类型
            chunk_index: chunk索引

        Returns:
            DocumentChunk对象
        """
        # 生成chunk_id
        chunk_id = f"{doc_name}_{chunk_type.value}_{chunk_index:04d}"

        # 构建上下文前缀
        prefix = f"[{doc_name}] [{module}] [{section}]"
        full_content = f"{prefix}\n\n{content}"

        from tools.document.schema import ChunkMetadata

        metadata = ChunkMetadata(
            source_doc=f"{doc_name}.md",
            doc_title=doc_name,
            module=module,
            section_title=section,
            page=0,
            chunk_type=chunk_type,
            chunk_index=chunk_index,
            is_plugin=False,
        )

        return DocumentChunk(
            chunk_id=chunk_id,
            content=full_content,
            metadata=metadata,
            image_metadata=None,
        )

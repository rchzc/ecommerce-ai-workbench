"""文档切分与结构化。

切分策略（面试高频问题，必须能讲清楚）：
- 段落优先：先按空行分段。固定长度切分会把完整段落拦腰截断，破坏语义完整性。
- 长段按句切：段落超过阈值时，按句号 / 问号 / 感叹号切句，滚动累积到接近阈值。
- 相邻块重叠：相邻切片尾部保留 overlap 个字符，缓解跨块语义断裂 ——
  一句话正好卡在块边界时，重叠能保证它至少完整地出现在某一个切片里。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Chunk:
    """一个语义切片。"""

    text: str
    index: int
    source: str
    domain: str
    #: 文档在语料中的全局序号。用于生成全局唯一的 chunk_id ——
    #: 同一域下多篇文档的 index 都从 0 开始，只靠 domain+index 会撞 ID，
    #: 导致写入向量库时被拒绝（ChromaDB 要求 ID 唯一）。
    doc_ordinal: int = 0

    @property
    def chunk_id(self) -> str:
        return f"{self.domain}-{self.doc_ordinal:03d}-{self.index:04d}"


def split_sentences(paragraph: str) -> list[str]:
    """按句末标点切句，保留标点。"""
    parts = _SENTENCE_SPLIT_RE.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def chunk_document(
    text: str,
    *,
    source: str,
    domain: str,
    chunk_size: int = 600,
    overlap: int = 50,
    doc_ordinal: int = 0,
) -> list[Chunk]:
    """把一篇文档切成语义切片。

    doc_ordinal 是文档在语料中的全局序号，用于保证 chunk_id 全局唯一。

    重叠只加在"确实被切断"的地方：

    - 段落本身没超阈值 → 整段独立成块，**不**拼接上一段的尾巴。
      之前无差别给每个块都加前缀，导致知识库里几乎每个切片都被上一段内容污染
      （中文文档大多整段都短于 600 字），向量被无关文本稀释，召回精度下降。
    - 长段被切成多块 → 只在**同一段内**的相邻块之间补重叠，
      保证卡在边界上的那句话至少完整地出现在某一个块里。
      之前这段逻辑重复加了两次重叠（滚动累积时加一次，最后统一又加一次）。
    """
    if overlap >= chunk_size:
        raise ValueError("overlap 必须小于 chunk_size")

    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]

    segments: list[str] = []
    # joins[i] 表示第 i 块是否「与第 i-1 块同属一个被切断的长段」，
    # 只有这种情况才需要补重叠
    joins: list[bool] = []

    for para in paragraphs:
        if len(para) <= chunk_size:
            segments.append(para)
            joins.append(False)
            continue
        # 长段落：按句滚动累积
        buffer = ""
        first_in_para = True
        for sentence in split_sentences(para):
            if not buffer:
                buffer = sentence
                continue
            if len(buffer) + len(sentence) <= chunk_size:
                buffer += sentence
            else:
                segments.append(buffer)
                joins.append(not first_in_para)
                first_in_para = False
                buffer = sentence
        if buffer:
            segments.append(buffer)
            joins.append(not first_in_para)

    chunks: list[Chunk] = []
    for idx, segment in enumerate(segments):
        body = segment
        if idx > 0 and overlap and joins[idx]:
            body = segments[idx - 1][-overlap:] + segment
        chunks.append(
            Chunk(
                text=body,
                index=idx,
                source=source,
                domain=domain,
                doc_ordinal=doc_ordinal,
            )
        )
    return chunks


def load_documents(docs_dir: str) -> list[tuple[str, str, str]]:
    """扫描目录下的 .md 文件，返回 (domain, filename, content)。

    目录约定：data/docs/<domain>/*.md，每个子目录是一个业务域。
    业务域会成为切片的元数据，检索时按域过滤，避免跨领域噪声。
    """
    import os

    results: list[tuple[str, str, str]] = []
    if not os.path.isdir(docs_dir):
        return results
    for entry in sorted(os.listdir(docs_dir)):
        sub = os.path.join(docs_dir, entry)
        if os.path.isdir(sub):
            domain = entry
            for name in sorted(os.listdir(sub)):
                if not name.endswith(".md"):
                    continue
                path = os.path.join(sub, name)
                with open(path, encoding="utf-8") as fh:
                    results.append((domain, name, fh.read()))
        elif entry.endswith(".md"):
            with open(sub, encoding="utf-8") as fh:
                results.append(("general", entry, fh.read()))
    return results

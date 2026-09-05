"""文档切分测试。

重点覆盖两类容易埋雷的地方：
1. 相邻块重叠是否真的生效（跨块语义断裂是 RAG 召回质量下降的常见原因）
2. chunk_id 是否全局唯一（重复 ID 会让 ChromaDB 拒绝整批写入）
"""
from __future__ import annotations

import pytest

from app.core.rag import Chunk, chunk_document, load_documents, split_sentences


def test_short_paragraphs_kept_intact():
    text = "第一段内容。\n\n第二段内容。"
    chunks = chunk_document(text, source="a.md", domain="ads", chunk_size=600, overlap=50)
    assert [c.text for c in chunks] == ["第一段内容。", "第二段内容。"]


def test_no_overlap_between_independent_short_paragraphs():
    """完整段落不应被上一段的尾巴污染，否则向量会被无关内容稀释。"""
    text = "第一段内容足够长一点。\n\n第二段内容也足够长一点。"
    chunks = chunk_document(text, source="a.md", domain="ads", chunk_size=600, overlap=20)
    assert chunks[1].text == "第二段内容也足够长一点。"


def test_long_paragraph_split_by_sentence():
    para = "。".join(f"这是第{i}句" for i in range(1, 21)) + "。"
    chunks = chunk_document(para, source="a.md", domain="ads", chunk_size=60, overlap=10)
    assert len(chunks) > 1
    # 每一块都不应远超阈值（重叠会带来少量超出，放宽到 1.5 倍）
    for c in chunks:
        assert len(c.text) <= 60 * 1.5 + 10


def test_overlap_actually_applied():
    """第 n 块的开头应包含第 n-1 块的结尾，否则跨块句子会被截断。"""
    para = "。".join(f"句子{i}" for i in range(1, 16)) + "。"
    chunks = chunk_document(para, source="a.md", domain="ads", chunk_size=40, overlap=12)
    assert len(chunks) >= 2
    prev_tail = chunks[0].text[-12:]
    assert chunks[1].text.startswith(prev_tail)


def test_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValueError):
        chunk_document("内容", source="a.md", domain="ads", chunk_size=10, overlap=10)


def test_chunk_id_is_globally_unique_across_documents():
    """同一域下两篇文档的 index 都从 0 开始，靠 doc_ordinal 区分。"""
    ids = set()
    for doc_ordinal in range(3):
        for idx, chunk in enumerate(
            chunk_document(
                "段落一。\n\n段落二。",
                source=f"d{doc_ordinal}.md",
                domain="ads",
                doc_ordinal=doc_ordinal,
            )
        ):
            cid = chunk.chunk_id
            assert cid not in ids, f"chunk_id 重复：{cid}"
            ids.add(cid)
            assert cid == f"ads-{doc_ordinal:03d}-{idx:04d}"


def test_chunk_dataclass_carries_metadata():
    c = Chunk(text="x", index=0, source="a.md", domain="ads", doc_ordinal=1)
    assert (c.domain, c.source, c.chunk_id) == ("ads", "a.md", "ads-001-0000")


def test_split_sentences_keeps_punctuation():
    sents = split_sentences("第一句。第二句！第三句？")
    assert sents == ["第一句。", "第二句！", "第三句？"]


def test_load_documents_groups_by_domain(tmp_path):
    (tmp_path / "ads").mkdir()
    (tmp_path / "ads" / "a.md").write_text("广告知识", encoding="utf-8")
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "b.md").write_text("评论知识", encoding="utf-8")
    (tmp_path / "root.md").write_text("未归类", encoding="utf-8")

    docs = load_documents(str(tmp_path))
    domains = {d for d, _, _ in docs}
    assert domains == {"ads", "review", "general"}
    assert len(docs) == 3


def test_load_documents_missing_dir_returns_empty(tmp_path):
    assert load_documents(str(tmp_path / "nope")) == []

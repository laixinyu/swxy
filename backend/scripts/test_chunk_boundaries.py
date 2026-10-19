#!/usr/bin/env python3
"""
切块边界测试：块不能跨页拼接，双栏版式里侧栏不能插进正文。
在 worker 容器里运行：docker exec -w /app swxy_worker python /app/../scripts/test_chunk_boundaries.py [pdf 路径]
"""
import sys
from functools import partial
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "app"))
sys.path.append("/app")

from engine.rag.app.naive import Pdf, chunk
from engine.rag.nlp import naive_merge


def tag(page, x0, x1, top, bottom):
    return f"@@{page}\t{x0:.1f}\t{x1:.1f}\t{top:.1f}\t{bottom:.1f}##"


def test_merge_breaks_at_page_change():
    sections = [
        ("第一页正文。", tag(1, 200, 570, 100, 120)),
        ("第一页结尾的风险提示。", tag(1, 200, 500, 528, 539)),
        ("第三页的免责声明。", tag(3, 42, 543, 79, 91)),
        ("第三页继续的声明内容。", tag(3, 42, 543, 102, 139)),
    ]
    chunks = naive_merge(sections, 128)
    assert len(chunks) == 2, chunks
    assert "风险提示" in chunks[0] and "免责声明" not in chunks[0], chunks
    assert chunks[1].startswith("第三页的免责声明"), chunks


def test_merge_keeps_page_spanning_box_with_next_page():
    sections = [
        ("跨页的一段话，从第一页", tag("1-2", 42, 543, 700, 820)),
        ("第二页接着写。", tag(2, 42, 543, 60, 80)),
    ]
    assert len(naive_merge(sections, 128)) == 1


def test_merge_without_positions_unchanged():
    assert naive_merge(["一段。", "两段。"], 128) == ["一段。两段。"]


def box(text, page, x0, x1, top):
    return {"text": text, "page_number": page, "x0": x0, "x1": x1, "top": top, "bottom": top + 10}


def test_sidebar_ordered_after_main_column():
    boxes = [
        box("标题", 1, 181, 408, 91),
        box("投资评级", 1, 21, 151, 134),
        box("事件：营收增长。", 1, 203, 568, 181),
        box("丁一SAC", 1, 19, 109, 194),
        box("渠道方面……", 1, 203, 569, 219),
        box("联系人", 1, 20, 52, 246),
        box("第二页正文", 2, 42, 543, 80),
    ]
    texts = [b["text"] for b in Pdf.order_by_column(boxes)]
    assert texts == ["投资评级", "丁一SAC", "联系人", "标题", "事件：营收增长。", "渠道方面……", "第二页正文"], texts


def test_single_column_page_unchanged():
    boxes = [
        box("全宽标题", 1, 42, 543, 50),
        box("左边短行", 1, 42, 100, 80),
        box("右边短行", 1, 300, 543, 80),
        box("全宽正文", 1, 42, 543, 110),
    ]
    assert Pdf.order_by_column(boxes) == boxes


def test_real_pdf(path):
    items = chunk(Path(path).name, binary=Path(path).read_bytes(), callback=lambda prog=None, msg="": None)
    for it in items:
        pages = sorted(set(it.get("page_num_int", [])))
        assert not pages or pages[-1] - pages[0] <= 1, (pages, it["content_with_weight"][:80])
        text = it["content_with_weight"]
        assert not ("SAC：" in text and "渠道方面" in text), text[:120]
    print(f"   {len(items)} 个块，均未跨页拼接，侧栏未插入正文")


if __name__ == "__main__":
    tests = [test_merge_breaks_at_page_change, test_merge_keeps_page_spanning_box_with_next_page,
             test_merge_without_positions_unchanged, test_sidebar_ordered_after_main_column,
             test_single_column_page_unchanged]
    if len(sys.argv) > 1:
        tests.append(partial(test_real_pdf, sys.argv[1]))
    failed = 0
    for t in tests:
        name = getattr(t, "__name__", "test_real_pdf")
        try:
            t()
            print(f"✅ {name}")
        except Exception as e:
            failed += 1
            print(f"❌ {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)

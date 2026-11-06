"""compact facts 投影自测。"""
from __future__ import annotations

import json

from DomToExcel.tools.web import _compact_facts


def test_compact_facts_keeps_structure_and_drops_noise() -> None:
    full = {
        "module": "采购订单",
        "path": "/bill/purchase_order",
        "list": {
            "buttons": ["新增"],
            "table_headers": ["单据编号"],
            "search_items": [{
                "label": "单据编号",
                "controls": [{"kind": "input", "attrs": {"placeholder": "请输入"}}],
            }],
            "row_count": 10,
            "options": {"供应商": ["A", "B"]},
            "html": "<html>noise</html>",
        },
        "add": {
            "fields": [{
                "label": "供应商",
                "required": True,
                "controls": [{"kind": ".ant-select", "attrs": {"placeholder": "请选择"}}],
            }],
            "detail_headers": ["#", "条码", "品牌", "品牌"],
            "detail_row": {"inputs": [{"id": "x"}]},
            "buttons": ["保存"],
            "options": {"供应商": ["A"]},
            "link_modal": {
                "title": "请选择请购单",
                "table_headers": ["单据编号"],
                "rows": [{"cells": [str(i)], "selected": False} for i in range(5)],
                "selection_type": "radio",
            },
            "product_modal": {
                "title": "选择商品",
                "table_headers": ["条码"],
                "rows": [{"cells": [str(i)], "selected": False} for i in range(5)],
                "selection_type": "checkbox",
            },
        },
    }

    compact = _compact_facts(full)
    text = json.dumps(compact, ensure_ascii=False)

    assert "html" not in text
    assert compact["add"]["detail_headers"] == ["#", "条码", "品牌", "品牌"]
    assert len(compact["add"]["link_modal"]["rows"]) == 3
    assert len(compact["add"]["product_modal"]["rows"]) == 3
    assert compact["add"]["link_modal"]["selection_type"] == "radio"
    assert compact["add"]["product_modal"]["selection_type"] == "checkbox"
    assert compact["list"]["search_items"][0]["controls"][0]["placeholder"] == "请输入"

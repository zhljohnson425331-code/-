# -*- coding: utf-8 -*-
"""
发票整理器 · MCP server
========================
把核心数据层（invoice_lib）暴露为可被 AI / 其他工作流直接调用的 MCP 工具，
不必依赖自然语言对话即可查票、记账、出报表。

运行（stdio 模式，供 MCP 客户端连接）:
    python mcp_server.py
配置: 与 invoice_lib 共用 INVOICE_HOME / INVOICE_PORT / INVOICE_DROP_DIR 环境变量。
依赖: mcp>=2  (pip install mcp)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import invoice_lib as lib

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("发票整理器")


@mcp.tool()
def invoice_add(invoice_no: str, amount: float, date: str = "", category: str = "",
                seller_name: str = "", buyer_name: str = "", tax_amount: float = 0.0,
                note: str = "", image_path: str = "") -> dict:
    """录入一张发票。invoice_no 必填；amount 金额(元)；date 开票日期 YYYY-MM-DD；
    category 类别(如 餐饮/停车费)；seller_name 销方；自动查重拦截。"""
    return lib.add_invoice({
        "invoice_no": invoice_no, "amount": amount, "date": date, "category": category,
        "seller_name": seller_name, "buyer_name": buyer_name, "tax_amount": tax_amount,
        "note": note, "image_path": image_path,
    })


@mcp.tool()
def invoice_list(status: str = "", category: str = "", keyword: str = "",
                 limit: int = 50) -> list:
    """查询发票列表。status 如 待报销/已提交/已报销/已删除；category 类别；
    keyword 关键词(发票号/销方/备注)；返回最多 limit 条(按日期倒序)。"""
    rows = lib.query(status=status or None, category=category or None,
                     keyword=keyword or None)
    return rows[:limit]


@mcp.tool()
def invoice_stats() -> dict:
    """返回统计：总数、总金额、待报销金额、按状态/类别汇总。"""
    return lib.get_stats()


@mcp.tool()
def invoice_monthly(months: int = 6) -> list:
    """最近 months 个月的发票数与金额趋势（供图表）。"""
    return lib.get_monthly_summary(months=months)


@mcp.tool()
def invoice_categories() -> list:
    """返回所有类别及其发票数（用于归类/归一）。"""
    return lib.distinct_categories()


@mcp.tool()
def invoice_update_status(invoice_id: int, status: str) -> dict:
    """更新某张发票报销状态：待报销 → 已提交 → 已报销。"""
    return lib.update_status(invoice_id, status)


@mcp.tool()
def invoice_delete(invoice_id: int) -> dict:
    """软删除（进回收站，可恢复）。"""
    return lib.soft_delete(invoice_id)


@mcp.tool()
def invoice_restore(invoice_id: int) -> dict:
    """从回收站恢复。"""
    return lib.restore(invoice_id)


@mcp.tool()
def invoice_export(format: str = "excel", ids: str = "") -> dict:
    """导出发票。format=excel 导出 xlsx；ids 形如 '1,2,3' 指定子集，空=全部。"""
    id_list = [int(x) for x in ids.split(",") if x.strip()] if ids else None
    if format == "excel":
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "invoices_export.xlsx")
        return lib.export_excel(out, id_list)
    return {"ok": False, "msg": "仅支持 excel"}


@mcp.tool()
def invoice_recalculate_tax() -> dict:
    """按当前税率规则重算全部发票的税额/不含税/可抵扣/认证到期（覆盖手动税率）。"""
    return lib.recalculate_tax()


@mcp.tool()
def invoice_tax_rules() -> dict:
    """返回当前税率规则配置（类别关键词 → 税率 → 是否可抵扣）。"""
    return lib.get_tax_rules()


if __name__ == "__main__":
    mcp.run()

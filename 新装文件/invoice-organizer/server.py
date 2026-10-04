# -*- coding: utf-8 -*-
"""
发票整理器 · 网页后端（只读）
================================
为本地网页提供数据 API + 静态页。录入不在此处（走 WorkBuddy 技能写 SQLite）。
运行（managed python venv）:
  python server.py            # 默认 http://127.0.0.1:8731
依赖: flask, openpyxl（导出 Excel 用）
"""
import os
import sys
import time
import threading
from flask import Flask, send_from_directory, jsonify, request, send_file, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import invoice_lib as lib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 兼容两种布局：web/ 子目录（本机 OneDrive 版）或 index.html 与脚本同级（门户技能包拍平版）
if os.path.isfile(os.path.join(BASE_DIR, "web", "index.html")):
    WEB_DIR = os.path.join(BASE_DIR, "web")
else:
    WEB_DIR = BASE_DIR
PORT = int(os.environ.get("INVOICE_PORT", "8731"))

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    # 直接读文件返回，避开 Flask send_file 在中文路径 + 文本文件上的 0 字节问题
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html; charset=utf-8")


@app.route("/api/invoices")
def api_invoices():
    status = request.args.get("status")
    category = request.args.get("category")
    kw = request.args.get("kw")
    rows = lib.query(status=status, category=category, keyword=kw)
    return jsonify(rows)


@app.route("/api/stats")
def api_stats():
    return jsonify(lib.get_stats())


@app.route("/api/duplicates")
def api_duplicates():
    return jsonify(lib.find_duplicates())


@app.route("/api/expiring")
def api_expiring():
    days = int(request.args.get("days", 30))
    return jsonify(lib.get_expiring(days))


@app.route("/api/export_excel", methods=["GET", "POST"])
def api_export_excel():
    ids = None
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
    else:
        p = request.args.get("ids")
        if p:
            ids = [int(x) for x in p.split(",") if x.strip()]
    out = os.path.join(BASE_DIR, "invoices_export.xlsx")
    res = lib.export_excel(out, ids)
    if not res.get("ok"):
        return jsonify(res), 404
    return send_file(
        res["path"],
        as_attachment=True,
        download_name="发票清单.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/print_a4", methods=["GET", "POST"])
def api_print_a4():
    """A4 两联排版打印页：GET=全部，POST {ids:[...]}=选中。返回 HTML，打开即调起打印。"""
    ids = None
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
    else:
        p = request.args.get("ids")
        if p:
            ids = [int(x) for x in p.split(",") if x.strip()]
    html = lib.export_a4_html(ids)
    if not html:
        return jsonify({"ok": False, "msg": "没有可打印的发票（可能勾选的 id 不存在）"}), 404
    return Response(html, mimetype="text/html; charset=utf-8")


# ---------------- 跨机实时同步（两台电脑共用同一份 OneDrive 账本） ----------------
# 第二台电脑上已开着的网页，必须能自动看到第一台刚录入的发票，否则等于数据不同步。
# 做法：后台线程按账本文件 mtime 变化触发合并（仅在另一台写入时才真正重读，开销极低）。
_RESYNC = {"mtime": 0.0, "lock": threading.Lock()}


def _ledger_changed():
    try:
        return os.path.getmtime(lib.LEDGER_PATH) != _RESYNC["mtime"]
    except OSError:
        return False


def _resync_once():
    """从共享账本合并另一台电脑录入的新发票。返回本次合并条数。线程安全。"""
    with _RESYNC["lock"]:
        if not _ledger_changed():
            return 0
        try:
            n = lib.sync_from_ledger()
        except Exception as e:
            print(f"[resync] 合并失败: {e}", flush=True)
            return 0
        try:
            _RESYNC["mtime"] = os.path.getmtime(lib.LEDGER_PATH)
        except OSError:
            pass
        if n:
            print(f"[resync] 已从共享账本合并 {n} 条新发票", flush=True)
        return n


def _start_resync_thread(interval=5):
    def loop():
        while True:
            try:
                _resync_once()
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.name = "ledger-resync"
    t.start()


@app.route("/api/sync")
def api_sync():
    """手动触发：立即从共享账本合并另一台电脑刚录入的发票。"""
    n = _resync_once()
    return jsonify({"merged": n, "total": len(lib.get_all())})


@app.route("/api/monthly")
def api_monthly():
    months = int(request.args.get("months", 6))
    return jsonify(lib.get_monthly_summary(months=months))


@app.route("/api/categories")
def api_categories():
    return jsonify(lib.distinct_categories())


@app.route("/api/thumb/<int:gid>")
def api_thumb(gid):
    """返回某张发票的原图 base64（用于表格缩略图）。无图返回 null。"""
    row = lib.get_by_ids([gid])
    if not row:
        return jsonify({"img": None})
    return jsonify({"img": lib._img_b64(row[0].get("image_path"))})


@app.route("/api/invoices/<int:gid>/status", methods=["POST"])
def api_set_status(gid):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if not status:
        return jsonify({"ok": False, "msg": "缺少 status"}), 400
    try:
        res = lib.update_status(gid, status)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"失败: {e}"}), 500
    return jsonify(res)


@app.route("/api/invoices/<int:gid>/delete", methods=["POST"])
def api_delete(gid):
    try:
        res = lib.soft_delete(gid)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"失败: {e}"}), 500
    return jsonify(res)


@app.route("/api/invoices/<int:gid>/restore", methods=["POST"])
def api_restore(gid):
    try:
        res = lib.restore(gid)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"失败: {e}"}), 500
    return jsonify(res)


@app.route("/api/normalize", methods=["POST"])
def api_normalize():
    data = request.get_json(silent=True) or {}
    old, new = data.get("old"), data.get("new")
    if not old or not new:
        return jsonify({"ok": False, "msg": "缺少 old/new"}), 400
    try:
        res = lib.rename_category(old, new)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"失败: {e}"}), 500
    return jsonify(res)


# ---------------- 专业财务 API（进项税 / 多维核算 / 预算 / 报销单 / 台账） ----------------
@app.route("/api/tax_summary")
def api_tax_summary():
    return jsonify(lib.get_tax_summary())


@app.route("/api/projects")
def api_projects():
    return jsonify(lib.distinct_projects())


@app.route("/api/departments")
def api_departments():
    return jsonify(lib.distinct_departments())


@app.route("/api/cost_centers")
def api_cost_centers():
    return jsonify(lib.distinct_cost_centers())


@app.route("/api/cross_summary")
def api_cross_summary():
    dim = request.args.get("dim", "project")
    return jsonify(lib.cross_summary(dim=dim))


@app.route("/api/invoices/<int:gid>/fields", methods=["POST"])
def api_set_fields(gid):
    data = request.get_json(silent=True) or {}
    try:
        res = lib.update_fields(gid, data)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"失败: {e}"}), 500
    return jsonify(res)


@app.route("/api/budget", methods=["GET", "POST"])
def api_budget():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        return jsonify(lib.set_budget(data))
    return jsonify(lib.budget_usage())


@app.route("/api/export_reimburse", methods=["GET", "POST"])
def api_export_reimburse():
    ids = None
    group_by = "none"
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
        group_by = data.get("group_by") or "none"
    else:
        p = request.args.get("ids")
        if p:
            ids = [int(x) for x in p.split(",") if x.strip()]
        group_by = request.args.get("group_by") or "none"
    html = lib.export_reimburse(ids, group_by=group_by)
    if not html:
        return jsonify({"ok": False, "msg": "没有可生成报销单的发票"}), 404
    return Response(html, mimetype="text/html; charset=utf-8")


@app.route("/api/tax_rules", methods=["GET", "POST"])
def api_tax_rules():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        return jsonify(lib.save_tax_rules(data))
    return jsonify(lib.get_tax_rules())


@app.route("/api/recalculate_tax", methods=["POST"])
def api_recalculate_tax():
    try:
        return jsonify(lib.recalculate_tax())
    except Exception as e:
        return jsonify({"ok": False, "msg": f"失败: {e}"}), 500


@app.route("/api/export_ledger", methods=["GET", "POST"])
def api_export_ledger():
    ids = None
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        ids = data.get("ids")
    else:
        p = request.args.get("ids")
        if p:
            ids = [int(x) for x in p.split(",") if x.strip()]
    res = lib.export_ledger(ids)
    if not res.get("ok"):
        return jsonify(res), 404
    return send_file(
        res["path"], as_attachment=True, download_name="财务台账.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    lib.init_db()
    _start_resync_thread(interval=5)
    print(f"发票整理器网页已启动: http://127.0.0.1:{PORT}（已开启跨机账本实时同步，约5秒一次）")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)

# -*- coding: utf-8 -*-
"""
发票整理器 · 跨机共享数据层
================================
设计目标：三台电脑（同 OneDrive 账号）各自都能「拖拽投递 -> 自动整理」，
且数据汇总到同一份，不会因为 SQLite 被云盘同步而损坏。

架构（关键）：
  · 共享持久层（放 OneDrive，三端同步）：
      BASE_DIR/store/ledger.jsonl   —— 追加式账本，每行一条发票记录（源真相，sync 安全）
      BASE_DIR/store/invoices/      —— 发票原图/PDF（不可变，sync 安全）
      BASE_DIR/store/_inbox/        —— 监视器投递的待处理原文件
      BASE_DIR/store/pending.jsonl  —— 待 AI 提取的队列
  · 本机缓存（不放云盘，每台机器独立）：
      ~/.invoice_organizer/cache.db  —— SQLite 快速索引，每次启动从 ledger 合并

为什么这样：SQLite 直接放 OneDrive 同步会被中途改写导致损坏；改用语义安全的
「追加写账本 + 本机缓存」——写入同时落账本(可恢复)与缓存(快)，任何机器开机先
从账本合并，三端数据自然一致。单人顺次使用，几乎不会冲突。

入口：
  1) WorkBuddy 技能（对话录入）：看发票图 -> 多模态提取 -> add_invoice 写库
  2) 网页后端 server.py（只读展示/导出）：查缓存返回 JSON / 导出
  3) watch_drop.py（每台机器常驻）：监视本地投递文件夹 -> 暂存到 _inbox -> 入 pending 队列
  4) 技能「整理投递」：读 pending -> AI 提取 -> add_invoice -> 标记完成

全程本地、免费，不调用任何外部付费 API。
命令行：
  python invoice_lib.py init
  python invoice_lib.py add --json '{...}'
  python invoice_lib.py list [--status 待报销] [--category 餐饮] [--kw 关键词]
  python invoice_lib.py stats
  python invoice_lib.py export-json [--out invoices.json]
  python invoice_lib.py export-excel [--out invoices.xlsx] [--ids 1,2,3]
  python invoice_lib.py duplicates
  python invoice_lib.py expiring [--days 30]
  python invoice_lib.py print-a4 [--out x.html] [--ids 1,2,3]
  python invoice_lib.py pending            # 列出待处理投递
  python invoice_lib.py stage --src 路径    # 手动投递一个文件
  python invoice_lib.py rebuild            # 从账本重建本机缓存（恢复用）
"""

import os
import sys
import json
import base64
import sqlite3
import socket
import time
import contextlib
import datetime as _dt

# ---------------- 路径解析（支持三端不同 OneDrive 位置） ----------------
def _detect_home():
    """优先 INVOICE_HOME 环境变量；其次自动探测 OneDrive；默认 __file__ 所在目录。
    不写死任何用户名/盘符，别人把 tool 放任意位置都能用。"""
    e = os.environ.get("INVOICE_HOME")
    if e and os.path.isdir(e):
        return e
    od = os.environ.get("OneDrive")
    if od and os.path.isdir(od):
        cand = os.path.join(od, "发票整理器", "tool")
        if os.path.isdir(cand):
            return cand
    # 默认：脚本自身所在目录（开箱即用，跨平台跨用户）
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _detect_home()
STORE_DIR = os.path.join(BASE_DIR, "store")
ARTIFACT_DIR = os.path.join(STORE_DIR, "invoices")   # 共享：原图/PDF
INBOX_DIR = os.path.join(STORE_DIR, "_inbox")        # 共享：待处理投递
LEDGER_PATH = os.path.join(STORE_DIR, "ledger.jsonl")  # 共享：追加式账本
PENDING_PATH = os.path.join(STORE_DIR, "pending.jsonl")  # 共享：待 AI 提取队列
CACHE_DB = os.path.join(os.path.expanduser("~"), ".invoice_organizer", "cache.db")  # 本机
TAX_RULES_PATH = os.path.join(BASE_DIR, "tax_rules.json")  # 共享：税率规则（可配置）

DEFAULT_DUE_DAYS = 360
_SYNCED = False  # 进程内只合并一次账本

# ---------------- 日志 / 备份 / 列集合（专业版加固） ----------------
import shutil

def _log(msg):
    """写本地运行日志（不进云盘，避免 OneDrive 同步写冲突）。"""
    try:
        d = os.path.dirname(CACHE_DB)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "app.log"), "a", encoding="utf-8") as f:
            f.write(f"[{_now()}] {msg}\n")
    except Exception:
        pass

COLUMNS = {"invoice_no", "invoice_code", "date", "amount", "tax_amount",
           "buyer_name", "buyer_tax_no", "seller_name", "seller_tax_no",
           "category", "expense_type", "status", "image_path", "note", "due_date",
           "tax_rate", "ex_tax_amount", "is_deductible", "deductible_tax",
           "cert_due", "project", "department", "cost_center", "account_subject"}

# 专业财务版新增列（旧本机缓存库需 ALTER 补齐；首次新建库直接建全）
NEW_COLS = {
    "tax_rate": "REAL",
    "ex_tax_amount": "REAL",
    "is_deductible": "INTEGER DEFAULT 1",
    "deductible_tax": "REAL",
    "cert_due": "TEXT",
    "project": "TEXT",
    "department": "TEXT",
    "cost_center": "TEXT",
    "account_subject": "TEXT",
}
BUDGET_PATH = os.path.join(STORE_DIR, "budget.json")

_BACKEDUP_TODAY = [""]
def _backup_ledger():
    """按日快照账本到 store/backups/（可恢复）。每天只备一次，开销极低。"""
    try:
        d = _dt.date.today().strftime("%Y%m%d")
        if _BACKEDUP_TODAY[0] == d:
            return
        _BACKEDUP_TODAY[0] = d
        bdir = os.path.join(STORE_DIR, "backups")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(LEDGER_PATH, os.path.join(bdir, f"ledger_{d}.jsonl"))
    except Exception:
        pass


# ---------------- 并发文件锁（本机多进程 / 多 Agent 安全写账本） ----------------
_LEDGER_LOCK_PATH = os.path.join(os.path.dirname(CACHE_DB), "ledger.lock")


@contextlib.contextmanager
def _ledger_lock():
    """本机级并发保护：用锁文件防止同机多进程/线程交错写坏账本。
    跨机靠 OneDrive 顺序写（最佳实践为单写端）。尽力加锁，超时则降级放行，绝不死锁。"""
    lockf = None
    acquired = False
    try:
        lockf = open(_LEDGER_LOCK_PATH, "w")
        if os.name == "nt":
            import msvcrt
            for _ in range(60):
                try:
                    msvcrt.locking(lockf.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            acquired = True
        yield
    finally:
        if lockf:
            try:
                if os.name == "nt" and acquired:
                    import msvcrt
                    msvcrt.locking(lockf.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                lockf.close()
            except Exception:
                pass


def machine_name():
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown"


# ---------------- 缓存（本机 SQLite） ----------------
def _now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_dirs():
    for d in (STORE_DIR, ARTIFACT_DIR, INBOX_DIR):
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)


def _cache_conn():
    _ensure_dirs()
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no   TEXT,
            invoice_code TEXT,
            date         TEXT,
            amount       REAL,
            tax_amount   REAL,
            buyer_name   TEXT,
            buyer_tax_no TEXT,
            seller_name  TEXT,
            seller_tax_no TEXT,
            category     TEXT,
            expense_type TEXT,
            status       TEXT DEFAULT '待报销',
            image_path   TEXT,
            note         TEXT,
            due_date     TEXT,
            created_at   TEXT,
            tax_rate     REAL,
            ex_tax_amount REAL,
            is_deductible INTEGER DEFAULT 1,
            deductible_tax REAL,
            cert_due     TEXT,
            project      TEXT,
            department   TEXT,
            cost_center  TEXT,
            account_subject TEXT
        )
    """)
    # 旧库（本次升级前已存在）补齐新列，已存在则忽略
    for _col, _typ in NEW_COLS.items():
        try:
            conn.execute(f"ALTER TABLE invoices ADD COLUMN {_col} {_typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def backfill_tax():
    """幂等：给升级前已存在的旧发票补算税额/不含税/可抵扣/认证到期。只跑一次。"""
    flag = os.path.join(STORE_DIR, ".tax_backfilled")
    if os.path.isfile(flag):
        return 0
    conn = _cache_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM invoices WHERE tax_amount IS NULL OR tax_rate IS NULL OR is_deductible IS NULL"
    ).fetchall()
    c = conn.cursor()
    n = 0
    for r in rows:
        rate = guess_tax_rate(r["category"])
        amt = r["amount"] or 0
        ex_tax = round(amt / (1 + rate), 2) if amt else 0.0
        tax = round(amt - ex_tax, 2) if amt else 0.0
        ded = default_deductible(r["category"])
        cd = None
        if ded and r["date"]:
            try:
                d = _dt.datetime.strptime(r["date"], "%Y-%m-%d")
                cd = (d + _dt.timedelta(days=360)).strftime("%Y-%m-%d")
            except ValueError:
                cd = None
        c.execute(
            "UPDATE invoices SET tax_rate=?, ex_tax_amount=?, tax_amount=?, "
            "is_deductible=?, deductible_tax=?, cert_due=? WHERE id=?",
            (rate, ex_tax, tax, int(ded), tax if ded else 0.0, cd, r["id"]),
        )
        _append_ledger({
            "invoice_no": r["invoice_no"], "tax_rate": rate,
            "ex_tax_amount": ex_tax, "tax_amount": tax,
            "is_deductible": int(ded), "deductible_tax": tax if ded else 0.0,
            "cert_due": cd,
        }, op="update")
        n += 1
    conn.commit()
    conn.close()
    open(flag, "w", encoding="utf-8").close()
    if n:
        _log(f"backfill_tax 已补算 {n} 张旧发票的税额/进项税")
    return n


def init_db():
    _cache_conn().close()
    global _SYNCED
    if not _SYNCED:
        sync_from_ledger()
        backfill_tax()
        _SYNCED = True


def _conn():
    init_db()
    return sqlite3.connect(CACHE_DB)


# ---------------- 账本（共享、追加写、sync 安全） ----------------
def _uid_of(rec):
    return f"{(rec.get('invoice_no') or '').strip()}|{(rec.get('invoice_code') or '').strip()}"


def _append_ledger(rec, op="add"):
    """写入一条账本事件。op: add(录入) / update(状态或字段变更) / delete(软删除)。"""
    _ensure_dirs()
    line = json.dumps({
        "op": op,
        "uid": _uid_of(rec),
        "machine": machine_name(),
        "ts": _now(),
        "rec": rec,
    }, ensure_ascii=False)
    with _ledger_lock():
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    _backup_ledger()


def sync_from_ledger():
    """把账本里本机缓存还没有的发票合并进来（按 invoice_no 去重）。"""
    if not os.path.isfile(LEDGER_PATH):
        return 0
    conn = _cache_conn()
    c = conn.cursor()
    existing = set(r[0] for r in c.execute("SELECT invoice_no FROM invoices").fetchall())
    n = 0
    with _ledger_lock():
        raw_lines = open(LEDGER_PATH, encoding="utf-8").read().splitlines()
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        op = obj.get("op", "add")
        rec = obj.get("rec", obj)
        no = (rec.get("invoice_no") or "").strip()
        if op == "add":
            if not no or no in existing:
                continue
            _insert_row(c, rec)
            existing.add(no)
            n += 1
        elif op == "update":
            if not no:
                continue
            sets = {k: v for k, v in rec.items()
                    if k not in ("invoice_no",) and k in COLUMNS}
            if sets:
                sql = "UPDATE invoices SET " + ", ".join(f"{k}=?" for k in sets) \
                      + " WHERE invoice_no=?"
                c.execute(sql, list(sets.values()) + [no])
        elif op == "delete":
            if not no:
                continue
            c.execute("UPDATE invoices SET status='已删除' WHERE invoice_no=?", (no,))
    conn.commit()
    conn.close()
    return n


def rebuild():
    """清空本机缓存并从账本重建（账本损坏/换机恢复用）。"""
    if os.path.isfile(CACHE_DB):
        os.remove(CACHE_DB)
    sync_from_ledger()
    return {"ok": True, "cache": CACHE_DB}


# ---------------- 写入 ----------------
def _insert_row(c, rec):
    c.execute("""
        INSERT INTO invoices
        (invoice_no, invoice_code, date, amount, tax_amount, buyer_name,
         buyer_tax_no, seller_name, seller_tax_no, category, expense_type,
         status, image_path, note, due_date, created_at,
         tax_rate, ex_tax_amount, is_deductible, deductible_tax, cert_due,
         project, department, cost_center, account_subject)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        (rec.get("invoice_no") or "").strip() or None,
        (rec.get("invoice_code") or "").strip() or None,
        (rec.get("date") or "").strip() or None,
        _norm_amount(rec.get("amount")),
        _norm_amount(rec.get("tax_amount")),
        (rec.get("buyer_name") or "").strip() or None,
        (rec.get("buyer_tax_no") or "").strip() or None,
        (rec.get("seller_name") or "").strip() or None,
        (rec.get("seller_tax_no") or "").strip() or None,
        (rec.get("category") or "其他").strip(),
        (rec.get("expense_type") or "").strip() or None,
        (rec.get("status") or "待报销").strip(),
        (rec.get("image_path") or "").strip() or None,
        (rec.get("note") or "").strip() or None,
        (rec.get("due_date") or "").strip() or None,
        rec.get("created_at") or _now(),
        _norm_amount(rec.get("tax_rate")) if rec.get("tax_rate") is not None else None,
        _norm_amount(rec.get("ex_tax_amount")),
        int(bool(rec.get("is_deductible", 1))),
        _norm_amount(rec.get("deductible_tax")),
        (rec.get("cert_due") or "").strip() or None,
        (rec.get("project") or "").strip() or None,
        (rec.get("department") or "").strip() or None,
        (rec.get("cost_center") or "").strip() or None,
        (rec.get("account_subject") or "").strip() or None,
    ))


def _norm_amount(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


DEFAULT_TAX_RULES = {
    "rules": [
        {"keywords": ["餐饮", "餐费", "餐", "伙食", "食堂"], "tax_rate": 0.06, "is_deductible": False},
        {"keywords": ["物业", "停车", "租赁", "租金", "房租"], "tax_rate": 0.09, "is_deductible": True},
        {"keywords": ["住宿", "酒店", "宾馆", "服务", "咨询", "会议", "培训", "广告"], "tax_rate": 0.06, "is_deductible": True},
        {"keywords": ["运输", "物流", "快递", "油", "加油", "汽油", "柴油"], "tax_rate": 0.09, "is_deductible": True},
        {"keywords": ["水电", "电", "水", "燃气", "通信", "宽带", "话费"], "tax_rate": 0.13, "is_deductible": True},
    ],
    "default_tax_rate": 0.13,
    "default_is_deductible": True,
}


def get_tax_rules():
    """读税率规则；文件不存在则写入默认规则并返回。"""
    if os.path.isfile(TAX_RULES_PATH):
        try:
            return json.load(open(TAX_RULES_PATH, encoding="utf-8"))
        except Exception:
            pass
    json.dump(DEFAULT_TAX_RULES, open(TAX_RULES_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return DEFAULT_TAX_RULES


def save_tax_rules(rules):
    """保存税率规则（同时落盘）。"""
    try:
        assert isinstance(rules, dict), "规则必须是对象"
        assert isinstance(rules.get("rules", []), list), "rules 必须是数组"
        json.dump(rules, open(TAX_RULES_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def guess_tax_rate(category):
    """按可配置税率规则推断增值税税率（本地规则、可手动覆盖）。仅作默认建议。"""
    c = (category or "").strip()
    for rule in get_tax_rules().get("rules", []):
        if any(k in c for k in rule.get("keywords", [])):
            return float(rule.get("tax_rate", 0.13))
    return float(get_tax_rules().get("default_tax_rate", 0.13))


def default_deductible(category):
    """是否可抵扣进项税（按规则，可手动覆盖）。餐饮/福利等默认不可抵扣。"""
    c = (category or "").strip()
    for rule in get_tax_rules().get("rules", []):
        if any(k in c for k in rule.get("keywords", [])):
            return bool(rule.get("is_deductible", True))
    return bool(get_tax_rules().get("default_is_deductible", True))


def recalculate_tax():
    """按当前税率规则重算全部发票的税额/不含税/可抵扣/认证到期（覆盖手动税率）。"""
    init_db()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM invoices WHERE status != '已删除'").fetchall()
    c = conn.cursor()
    n = 0
    for r in rows:
        cat = r["category"] or "其他"
        rate = guess_tax_rate(cat)
        amt = r["amount"] or 0
        ex_tax = round(amt / (1 + rate), 2) if amt else 0.0
        tax = round(amt - ex_tax, 2) if amt else 0.0
        ded = default_deductible(cat)
        ded_tax = tax if ded else 0.0
        cd = None
        if ded and r["date"]:
            try:
                d = _dt.datetime.strptime(r["date"], "%Y-%m-%d")
                cd = (d + _dt.timedelta(days=360)).strftime("%Y-%m-%d")
            except ValueError:
                cd = None
        c.execute(
            "UPDATE invoices SET tax_rate=?, ex_tax_amount=?, tax_amount=?, "
            "is_deductible=?, deductible_tax=?, cert_due=? WHERE id=?",
            (rate, ex_tax, tax, int(ded), ded_tax, cd, r["id"]))
        _append_ledger({
            "invoice_no": r["invoice_no"], "tax_rate": rate,
            "ex_tax_amount": ex_tax, "tax_amount": tax,
            "is_deductible": int(ded), "deductible_tax": ded_tax,
            "cert_due": cd,
        }, op="update")
        n += 1
    conn.commit()
    conn.close()
    _log(f"recalculate_tax 按规则重算 {n} 张")
    return {"ok": True, "count": n}


def add_invoice(fields: dict):
    """写入一条发票。返回 dict: {id, duplicate(bool), msg}。
    同步落：本机缓存 + 共享账本（可恢复）。"""
    init_db()
    inv_no = (fields.get("invoice_no") or "").strip()
    inv_code = (fields.get("invoice_code") or "").strip()
    if not inv_no:
        return {"id": None, "duplicate": False, "msg": "缺少发票号码，未写入"}

    conn = _conn()
    c = conn.cursor()
    c.execute(
        "SELECT id FROM invoices WHERE invoice_no=? AND (invoice_code=? OR (invoice_code IS NULL AND ?=''))",
        (inv_no, inv_code, inv_code),
    )
    row = c.fetchone()
    if row:
        conn.close()
        return {"id": row[0], "duplicate": True,
                "msg": f"发票号码 {inv_no} 已存在（id={row[0]}），视为重复，未重复写入"}

    due = fields.get("due_date")
    if not due and fields.get("date"):
        try:
            d = _dt.datetime.strptime(fields["date"], "%Y-%m-%d")
            due = (d + _dt.timedelta(days=DEFAULT_DUE_DAYS)).strftime("%Y-%m-%d")
        except ValueError:
            due = None

    # ---- 专业财务：税额/不含税/可抵扣/认证到期 自动推导 ----
    amt = _norm_amount(fields.get("amount"))
    rate = fields.get("tax_rate")
    rate = _norm_amount(rate) if rate not in (None, "") else guess_tax_rate(fields.get("category"))
    given_tax = _norm_amount(fields.get("tax_amount"))
    if given_tax:
        tax = given_tax
        ex_tax = round(amt - tax, 2) if amt is not None else None
    else:
        ex_tax = round(amt / (1 + rate), 2) if amt is not None else None
        tax = round(amt - ex_tax, 2) if amt is not None else None
    ded = fields.get("is_deductible")
    ded = bool(int(ded)) if ded not in (None, "") else default_deductible(fields.get("category"))
    ded_tax = tax if ded else 0.0
    cert_due = None
    if ded and fields.get("date"):
        try:
            d = _dt.datetime.strptime(fields["date"], "%Y-%m-%d")
            cert_due = (d + _dt.timedelta(days=360)).strftime("%Y-%m-%d")
        except ValueError:
            cert_due = None

    rec = {
        "invoice_no": inv_no,
        "invoice_code": inv_code or None,
        "date": (fields.get("date") or "").strip() or None,
        "amount": amt,
        "tax_amount": tax,
        "buyer_name": (fields.get("buyer_name") or "").strip() or None,
        "buyer_tax_no": (fields.get("buyer_tax_no") or "").strip() or None,
        "seller_name": (fields.get("seller_name") or "").strip() or None,
        "seller_tax_no": (fields.get("seller_tax_no") or "").strip() or None,
        "category": (fields.get("category") or "其他").strip(),
        "expense_type": (fields.get("expense_type") or "").strip() or None,
        "status": (fields.get("status") or "待报销").strip(),
        "image_path": (fields.get("image_path") or "").strip() or None,
        "note": (fields.get("note") or "").strip() or None,
        "due_date": due,
        "created_at": _now(),
        "tax_rate": rate,
        "ex_tax_amount": ex_tax,
        "is_deductible": int(ded),
        "deductible_tax": ded_tax,
        "cert_due": cert_due,
        "project": (fields.get("project") or "").strip() or None,
        "department": (fields.get("department") or "").strip() or None,
        "cost_center": (fields.get("cost_center") or "").strip() or None,
        "account_subject": (fields.get("account_subject") or "").strip() or None,
    }
    _insert_row(c, rec)
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    _append_ledger(rec)  # 共享账本（同步安全、可恢复）
    return {"id": new_id, "duplicate": False, "msg": f"已写入 id={new_id}"}


# ---------------- 读取（全部走本机缓存） ----------------
def get_all():
    init_db()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM invoices ORDER BY date DESC, id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query(status=None, category=None, keyword=None):
    init_db()
    sql = "SELECT * FROM invoices WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if category:
        sql += " AND category=?"
        args.append(category)
    if keyword:
        sql += " AND (invoice_no LIKE ? OR buyer_name LIKE ? OR seller_name LIKE ? OR note LIKE ?)"
        kw = f"%{keyword}%"
        args += [kw, kw, kw, kw]
    sql += " ORDER BY date DESC, id DESC"
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_duplicates():
    init_db()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT invoice_no, invoice_code, COUNT(*) c, GROUP_CONCAT(id) ids "
        "FROM invoices GROUP BY invoice_no, invoice_code HAVING c>1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_expiring(days=30):
    init_db()
    today = _dt.date.today()
    limit = (today + _dt.timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM invoices WHERE due_date IS NOT NULL AND due_date <= ? "
        "AND status != '已报销' ORDER BY due_date ASC", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats():
    init_db()
    conn = _conn()
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    total_amt = c.execute("SELECT COALESCE(SUM(amount),0) FROM invoices").fetchone()[0]
    by_status = dict(c.execute(
        "SELECT status, COUNT(*) FROM invoices GROUP BY status").fetchall())
    by_cat = dict(c.execute(
        "SELECT category, COALESCE(SUM(amount),0) FROM invoices GROUP BY category").fetchall())
    pend_amt = c.execute(
        "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='待报销'").fetchone()[0]
    conn.close()
    return {
        "total": total,
        "total_amount": round(total_amt, 2),
        "pending_amount": round(pend_amt, 2),
        "by_status": by_status,
        "by_category": {k: round(v, 2) for k, v in by_cat.items()},
    }


def get_by_ids(ids):
    init_db()
    if not ids:
        return []
    conn = _conn()
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM invoices WHERE id IN ({placeholders}) ORDER BY date DESC, id DESC",
        ids,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------- 导出 ----------------
def export_json(out_path=None):
    if out_path is None:
        out_path = os.path.join(BASE_DIR, "invoices.json")
    data = {
        "exported_at": _now(),
        "stats": get_stats(),
        "invoices": get_all(),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out_path


def export_excel(out_path=None, ids=None):
    if out_path is None:
        out_path = os.path.join(BASE_DIR, "invoices.xlsx")
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return {"ok": False, "msg": "未安装 openpyxl，先跑: pip install openpyxl"}

    rows = get_all() if ids is None else get_by_ids(ids)
    if not rows:
        return {"ok": False, "msg": "没有可导出的发票（可能勾选的 id 不存在）"}
    wb = Workbook()
    ws = wb.active
    ws.title = "发票清单"
    headers = ["ID", "发票代码", "发票号码", "开票日期", "金额(元)", "税额(元)",
               "购方抬头", "购方税号", "销方", "销方税号", "类型",
               "报销类型", "状态", "过期日", "备注", "来源图", "录入时间"]
    ws.append(headers)
    head_fill = PatternFill("solid", fgColor="FF4D4F")
    for col, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append([
            r.get("id"), r.get("invoice_code"), r.get("invoice_no"), r.get("date"),
            r.get("amount"), r.get("tax_amount"), r.get("buyer_name"),
            r.get("buyer_tax_no"), r.get("seller_name"), r.get("seller_tax_no"),
            r.get("category"), r.get("expense_type"), r.get("status"),
            r.get("due_date"), r.get("note"), r.get("image_path"), r.get("created_at"),
        ])
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=6):
        for cell in row:
            if cell.value is not None:
                cell.number_format = "0.00"
    widths = [6, 14, 16, 12, 12, 10, 20, 20, 20, 20, 10, 12, 10, 12, 24, 28, 18]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = w
    wb.save(out_path)
    return {"ok": True, "path": out_path, "count": len(rows)}


def _fmt_amt(v):
    try:
        return f"¥{float(v):,.2f}" if v is not None else "—"
    except (TypeError, ValueError):
        return "—"


def _guess_mime(p):
    ext = os.path.splitext(p)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")


def _resolve_image(path):
    """返回可读的本地图片绝对路径，否则 None。优先共享 ARTIFACT_DIR / INBOX_DIR。"""
    if not path:
        return None
    base = os.path.basename(str(path))
    candidates = [path,
                  os.path.join(ARTIFACT_DIR, base),
                  os.path.join(INBOX_DIR, base),
                  os.path.join(BASE_DIR, base)]
    if not os.path.isabs(path):
        candidates.append(os.path.join(BASE_DIR, path))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _img_b64(path):
    """读图片 -> base64 data URI（Pillow 降采样）。PDF 渲染首页。"""
    p = _resolve_image(path)
    if not p:
        return None
    ext = os.path.splitext(p)[1].lower()
    if ext == ".pdf":
        return _pdf_page_b64(p)
    try:
        raw = open(p, "rb").read()
        mime = _guess_mime(p)
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(raw))
            maxedge = 1400
            if max(im.size) > maxedge:
                im.thumbnail((maxedge, maxedge))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82)
            raw = buf.getvalue()
            mime = "image/jpeg"
        except Exception:
            pass
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def _pdf_page_b64(pdf_path, maxedge=1400):
    """PDF 首页渲染为 JPEG base64（用于打印排版显示原票）。"""
    import pymupdf
    import io
    from PIL import Image
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    zoom = maxedge / page.rect.width
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    doc.close()
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


A4_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>发票打印 · A4两联排版</title>
<style>
  @page{size:A4;margin:0;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:#5b5f66;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}
  .page{width:210mm;height:297mm;margin:10px auto;background:#fff;padding:5mm;
        display:flex;flex-direction:column;justify-content:space-between;
        page-break-after:always;box-shadow:0 2px 10px rgba(0,0,0,.35);}
  .page:last-child{page-break-after:auto;}
  .card{flex:1;width:100%;border:1px solid #b8b8b8;display:flex;flex-direction:column;
        overflow:hidden;background:#fff;min-height:0;}
  .shot{width:100%;flex:1;min-height:0;object-fit:contain;background:#fafafa;display:block;}
  .cap{display:flex;flex-wrap:wrap;gap:3px 16px;align-items:center;padding:2.5mm 4mm;
       border-top:1px solid #cfcfcf;font-size:11px;color:#222;background:#f6f6f6;}
  .cap .cno{font-weight:700;}
  .cap .camt{font-weight:700;color:#b00000;}
  .cap .ccat{background:#eaf0ff;padding:1px 7px;border-radius:4px;}
  .nocard{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
          gap:10px;color:#888;min-height:0;}
  .nocap{font-size:13px;}
  .nofields div{display:flex;gap:8px;font-size:13px;}
  .nofields span{color:#999;min-width:52px;}
  .pfoot{text-align:center;color:#ddd;font-size:12px;padding:8px;}
  @media print{
    body{background:#fff;}
    .page{margin:0;box-shadow:none;}
    .pfoot{display:none;}
  }
</style></head>
<body>
__PAGES__
<div class="pfoot">共 __COUNT__ 张发票 · A4 两联排版（每页2张）</div>
<script>window.onload=function(){setTimeout(function(){window.print();},350);};</script>
</body></html>"""


def export_a4_html(ids=None):
    """生成 A4 两联排版打印页 HTML：每张 A4 排 2 张发票、排满整页。"""
    rows = get_all() if ids is None else get_by_ids(ids)
    if not rows:
        return None
    cards = []
    for r in rows:
        inv_no = r.get("invoice_no") or "—"
        date = r.get("date") or "—"
        amount_s = _fmt_amt(r.get("amount"))
        seller = r.get("seller_name") or ""
        category = r.get("category") or ""
        img = _img_b64(r.get("image_path"))
        if img:
            media = f'<img class="shot" src="{img}" alt="发票图">'
        else:
            media = (f'<div class="nocard">'
                     f'<div class="nocap">无来源图（仅字段）</div>'
                     f'<div class="nofields">'
                     f'<div><span>发票号</span><b>{inv_no}</b></div>'
                     f'<div><span>金额</span><b>{amount_s}</b></div>'
                     f'<div><span>日期</span><b>{date}</b></div>'
                     f'<div><span>销方</span><b>{seller or "—"}</b></div>'
                     f'</div></div>')
        cap_parts = [
            f'<span class="cno">发票号 {inv_no}</span>',
            f'<span class="camt">金额 {amount_s}</span>',
            f'<span class="cdate">{date}</span>',
        ]
        if category:
            cap_parts.append(f'<span class="ccat">{category}</span>')
        if seller:
            cap_parts.append(f'<span class="csell">{seller}</span>')
        cap = '<div class="cap">' + "".join(cap_parts) + '</div>'
        cards.append(f'<div class="card">{media}{cap}</div>')
    pages = []
    for i in range(0, len(cards), 2):
        pages.append('<div class="page">' + "".join(cards[i:i + 2]) + '</div>')
    html = (A4_HTML_TEMPLATE
            .replace("__PAGES__", "".join(pages))
            .replace("__COUNT__", str(len(rows))))
    return html


# ---------------- 投递 / 待处理队列（跨机拖拽用） ----------------
def stage_file(src_path):
    """把本地投递的文件暂存到共享 _inbox（带时间戳防重名），并入 pending 队列。
    返回 {ok, uid, artifact, msg}。真正的字段提取由 AI（整理投递）完成。"""
    _ensure_dirs()
    if not os.path.isfile(src_path):
        return {"ok": False, "msg": f"文件不存在: {src_path}"}
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(src_path)
    # 去掉可能的中文括号等易错字符，仅保留安全文件名
    safe = "".join(ch for ch in base if ch.isalnum() or ch in "._- ")
    dest = os.path.join(INBOX_DIR, f"{ts}_{safe}")
    import shutil
    shutil.copy2(src_path, dest)
    uid = f"{ts}_{base}"
    _append_pending({
        "uid": uid,
        "src": dest,
        "orig_name": base,
        "machine": machine_name(),
        "ts": _now(),
        "status": "pending",
    })
    return {"ok": True, "uid": uid, "artifact": dest,
            "msg": f"已投递到待处理队列: {base}"}


def _append_pending(entry):
    _ensure_dirs()
    with open(PENDING_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def list_pending():
    if not os.path.isfile(PENDING_PATH):
        return []
    out = []
    with open(PENDING_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("status") == "pending":
                out.append(obj)
    return out


def mark_pending_done(uid, invoice_id=None):
    """处理完成后把 pending 标记为 done（保留记录便于追溯）。"""
    if not os.path.isfile(PENDING_PATH):
        return
    kept = []
    with open(PENDING_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                kept.append(line)
                continue
            if obj.get("uid") == uid:
                obj["status"] = "done"
                obj["invoice_id"] = invoice_id
                kept.append(json.dumps(obj, ensure_ascii=False))
            else:
                kept.append(line)
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")


def _print_cli(rows):
    print(f"{'ID':>4} | {'发票号':<16} | {'日期':<10} | {'金额':>10} | {'类型':<6} | {'状态':<8}")
    print("-" * 70)
    for r in rows:
        print(f"{r.get('id'):>4} | {(r.get('invoice_no') or ''):<16} | "
              f"{(r.get('date') or ''):<10} | {float(r.get('amount') or 0):>10.2f} | "
              f"{(r.get('category') or ''):<6} | {(r.get('status') or ''):<8}")


# ---------------- 专业版扩展：统计 / 状态流转 / 软删 / 类别管理 ----------------
def get_monthly_summary(months=6):
    """最近 N 个月（含本月）的发票数与金额，供趋势图。"""
    init_db()
    today = _dt.date.today()
    y, m = today.year, today.month
    labels = []
    for _ in range(months):
        labels.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    labels = list(reversed(labels))
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT substr(date,1,7) ym, COUNT(*) c, COALESCE(SUM(amount),0) a "
        "FROM invoices WHERE status != '已删除' GROUP BY ym"
    ).fetchall()
    conn.close()
    data = {r["ym"]: {"count": r["c"], "amount": round(r["a"], 2)} for r in rows}
    return [{"month": lm,
             "count": data.get(lm, {}).get("count", 0),
             "amount": data.get(lm, {}).get("amount", 0.0)} for lm in labels]


def get_no_by_id(invoice_id):
    conn = _conn()
    r = conn.execute("SELECT invoice_no FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    conn.close()
    return r[0] if r else None


def update_status(invoice_id, status):
    """状态流转：待报销 → 已提交 → 已报销（跨机同步）。"""
    init_db()
    no = get_no_by_id(invoice_id)
    if not no:
        return {"ok": False, "msg": f"未找到 id={invoice_id}"}
    conn = _conn()
    c = conn.cursor()
    c.execute("UPDATE invoices SET status=? WHERE id=?", (status, invoice_id))
    conn.commit()
    conn.close()
    _append_ledger({"invoice_no": no, "status": status}, op="update")
    _log(f"状态更新 id={invoice_id} -> {status}")
    return {"ok": True, "msg": f"已更新为 {status}"}


def soft_delete(invoice_id):
    """软删除（进回收站，可恢复），不物理销毁，保留账本可恢复性。"""
    init_db()
    no = get_no_by_id(invoice_id)
    if not no:
        return {"ok": False, "msg": f"未找到 id={invoice_id}"}
    conn = _conn()
    c = conn.cursor()
    c.execute("UPDATE invoices SET status='已删除' WHERE id=?", (invoice_id,))
    conn.commit()
    conn.close()
    _append_ledger({"invoice_no": no, "status": "已删除"}, op="delete")
    _log(f"软删除 id={invoice_id}")
    return {"ok": True, "msg": "已移至回收站（可恢复）"}


def restore(invoice_id):
    """从回收站恢复。"""
    return update_status(invoice_id, "待报销")


def distinct_categories():
    init_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT category, COUNT(*) c FROM invoices WHERE status != '已删除' "
        "GROUP BY category ORDER BY c DESC"
    ).fetchall()
    conn.close()
    return [{"category": r[0], "count": r[1]} for r in rows]


def rename_category(old, new):
    """类别归一（如 餐费→餐饮），同步改缓存与账本事件，跨机生效。"""
    init_db()
    conn = _conn()
    c = conn.cursor()
    rows = c.execute("SELECT id, invoice_no FROM invoices WHERE category=?",
                     (old,)).fetchall()
    n = 0
    for rid, no in rows:
        c.execute("UPDATE invoices SET category=? WHERE id=?", (new, rid))
        _append_ledger({"invoice_no": no, "category": new}, op="update")
        n += 1
    conn.commit()
    conn.close()
    _log(f"类别归一 {old} -> {new} ({n} 条)")
    return {"ok": True, "count": n, "msg": f"已将 {old} 合并到 {new}（{n} 条）"}


# ---------------- 专业财务扩展：进项税 / 多维核算 / 预算 / 报销单 / 台账 ----------------
def get_tax_summary():
    """进项税可抵扣汇总 + 临期专票提醒。"""
    init_db()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM invoices WHERE status != '已删除'").fetchall()
    conn.close()
    total_amt = total_tax = ded_total = nonded_total = 0.0
    ded_by_cat = {}
    cert_expiring = []
    today = _dt.date.today()
    for r in rows:
        a = r["amount"] or 0
        t = r["tax_amount"] or 0
        total_amt += a
        total_tax += t
        if r["is_deductible"]:
            ded_total += t
            ded_by_cat[r["category"]] = ded_by_cat.get(r["category"], 0) + t
        else:
            nonded_total += t
        cd = r["cert_due"]
        if cd and r["is_deductible"] and r["status"] != "已报销":
            try:
                cd_d = _dt.datetime.strptime(cd, "%Y-%m-%d").date()
                if (cd_d - today).days <= 90:
                    cert_expiring.append(dict(r))
            except ValueError:
                pass
    return {
        "total_amount": round(total_amt, 2),
        "total_tax": round(total_tax, 2),
        "deductible_total": round(ded_total, 2),
        "nondeductible_total": round(nonded_total, 2),
        "deductible_by_category": {k: round(v, 2) for k, v in ded_by_cat.items()},
        "cert_expiring": sorted(cert_expiring, key=lambda x: x["cert_due"] or ""),
    }


def distinct_field(field):
    init_db()
    conn = _conn()
    rows = conn.execute(
        f"SELECT {field}, COUNT(*) c FROM invoices WHERE status != '已删除' "
        f"AND {field} IS NOT NULL AND {field} != '' GROUP BY {field} ORDER BY c DESC"
    ).fetchall()
    conn.close()
    return [{"name": r[0], "count": r[1]} for r in rows]


def distinct_projects():
    return distinct_field("project")


def distinct_departments():
    return distinct_field("department")


def distinct_cost_centers():
    return distinct_field("cost_center")


def cross_summary(dim="project", months=None):
    """类别 × 维度（project/department/cost_center/category）交叉汇总。"""
    if dim not in ("project", "department", "cost_center", "category"):
        dim = "project"
    init_db()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {dim}, category, COALESCE(SUM(amount),0) a, "
        f"COALESCE(SUM(tax_amount),0) t, COUNT(*) c "
        f"FROM invoices WHERE status != '已删除' GROUP BY {dim}, category"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        d = r[dim] or "(未归类)"
        node = out.setdefault(d, {"amount": 0.0, "tax": 0.0, "count": 0, "by_category": {}})
        node["amount"] += r["a"]
        node["tax"] += r["t"]
        node["count"] += r["c"]
        node["by_category"][r["category"]] = node["by_category"].get(r["category"], 0) + r["a"]
    for v in out.values():
        v["amount"] = round(v["amount"], 2)
        v["tax"] = round(v["tax"], 2)
        v["by_category"] = {k: round(x, 2) for k, x in v["by_category"].items()}
    return out


def update_fields(invoice_id, fields):
    """更新项目/部门/税率/可抵扣等字段（写账本 update 事件，跨机同步）。"""
    init_db()
    no = get_no_by_id(invoice_id)
    if not no:
        return {"ok": False, "msg": f"未找到 id={invoice_id}"}
    allowed = {"tax_rate", "ex_tax_amount", "is_deductible", "project", "department",
               "cost_center", "account_subject", "category", "note", "seller_name"}
    sets = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "is_deductible":
            sets[k] = int(bool(v))
        elif k == "tax_rate":
            sets[k] = _norm_amount(v)
        else:
            sets[k] = (str(v).strip() or None) if v not in (None, "") else None
    # 改税率时重算税额/不含税/可抵扣/认证到期
    if "tax_rate" in sets:
        conn = _conn()
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT amount, date, category FROM invoices WHERE id=?",
                           (invoice_id,)).fetchone()
        conn.close()
        amt = cur["amount"] or 0
        rate = sets["tax_rate"] or guess_tax_rate(cur["category"])
        ex_tax = round(amt / (1 + rate), 2) if amt else 0.0
        tax = round(amt - ex_tax, 2) if amt else 0.0
        ded = bool(sets.get("is_deductible", default_deductible(cur["category"])))
        cd = None
        if ded and cur["date"]:
            try:
                d = _dt.datetime.strptime(cur["date"], "%Y-%m-%d")
                cd = (d + _dt.timedelta(days=360)).strftime("%Y-%m-%d")
            except ValueError:
                cd = None
        sets["ex_tax_amount"] = ex_tax
        sets["tax_amount"] = tax
        sets["is_deductible"] = int(ded)
        sets["deductible_tax"] = tax if ded else 0.0
        sets["cert_due"] = cd
    if not sets:
        return {"ok": True, "msg": "无变化"}
    conn = _conn()
    c = conn.cursor()
    sql = "UPDATE invoices SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?"
    c.execute(sql, list(sets.values()) + [invoice_id])
    conn.commit()
    conn.close()
    rec = {"invoice_no": no}
    rec.update(sets)
    _append_ledger(rec, op="update")
    _log(f"字段更新 id={invoice_id}: {list(sets.keys())}")
    return {"ok": True, "msg": "已更新字段"}


def get_budget():
    try:
        return json.load(open(BUDGET_PATH, encoding="utf-8"))
    except Exception:
        return {}


def set_budget(b):
    try:
        json.dump(b, open(BUDGET_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def budget_usage(ym=None):
    if ym is None:
        ym = _dt.date.today().strftime("%Y-%m")
    b = get_budget()
    init_db()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT category, COALESCE(SUM(amount),0) a FROM invoices "
        "WHERE status != '已删除' AND substr(date,1,7)=? GROUP BY category", (ym,)
    ).fetchall()
    conn.close()
    out = []
    for cat, limit in b.items():
        used = sum(r["a"] for r in rows if r["category"] == cat)
        out.append({
            "category": cat, "limit": limit, "used": round(used, 2),
            "remain": round(limit - used, 2), "over": used > limit,
        })
    return {"ym": ym, "rows": out}


REIMBURSE_BLOCK = """<div class="reimb">
  <h1>费用报销单</h1>
  <div class="sub">报销日期：__DATE__　|　__GROUP__　|　共 __COUNT__ 张发票</div>
  <div class="meta"><span>申请人：__________</span><span>部门：__DEPT__</span><span>项目：__PROJ__</span></div>
  <table>
    <thead><tr><th>发票号</th><th>日期</th><th>销方</th><th>类别</th><th>金额</th><th>税额</th><th>不含税</th><th>项目</th><th>进项税</th></tr></thead>
    <tbody>__ROWS__</tbody>
    <tfoot><tr><td colspan="4">合计</td><td style="text-align:right">__TOTAL__</td><td style="text-align:right">__TOTAL_TAX__</td><td></td><td></td><td>__DED_TAX__</td></tr></tfoot>
  </table>
  <div class="sign">
    <div class="line">申请人签字：</div>
    <div class="line">部门审核：</div>
    <div class="line">财务复核：</div>
  </div>
</div>"""

REIMBURSE_DOC = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>费用报销单</title>
<style>
  @page{size:A4;margin:14mm;}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:#1f2933;font-size:13px}
  h1{text-align:center;font-size:20px;margin-bottom:4px}
  .sub{text-align:center;color:#667085;font-size:12px;margin-bottom:18px}
  .meta{display:flex;justify-content:space-between;margin-bottom:12px;font-size:13px}
  table{width:100%;border-collapse:collapse;border:1px solid #444}
  th,td{border:1px solid #888;padding:6px 8px;font-size:12px}
  th{background:#eef2ff;font-weight:700}
  tfoot td{font-weight:700;background:#f6f6f6}
  .sign{margin-top:28px;display:flex;justify-content:space-between;font-size:13px}
  .sign div{width:30%}
  .line{border-top:1px solid #333;margin-top:26px;padding-top:4px}
  .reimb{page-break-after:always}
  .reimb:last-child{page-break-after:auto}
  @media print{body{-webkit-print-color-adjust:exact}}
</style></head>
<body>
__BLOCKS__
<script>window.onload=function(){setTimeout(function(){window.print();},350);};</script>
</body></html>"""


def _reimburse_block(rows, group_label="", dept="", proj=""):
    """生成单张报销单 block（不含 html/body 包裹）。"""
    if not rows:
        return ""
    total = sum(r.get("amount") or 0 for r in rows)
    total_tax = sum(r.get("tax_amount") or 0 for r in rows)
    ded_tax = sum((r.get("deductible_tax") or 0) for r in rows)
    today = _dt.date.today().strftime("%Y-%m-%d")
    trs = ""
    for r in rows:
        trs += ("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td style='text-align:right'>%s</td><td style='text-align:right'>%s</td>"
                "<td style='text-align:right'>%s</td><td>%s</td><td>%s</td></tr>") % (
            r.get("invoice_no") or "", r.get("date") or "", r.get("seller_name") or "",
            r.get("category") or "", f"{(r.get('amount') or 0):,.2f}",
            f"{(r.get('tax_amount') or 0):,.2f}", f"{(r.get('ex_tax_amount') or 0):,.2f}",
            r.get("project") or "", "可抵扣" if r.get("is_deductible") else "不可抵扣")
    return (REIMBURSE_BLOCK.replace("__DATE__", today).replace("__GROUP__", group_label)
            .replace("__DEPT__", dept or "__________").replace("__PROJ__", proj or "__________")
            .replace("__COUNT__", str(len(rows))).replace("__ROWS__", trs)
            .replace("__TOTAL__", f"{total:,.2f}")
            .replace("__TOTAL_TAX__", f"{total_tax:,.2f}").replace("__DED_TAX__", f"{ded_tax:,.2f}"))


def export_reimburse(ids=None, group_by="none"):
    """标准报销单：勾选多张发票聚合，含合计/可抵扣税额/签字栏，A4 打印。
    group_by: 'none'(不分组) / 'project'(按项目分别出单) / 'department'(按部门分别出单)。"""
    rows = get_all() if ids is None else get_by_ids(ids)
    rows = [r for r in rows if r.get("status") != "已删除"]
    if not rows:
        return None
    if group_by in ("project", "department"):
        groups = {}
        for r in rows:
            key = (r.get(group_by) or "").strip() or "(未归类)"
            groups.setdefault(key, []).append(r)
        blocks = []
        for key in sorted(groups.keys()):
            g = groups[key]
            if group_by == "project":
                blocks.append(_reimburse_block(g, group_label="项目：" + key, proj=key))
            else:
                blocks.append(_reimburse_block(g, group_label="部门：" + key, dept=key))
        return REIMBURSE_DOC.replace("__BLOCKS__", "\n".join(blocks))
    return REIMBURSE_DOC.replace("__BLOCKS__", _reimburse_block(rows))


def export_ledger(ids=None, out_path=None):
    """财务台账：按科目/项目/部门导出，对接记账。"""
    if out_path is None:
        out_path = os.path.join(BASE_DIR, "财务台账.xlsx")
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return {"ok": False, "msg": "未安装 openpyxl，先跑: pip install openpyxl"}
    rows = get_all() if ids is None else get_by_ids(ids)
    rows = [r for r in rows if r.get("status") != "已删除"]
    if not rows:
        return {"ok": False, "msg": "没有可导出的发票"}
    wb = Workbook()
    ws = wb.active
    ws.title = "财务台账"
    headers = ["发票号码", "开票日期", "销方", "销方税号", "类别", "项目", "部门", "成本中心",
               "金额(含税)", "税率", "税额", "不含税金额", "是否可抵扣", "可抵扣税额",
               "认证到期", "记账科目", "状态", "备注"]
    ws.append(headers)
    head_fill = PatternFill("solid", fgColor="FF4D4F")
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append([
            r.get("invoice_no"), r.get("date"), r.get("seller_name"), r.get("seller_tax_no"),
            r.get("category"), r.get("project"), r.get("department"), r.get("cost_center"),
            r.get("amount"), (r.get("tax_rate") if r.get("tax_rate") is not None else ""),
            r.get("tax_amount"), r.get("ex_tax_amount"),
            "是" if r.get("is_deductible") else "否", r.get("deductible_tax"),
            r.get("cert_due"), r.get("account_subject"), r.get("status"), r.get("note"),
        ])
    for row in ws.iter_rows(min_row=2, min_col=9, max_col=14):
        for cell in row:
            if cell.value is not None and isinstance(cell.value, (int, float)):
                cell.number_format = "0.00"
    widths = [18, 12, 22, 20, 10, 12, 10, 12, 12, 8, 10, 12, 10, 12, 12, 14, 10, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = w
    wb.save(out_path)
    return {"ok": True, "path": out_path, "count": len(rows)}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    init_db()
    if cmd == "init":
        print("本机缓存:", CACHE_DB)
        print("共享账本:", LEDGER_PATH)
    elif cmd == "add":
        raw = None
        for i, a in enumerate(sys.argv):
            if a == "--json":
                raw = sys.argv[i + 1]
        if not raw:
            print("用法: add --json '{...}'")
            return
        print(json.dumps(add_invoice(json.loads(raw)), ensure_ascii=False))
    elif cmd == "list":
        status = None
        category = None
        kw = None
        for i, a in enumerate(sys.argv):
            if a == "--status":
                status = sys.argv[i + 1]
            elif a == "--category":
                category = sys.argv[i + 1]
            elif a == "--kw":
                kw = sys.argv[i + 1]
        _print_cli(query(status=status, category=category, keyword=kw))
    elif cmd == "stats":
        print(json.dumps(get_stats(), ensure_ascii=False, indent=2))
    elif cmd == "export-json":
        out = None
        for i, a in enumerate(sys.argv):
            if a == "--out":
                out = sys.argv[i + 1]
        print("导出:", export_json(out))
    elif cmd == "export-excel":
        out = None
        ids = None
        for i, a in enumerate(sys.argv):
            if a == "--out":
                out = sys.argv[i + 1]
            elif a == "--ids":
                ids = [int(x) for x in sys.argv[i + 1].split(",") if x.strip()]
        print(json.dumps(export_excel(out, ids), ensure_ascii=False))
    elif cmd == "duplicates":
        print("重复发票:", json.dumps(find_duplicates(), ensure_ascii=False, indent=2))
    elif cmd == "expiring":
        days = 30
        for i, a in enumerate(sys.argv):
            if a == "--days":
                days = int(sys.argv[i + 1])
        print(json.dumps(get_expiring(days), ensure_ascii=False, indent=2))
    elif cmd == "print-a4":
        out = None
        ids = None
        for i, a in enumerate(sys.argv):
            if a == "--out":
                out = sys.argv[i + 1]
            elif a == "--ids":
                ids = [int(x) for x in sys.argv[i + 1].split(",") if x.strip()]
        html = export_a4_html(ids)
        if not html:
            print("没有可打印的发票（可能勾选的 id 不存在）")
            return
        if out is None:
            out = os.path.join(BASE_DIR, "发票打印_A4两联.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print("已生成 A4 两联打印页:", out, "（用浏览器打开后 Ctrl/Cmd+P 打印）")
    elif cmd == "pending":
        ps = list_pending()
        print(f"待处理投递: {len(ps)} 条")
        for p in ps:
            print(f"  - {p.get('uid')} | {p.get('orig_name')} | 来自机器 {p.get('machine')} | {p.get('ts')}")
    elif cmd == "stage":
        src = None
        for i, a in enumerate(sys.argv):
            if a == "--src":
                src = sys.argv[i + 1]
        if not src:
            print("用法: stage --src 文件路径")
            return
        print(json.dumps(stage_file(src), ensure_ascii=False))
    elif cmd == "rebuild":
        print(json.dumps(rebuild(), ensure_ascii=False))
    elif cmd == "monthly":
        print(json.dumps(get_monthly_summary(), ensure_ascii=False, indent=2))
    elif cmd == "categories":
        print(json.dumps(distinct_categories(), ensure_ascii=False, indent=2))
    elif cmd == "set-status":
        iid = None
        st = None
        for i, a in enumerate(sys.argv):
            if a == "--id":
                iid = int(sys.argv[i + 1])
            elif a == "--status":
                st = sys.argv[i + 1]
        if iid is None or st is None:
            print("用法: set-status --id 1 --status 已报销")
        else:
            print(json.dumps(update_status(iid, st), ensure_ascii=False))
    elif cmd == "delete":
        iid = None
        for i, a in enumerate(sys.argv):
            if a == "--id":
                iid = int(sys.argv[i + 1])
        print(json.dumps(soft_delete(iid) if iid is not None else {"ok": False, "msg": "缺 --id"},
                         ensure_ascii=False))
    elif cmd == "normalize":
        old = new = None
        for i, a in enumerate(sys.argv):
            if a == "--old":
                old = sys.argv[i + 1]
            elif a == "--new":
                new = sys.argv[i + 1]
        if not old or not new:
            print("用法: normalize --old 餐费 --new 餐饮")
        else:
            print(json.dumps(rename_category(old, new), ensure_ascii=False))
    elif cmd == "tax-summary":
        print(json.dumps(get_tax_summary(), ensure_ascii=False, indent=2))
    elif cmd == "projects":
        print(json.dumps(distinct_projects(), ensure_ascii=False, indent=2))
    elif cmd == "departments":
        print(json.dumps(distinct_departments(), ensure_ascii=False, indent=2))
    elif cmd == "cross":
        dim = None
        for i, a in enumerate(sys.argv):
            if a == "--dim":
                dim = sys.argv[i + 1]
        print(json.dumps(cross_summary(dim or "project"), ensure_ascii=False, indent=2))
    elif cmd == "set-fields":
        iid = None
        raw = None
        for i, a in enumerate(sys.argv):
            if a == "--id":
                iid = int(sys.argv[i + 1])
            elif a == "--json":
                raw = sys.argv[i + 1]
        if iid is None or raw is None:
            print("用法: set-fields --id 1 --json '{\"project\":\"X\"}'")
        else:
            print(json.dumps(update_fields(iid, json.loads(raw)), ensure_ascii=False))
    elif cmd == "budget":
        raw = None
        for i, a in enumerate(sys.argv):
            if a == "--json":
                raw = sys.argv[i + 1]
        if raw:
            print(json.dumps(set_budget(json.loads(raw)), ensure_ascii=False))
        else:
            print(json.dumps(get_budget(), ensure_ascii=False, indent=2))
            print(json.dumps(budget_usage(), ensure_ascii=False, indent=2))
    elif cmd == "reimburse":
        ids = None
        out = None
        group = "none"
        for i, a in enumerate(sys.argv):
            if a == "--ids":
                ids = [int(x) for x in sys.argv[i + 1].split(",") if x.strip()]
            elif a == "--out":
                out = sys.argv[i + 1]
            elif a == "--group-by":
                group = sys.argv[i + 1]
        html = export_reimburse(ids, group_by=group)
        if not html:
            print("没有可生成报销单的发票")
            return
        if out is None:
            out = os.path.join(BASE_DIR, "报销单.html")
        open(out, "w", encoding="utf-8").write(html)
        print("已生成报销单:", out, "(group_by=" + group + ")")
    elif cmd == "tax-rules":
        print(json.dumps(get_tax_rules(), ensure_ascii=False, indent=2))
    elif cmd == "save-tax-rules":
        raw = None
        for i, a in enumerate(sys.argv):
            if a == "--json":
                raw = sys.argv[i + 1]
        if not raw:
            print("用法: save-tax-rules --json '{...}'")
            return
        print(json.dumps(save_tax_rules(json.loads(raw)), ensure_ascii=False))
    elif cmd == "recalculate-tax":
        print(json.dumps(recalculate_tax(), ensure_ascii=False))
    elif cmd == "ledger":
        ids = None
        out = None
        for i, a in enumerate(sys.argv):
            if a == "--ids":
                ids = [int(x) for x in sys.argv[i + 1].split(",") if x.strip()]
            elif a == "--out":
                out = sys.argv[i + 1]
        print(json.dumps(export_ledger(ids, out), ensure_ascii=False))
    else:
        print("未知命令:", cmd)


if __name__ == "__main__":
    main()

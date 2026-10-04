# -*- coding: utf-8 -*-
"""
发票整理器 · 投递监视器（每台电脑各跑一个）
==========================================
监视本机「投递文件夹」（默认 ~/发票投递，可用 INVOICE_DROP_DIR 环境变量覆盖），
发现新发票图/PDF -> 暂存到共享 OneDrive 的 _inbox -> 写入 pending 队列。
真正的字段提取由 WorkBuddy 技能「整理投递」完成（需要 AI 视觉，不调付费 OCR）。

特点：
  · 纯标准库，零额外依赖，三台电脑（同 OneDrive 账号）通用
  · 投递文件夹是本机本地的，避免三台机器重复处理同一份云盘文件
  · 处理完原文件移入 _done 子目录，不会重复投递
  · 共享 store 暂不可用时自动跳过、下次轮询重试

用法：
  python watch_drop.py                 # 默认投递文件夹
  python watch_drop.py --drop D:/发票投递
  python watch_drop.py --once         # 只扫一次就退出（调试用）
"""
import os
import sys
import time
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import invoice_lib as lib

SUPPORTED = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".pdf", ".tif", ".tiff")


def default_drop():
    """投递文件夹：优先 INVOICE_DROP_DIR 环境变量；默认 ~/发票投递（跨平台，不写死用户名）。"""
    return os.environ.get("INVOICE_DROP_DIR") or os.path.join(os.path.expanduser("~"), "发票投递")


def _log(msg):
    line = f"[{lib._now()}] {msg}"
    print(line, flush=True)


def process_once(drop_dir):
    done_dir = os.path.join(drop_dir, "_done")
    os.makedirs(done_dir, exist_ok=True)
    try:
        names = os.listdir(drop_dir)
    except Exception as e:
        _log(f"读取投递文件夹失败（稍后重试）: {e}")
        return 0
    staged = 0
    for name in names:
        if name.startswith("_"):
            continue
        low = name.lower()
        if not low.endswith(SUPPORTED):
            continue
        p = os.path.join(drop_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except Exception:
            continue
        if size == 0:
            continue
        # 用 .stagehint 标记「已稳定可处理」，避免读到半截文件
        hint = p + ".stagehint"
        if os.path.exists(hint):
            # 上一轮已稳定 -> 投递
            try:
                res = lib.stage_file(p)
                if res.get("ok"):
                    dest = os.path.join(done_dir, name)
                    shutil.move(p, dest)
                    staged += 1
                    _log(f"已投递: {name} -> {res.get('msg')}")
                else:
                    _log(f"投递失败: {name} -> {res.get('msg')}")
            except Exception as e:
                _log(f"投递异常: {name} -> {e}")
            finally:
                try:
                    os.remove(hint)
                except Exception:
                    pass
        else:
            # 第一次见到 -> 写稳定标记，下轮再处理
            try:
                open(hint, "w").close()
            except Exception:
                pass
    return staged


def main():
    drop = default_drop()
    once = False
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--drop":
            drop = sys.argv[i + 1]; i += 2
        elif a == "--once":
            once = True; i += 1
        else:
            i += 1
    os.makedirs(drop, exist_ok=True)
    _log(f"投递监视器启动 | 机器={lib.machine_name()} | 投递文件夹={drop}")
    _log(f"共享存储={lib.BASE_DIR} | 队列={lib.PENDING_PATH}")
    if once:
        n = process_once(drop)
        _log(f"单次扫描完成，投递 {n} 个文件。")
        return
    _log("进入常驻循环（Ctrl+C 退出）…")
    while True:
        try:
            process_once(drop)
        except KeyboardInterrupt:
            _log("收到中断，退出。")
            break
        except Exception as e:
            _log(f"循环异常（继续）: {e}")
        time.sleep(3)


if __name__ == "__main__":
    main()

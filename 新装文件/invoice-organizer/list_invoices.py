# -*- coding: utf-8 -*-
"""
发票整理器 · 文件夹列举助手
================================
列出某文件夹下的发票图片/PDF 文件清单（路径 + 大小），辅助「批量导入文件夹」。

用法:
  python list_invoices.py <文件夹> [--recursive]
示例:
  python list_invoices.py "D:/发票/九月"
  python list_invoices.py "D:/发票" --recursive
"""
import os
import sys
import json

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".pdf", ".heic"}


def list_files(folder, recursive=False):
    out = []
    if recursive:
        for root, _, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in EXTS:
                    p = os.path.join(root, f)
                    out.append({"path": p, "size": os.path.getsize(p)})
    else:
        for f in sorted(os.listdir(folder)):
            fp = os.path.join(folder, f)
            if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in EXTS:
                out.append({"path": fp, "size": os.path.getsize(fp)})
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python list_invoices.py <文件夹> [--recursive]")
        sys.exit(1)
    folder = sys.argv[1]
    rec = "--recursive" in sys.argv or "-r" in sys.argv
    files = list_files(folder, rec)
    print(json.dumps({"folder": folder, "count": len(files), "files": files},
                     ensure_ascii=False, indent=2))

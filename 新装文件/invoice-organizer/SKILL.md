---
name: invoice-organizer
display_name: 发票整理报销台
display_name_en: Invoice Organizer & Reimbursement Dashboard
description: 个人发票整理与报销管理工具——对话里发发票图片/PDF，AI 多模态提取字段自动入库（带查重），配套本地网页报销台：看板/税额拆分/进项税抵扣/多维核算/预算预警/标准报销单/财务台账/A4 打印/Excel 导出。数据全部存本地（可选 OneDrive 多机共享），全程免费，不调用任何付费 OCR/生成 API。
description_zh: 个人发票整理与报销管理工具——对话里发发票图片/PDF，AI 多模态提取字段自动入库（带查重），配套本地网页报销台：看板/税额拆分/进项税抵扣/多维核算/预算预警/标准报销单/财务台账/A4 打印/Excel 导出。数据全部存本地（可选 OneDrive 多机共享），全程免费，不调用任何付费 OCR/生成 API。
description_en: |-
  Personal invoice organizer and reimbursement manager. Send an invoice image or PDF in chat and the AI extracts fields with its own multimodal vision (no paid OCR) and files it with duplicate detection. Ships with a local web dashboard: KPI cards, tax splitting, input-VAT deduction, project/department accounting, budget alerts, printable reimbursement forms, finance ledger export and A4 printing. All data stays local (optional OneDrive sync across computers). Fully free, no paid APIs.
category: 08-FinanceInvestment
version: 1.0.0
author: 彬总
agent_created: true
read_when:
  - 用户发来发票图片/PDF（任意语境，无需特定关键词）——自动识别并入库（自动收集）
  - 用户说「整理发票」「入账」「报销」「归档发票」「收一下发票」「整理投递」
  - 用户要查发票、查重、看报销清单、导出 Excel、打印、看 A4 排版
  - 用户提到「发票整理器」「报销台」「批量导入」「批量导出」「投递文件夹」
  - 用户问「微信/手机/另一台电脑的发票怎么收」
---

# 发票整理报销台（Invoice Organizer）

个人发票整理与报销管理工具。对话里发发票图/PDF → AI 视觉提取字段 → 写入追加式账本；配套本地网页「报销台」做看板、导出、打印与财务核算。数据全部本地（可选 OneDrive 实现多台电脑共享同一份账本）。

## 首次安装（第三方用户必读）
本技能自带完整工具（与本 SKILL.md 同一目录）：
- 核心库 `invoice_lib.py`（单一真相源）、网页后端 `server.py`、投递监视器 `watch_drop.py`、MCP server `mcp_server.py`、前端 `index.html`、跨平台启动脚本与 `README.md`。
- 依赖：Python 3.10+，`pip install flask openpyxl pillow pymupdf`（Windows 可双击 `install.bat` 一键装依赖+建投递夹+注册自启；macOS/Linux 用 `install.sh`）。
- 数据目录：默认在工具目录下的 `store/`；可用环境变量 `INVOICE_HOME`（工具/数据根目录）、`INVOICE_PORT`（网页端口，默认 8731）、`INVOICE_DROP_DIR`（本机投递夹，默认 `~/发票投递`）覆盖。
- 首次使用：运行 `python server.py` 后访问 `http://127.0.0.1:8731`；要桌面拖拽收集再跑 `python watch_drop.py`。
- 可选 MCP：把 `mcp_server.py` 按平台 MCP 规范注册（stdio），即可让 AI 直接调用 11 个发票工具函数。

## 架构（关键，决定多机互通）
- **共享持久层（可选 OneDrive 同步）**：`$INVOICE_HOME`（默认脚本所在目录）
  - `store/ledger.jsonl` —— 追加式账本，每行一条事件（源真相，云盘同步安全）
  - `store/invoices/` —— 发票原图/PDF（不可变）
  - `store/_inbox/` —— 监视器投递的待处理原文件
  - `store/pending.jsonl` —— 待 AI 提取的队列
  - `tax_rules.json` —— 税率规则配置（可编辑，见下）
- **本机缓存（不进云盘）**：`~/.invoice_organizer/cache.db` —— SQLite 快速索引，每次启动从账本合并。
- **为什么这样**：SQLite 直接放 OneDrive 同步会被中途改写损坏；改用语义安全的「追加写账本 + 本机缓存」——写入同时落账本(可恢复)与缓存(快)，任何机器开机先从账本合并，多端自然一致。单人顺次使用几乎不冲突；写读均有并发文件锁保护。

> `invoice_lib.py` 是单一真相源（SSOT），网页/对话/监视器都只通过它读写。路径用 `INVOICE_HOME` 环境变量可覆盖。

## 关键路径（相对/可配置）
- 工具目录：`$INVOICE_HOME`（默认脚本 `invoice_lib.py` 所在目录；可用 `INVOICE_HOME` 环境变量覆盖）
- 数据层（SSOT）：`$INVOICE_HOME/invoice_lib.py`
- 网页后端：`$INVOICE_HOME/server.py`（端口 `8731`，可用 `INVOICE_PORT` 覆盖）
- 投递监视器：`$INVOICE_HOME/watch_drop.py`
- 本机投递文件夹（每台电脑一个，本地不云同步）：`$INVOICE_DROP_DIR`（默认 `~/发票投递`）
- 网页：`$INVOICE_HOME/index.html`
- Python：任意 Python 3.10+

## 三条汇入渠道（多机都可用）
1. **桌面拖拽**：把发票拖进本机投递夹（默认 `~/发票投递`）→ `watch_drop.py` 自动暂存到共享 `_inbox` 并入 pending 队列。无需开 WorkBuddy。
2. **对话直发**：在 WorkBuddy 发发票图（桌面端或移动端、同一账号）→ 自动收集入库（见下）。
3. **微信/邮箱转发**：转发到已连的「智能体邮箱」→ 我读附件入库。
无论哪条，字段提取都由**我（多模态视觉）**完成，不碰任何付费 OCR。

> **移动端（手机 App）为什么也通**：手机发图触发 AI，但「提取字段 + 写入账本」的代码在桌面机（AI 执行环境）上运行，账本路径天然存在。手机端没有「本机拖拽投递文件夹」概念，用对话发图替代即可。

## 工作流 A：对话直发一张发票（自动收集）
**只要用户发来一张像发票的图/PDF，就自动走录入，不需要关键词。** 明显不是发票则礼貌说明不收录。
1. 用你的多模态能力**直接看图**，提取字段（缺失留空字符串）：
   - `invoice_no` 发票号码（必填，查重主键）
   - `invoice_code` 发票代码（可选）
   - `date` 开票日期 `YYYY-MM-DD`
   - `amount` 价税合计金额（元，数字）
   - `tax_amount` 税额（元，可选）
   - `buyer_name` 购方/抬头（从票面提取；可询问用户常用抬头并记住，后续自动填写）
   - `buyer_tax_no` 购方税号
   - `seller_name` 销方名称
   - `seller_tax_no` 销方税号
   - `category` 消费类型：从 {餐饮, 交通, 住宿, 办公, 通讯, 停车费, 租金, 物业费, 其他} 选最贴近的
   - `expense_type` 报销类型（可选）
   - `note` 备注（可选）
   - `image_path` 若用户给了原图本地路径可填，否则留空（直发场景一般留空，靠对话图识别）
2. 组装 JSON，调用（路径以 `$INVOICE_HOME` 为准，以下为示例）：
   ```
   python "$INVOICE_HOME/invoice_lib.py" add --json '{"invoice_no":"...","date":"2026-09-01","amount":128.0,"category":"餐饮",...}'
   ```
3. 解析返回：`duplicate:true` → 告知已查重拦截（id=…）；`duplicate:false` → 告知已入库 id=…、金额、类型。
4. 末尾补：想看全部/导出，说「开报销台网页」或「导出发票 Excel」。

## 工作流 B：整理投递（处理拖拽/监视器入队的文件）
当用户说「整理投递」「处理投递」「看投递箱」，或你判断有大量待处理时：
1. 运行 `invoice_lib.py pending` 列出队列（也可直接读 `$INVOICE_HOME/store/pending.jsonl`）。
2. 对每条 `status:"pending"` 的条目：
   - 用 **Read 工具打开它的 `src` 路径**（在共享 `_inbox/` 里的 PDF/图片）做多模态提取；
   - 按工作流 A 的字段提取后，调用 `add --json ...` 写入（注意：此时 `image_path` 填该 `_inbox` 原文件路径，打印即可显示真票原图）；
   - 写入成功后调用标记完成：用 Python 调 `lib.mark_pending_done(uid, invoice_id)`（或脚本 `python -c "import sys;sys.path.insert(0,r'<INVOICE_HOME>');import invoice_lib as l;l.mark_pending_done('UID', ID)"`）。
3. 批处理时每 6 张向用户简短汇报进度；全部完成给汇总（成功几条/重复几条/金额合计）。
4. 若队列为空，告知「没有待处理的投递」。

## 批量录入（文件夹）
用户说「导入 <某文件夹>」→ Glob 列出 `**/*.{jpg,png,pdf,webp,jpeg}` → 每批 Read 6 张提取并 add → 分批汇报。

## 批量导出
- 网页端：勾选行 →「导出选中」下载子集 Excel；或「导出全部」。
- 命令行：`invoice_lib.py export-excel --ids 1,2,3` 仅导出指定；不带 `--ids` 导出全部。

## 其他指令
- **「开报销台网页」**：后台运行 `python "$INVOICE_HOME/server.py"`，然后告诉用户访问 `http://127.0.0.1:8731`（网页读的是本机缓存，已与共享账本合并；并后台每 5 秒自动合并其他电脑刚录入的发票，无需重启）。
- **「导出发票 Excel」**：`invoice_lib.py export-excel`，把生成的 `invoices.xlsx` 路径告知并打开。
- **「查重」**：`invoice_lib.py duplicates`。**「报销期限」**：`invoice_lib.py expiring --days 30`。
- **「发票统计」**：`invoice_lib.py stats`。**「A4 打印」**：网页点「打印排版(A4两联)」；或 `invoice_lib.py print-a4 --out x.html`。
- **「换电脑了/数据不对」**：`invoice_lib.py rebuild` 从共享账本重建本机缓存。

## 多机部署（两台/三台本地电脑都适用，可选 OneDrive）
架构本就为多机设计：账本与图片放 OneDrive 等同步盘共享，缓存每机本地。**两台电脑无需额外机制，照做即互通**：
1. **第二台电脑登录同一同步盘账号** → 工具目录自动出现（工具代码、账本、图片全在云盘，不用复制）。
2. **装 Python 依赖（每台一次）**：双击 `start_server.bat` / `start_watcher.bat`（Windows）或 `start.sh`（macOS/Linux），首次会自动装依赖。
3. **每台启动网页 + 拖拽监视**。本机投递文件夹（默认 `~/发票投递`，由 `INVOICE_DROP_DIR` 控制）是各自本地的，互不干扰。
4. **跨机实时同步**：每台网页后端有后台线程，约每 5 秒按账本文件变化自动合并另一台刚录入的发票——A 机刚存的票，B 机开着的网页几秒内就看到，不用重启。也可 `GET http://127.0.0.1:8731/api/sync` 立即手动触发。

⚠️ 同步盘「按需文件/联机占位」会把 `store/ledger.jsonl`、发票图变成仅联机占位符，导致读取/打印失败。在该文件夹设为「始终保留在此设备上」。
⚠️ 单人顺次使用几乎不冲突；极端情况（两台同时秒级写同一账本）同步盘可能生成 conflict 副本，概率极低，真发生就 `rebuild` 重建。

## 网页报销台（Dashboard）
- **看板**：KPI 卡片（总数/待报销额/已报销/可抵扣进项税/本月预算使用率）、类别环形图、进项税结构环图、近 6 月趋势柱图（均**手绘 SVG，无 CDN、离线可用**）、临期专票预警、搜索/类别/项目/部门/状态/排序、票样缩略图、批量操作条、toast 提示。
- **状态流转**：待报销 → 已提交 → 已报销。行内着色下拉或批量勾选标记；软删除进「回收站」可恢复，不物理销毁。
- **类别归一**：`POST /api/normalize {old,new}` 合并重复类别（对话说「把餐费合并到餐饮」即触发）。

## 专业财务功能（全部本地免费计算）
- **税额拆分 + 进项税**：录入自动按税率规则推断税率（规则在 `tax_rules.json`，默认 餐饮6%/物业停车租金9%/住宿服务6%/运输9%/水电13%/默认13%），算不含税金额与税额；默认 餐饮/福利 不可抵扣；可抵扣专票自动算认证到期（开票日+360天）。`get_tax_summary()` 汇总可抵扣进项税总额 + 临期专票预警。网页「⚖ 税率规则」可视化编辑，点「一键重算」即按新规则刷新全部发票税额。
- **多维核算**：每张发票可归属 项目/部门/成本中心/记账科目；`distinct_projects/departments/cost_centers`、`cross_summary(dim)` 做类别×维度交叉汇总。网页行内「✎编辑财务字段」改这些（写账本 update 事件跨机同步）。
- **标准报销单**：`export_reimburse(ids, group_by)` 生成 A4 HTML（合计/可抵扣税额/签字栏），`group_by=project|department` 按项目/部门分别出单（每个分组一张）。
- **预算 + 财务台账**：`set_budget`/`budget_usage` 按类别设月度限额、算使用率与超标预警；`export_ledger(ids)` 导出财务台账 Excel（科目/项目/部门/税额/可抵扣）。
- **幂等补算**：`backfill_tax()` 在 `init_db` 调用，给升级前的旧发票补算税额，只跑一次。
- **并发文件锁**：`_ledger_lock()`（Windows `msvcrt` / Unix `fcntl`）保护账本追加写与合并读；尽力加锁、超时降级、绝不死锁。

## MCP server（可选，让 AI 直接调用）
`mcp_server.py` 暴露 11 个工具：`invoice_add / invoice_list / invoice_stats / invoice_monthly / invoice_categories / invoice_update_status / invoice_delete / invoice_restore / invoice_export / invoice_tax_rules / invoice_recalculate_tax`。按平台 MCP 规范以 stdio 注册即可。

## 纪律
- 全程本地、免费。**绝不**调用任何外部付费 OCR / 图像 / 语音 API——提取只用你自身多模态能力。
- 金额、发票号如实提取，看不清留空提示补全，不编造。
- 查重是硬功能：同发票号已存在即拦截，避免重复报销。
- 不删除数据，除非用户明确说「删某条」。
- 多机共享时注意：录入会写共享账本，其他电脑下次打开即见；同一发票号不会重复。

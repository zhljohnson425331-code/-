# 发票整理器（Invoice Organizer）

本地优先的个人发票报销台：**在对话里发发票图 → 自动提取字段 → 写入账本**，配套网页查看、查重、税额拆分、进项税核算、预算预警、A4 打印、标准报销单与财务台账导出。

> 🔒 **隐私优先 · 全程免费**：所有数据留在本机 / 你的 OneDrive；字段提取只用 AI 多模态能力，**不调用任何付费 OCR / 图像 / 语音 API**。

---

## ✨ 功能

- **零操作录入**：对话里直接发发票图片/PDF，自动识别并入库（查重拦截）。
- **多端多机共享**：账本与图片存 OneDrive，多台电脑自动同步；网页后台每 5 秒自动合并另一台刚录入的发票。
- **专业财务**
  - 税额拆分（不含税金额 + 税额）、**可抵扣进项税**汇总、专票认证临期预警。
  - 税率规则**可配置**（`tax_rules.json`），一键按规则重算全部发票。
  - 多维核算：每张发票可归属 项目 / 部门 / 成本中心 / 记账科目。
  - 标准报销单（可按项目/部门分组出单）、财务台账 Excel 导出。
  - 类别月度预算 + 超标预警。
- **批量能力**：批量导入文件夹、批量导出 Excel、A4 两联排版打印。
- **可被 AI 编排**：内置 MCP server，其他 Agent / 工作流能直接查询、记账、出报表。

---

## 📦 安装

### 依赖
Python 3.10+，安装：
```bash
pip install flask openpyxl pillow pymupdf
# MCP server（可选，供其他 AI 调用）：
pip install "mcp>=2"
```

### 一键安装（Windows）
双击 `install.bat`：自动装依赖、建本机投递文件夹、注册开机登录自启（计划任务「发票报销台」）。
卸载自启用 `uninstall.bat`（保留数据）。

### 跨平台
- **Windows**：`start_server.bat`（开网页）+ `start_watcher.bat`（开拖拽投递监视）
- **macOS / Linux**：`bash install.sh` 安装；`bash start.sh` 启动；自启见脚本内 launchd/systemd 说明。

### 启动网页
```bash
python server.py
# 默认 http://127.0.0.1:8731
```

---

## ⚙️ 配置项（环境变量，均可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `INVOICE_HOME` | `~/OneDrive/发票整理器/tool` 或脚本所在目录 | 工具根目录（账本/图片/规则所在） |
| `INVOICE_PORT` | `8731` | 网页端口 |
| `INVOICE_DROP_DIR` | `~/发票投递` | 本机拖拽投递文件夹（各机本地，不云同步） |

> 换机器 / 不同 OneDrive 路径时，只需设置 `INVOICE_HOME` 指向你的工具目录即可，无需改代码。

---

## 🗂️ 目录结构

```
发票整理器/tool/
├── invoice_lib.py      # 数据层（SSOT）：读写账本/税额/进项税/报表
├── server.py           # 网页后端（只读 API + 静态页）
├── mcp_server.py       # MCP server（供其他 AI 调用）
├── watch_drop.py       # 本机投递文件夹监视器
├── index.html          # 报销台 Dashboard（自包含，无 CDN；本机版也可放 web/ 子目录，二者兼容）
├── tax_rules.json      # 税率规则（可编辑）
├── install.bat / start_server.bat / start_watcher.bat   # Windows
├── install.sh  / start.sh                              # macOS/Linux
├── requirements.txt
├── README.md
└── store/
    ├── ledger.jsonl        # 追加式账本（共享真相源）
    ├── invoices/           # 发票原图/PDF
    ├── _inbox/             # 待处理投递
    ├── pending.jsonl       # 待 AI 提取队列
    ├── budget.json         # 预算配置
    └── backups/            # 按日账本快照
```

---

## 📊 税率规则（可配置）

`tax_rules.json` 示例：
```json
{
  "rules": [
    {"keywords": ["餐饮","餐费","餐"], "tax_rate": 0.06, "is_deductible": false},
    {"keywords": ["物业","停车","租金"], "tax_rate": 0.09, "is_deductible": true}
  ],
  "default_tax_rate": 0.13,
  "default_is_deductible": true
}
```
- 录入发票时按「类别关键词」匹配税率与可抵扣性（仅作默认建议，单行仍可在网页「✎编辑」手动覆盖）。
- 在网页点「⚖ 税率规则」可视化编辑，保存后点「一键重算」即刷新全部发票税额。

> 税率为本地启发式规则，真实票面税率以发票为准；如有出入请在行内编辑修正。

---

## 🔌 API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/invoices` | 发票列表（支持 `status`/`category`/`kw` 筛选） |
| GET | `/api/stats` `/api/monthly` `/api/categories` | 统计 / 月度趋势 / 类别 |
| GET | `/api/tax_summary` | 进项税可抵扣汇总 + 临期专票 |
| GET | `/api/projects` `/api/departments` `/api/cross_summary?dim=` | 多维核算 |
| GET/POST | `/api/export_excel` `/api/print_a4` `/api/export_ledger` `/api/export_reimburse` | 导出（支持 `ids` 子集；报销单支持 `group_by=project\|department\|none`） |
| POST | `/api/invoices/<id>/status\|delete\|restore\|fields` | 状态流转 / 软删 / 恢复 / 改字段 |
| GET/POST | `/api/tax_rules` | 读 / 保存税率规则 |
| POST | `/api/recalculate_tax` | 按规则重算全部发票税额 |
| GET | `/api/sync` | 手动触发跨机合并 |

命令行：`python invoice_lib.py add|list|stats|export-excel|print-a4|reimburse|ledger|tax-rules|recalculate-tax|rebuild ...`

---

## 🤖 MCP server（可选）

让其他 AI / 工作流直接调用你的发票数据层：
```bash
python mcp_server.py
```
暴露工具：`invoice_add` `invoice_list` `invoice_stats` `invoice_monthly` `invoice_categories`
`invoice_update_status` `invoice_delete` `invoice_restore` `invoice_export`
`invoice_recalculate_tax` `invoice_tax_rules`

---

## 🛡️ 并发与数据安全

- 账本写入与本机合并读均经 `_ledger_lock()` 文件锁（Windows `msvcrt` / Unix `fcntl`）保护，防本机多进程交错写坏；超时降级放行，绝不死锁。
- 跨机同步基于 OneDrive 顺序写（最佳实践为单写端）；极端冲突可用 `python invoice_lib.py rebuild` 从账本重建本机缓存。
- 按日账本快照在 `store/backups/`，可恢复误改。

---

## 📤 数据汇入三渠道

1. **对话直发**：在 WorkBuddy 发发票图 → 自动提取入库。
2. **桌面拖拽**：把发票拖进本机投递文件夹 → 监视器自动入队，再「整理投递」批量提取。
3. **微信/邮箱转发**：转发到已连的「智能体邮箱」→ 读附件入库。

---

## ❓ 常见问题

- **云端收不到？** 移动端发图请选「本地电脑」模式，账本路径在本机 Windows，选「云端」写不进本地账本。
- **OneDrive「按需文件」导致读不到？** 在 `发票整理器` 文件夹右键 →「始终保留在此设备上」。
- **换电脑数据不对？** 跑 `python invoice_lib.py rebuild` 从共享账本重建。

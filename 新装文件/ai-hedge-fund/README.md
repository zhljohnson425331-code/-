# ai-hedge-fund（AI 对冲基金）安装说明

> 安装日期：2026-10-03 ｜ 版本：aihf v2.5.0
> 来源：virattt/ai-hedge-fund（GitHub 62k stars，MIT 许可）
> 注意：它是一个**独立的 Python 开源应用**，不是 WorkBuddy 那种纯 SKILL.md 技能

## 一、它是什么

用多个 AI Agent 模拟投资大佬（巴菲特、木头姐、索罗斯等）做决策，支持两种模式：

- **Paper Trading（模拟盘）**：用虚拟资金做交易模拟
- **Backtest（历史回测）**：用历史数据回测策略

⚠️ 官方明确声明：**不做任何真实交易**，仅教育/研究用途，不会产生真实下单。

## 二、安装位置（不污染系统）

装在受管 Python 环境里：

```
C:\Users\Administrator\.workbuddy\binaries\python\envs\default
```

依赖：numpy 2.5.3 / pandas 3.0.6 / scipy / matplotlib / langchain 全家桶 / tiktoken / rich / textual 等（全部为预编译轮子，无源码编译）。

## 三、启动方式

双击工作目录下的 **`启动AI对冲基金.bat`** 即可运行；或在命令行执行：

```
C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\aihf.exe
```

## 四、首次运行需要准备

1. **Financial Datasets 行情密钥**：去 financialdatasets.ai 注册获取（用于拉美股行情）
2. **大模型 API 密钥（任选其一）**：Anthropic / OpenAI / DeepSeek / Google / xAI / Kimi
3. 密钥保存在本地 `~/.hedge-fund/.env`
4. ⚠️ 调用大模型会产生 **API 费用**（不是软件收费，是模型调用费）

## 五、安装踩坑记录（供以后复用）

- 官方默认装法在本机必失败：aihf 把 numpy 锁死在 `<2.0`，但 Python 3.13 上老版 numpy 只能源码编译，本机**无 C/Rust 编译器** → 编译卡死。
- 解法：依赖放宽到有现成轮子的版本（numpy 2.x / pydantic>=2.9 / tiktoken>=0.8）+ `--only-binary=:all:` 强制只装轮子不编译 + `aihf --no-deps` 避开它的 numpy 死锁，分开两步装。
- 网络：VPN 的本地代理（127.0.0.1:56785）会让 pip 报 502 → pip 装包时加 `NO_PROXY='*' no_proxy='*'` 绕开直连；国内包走**清华镜像**最稳。
- 经验：直接装 Python 3.12 便携版这条路在当下网络下下载超时严重，不如在 3.13 上用"放宽版本 + 强制轮子"的修法。

## 六、安全提醒

- 用完把各平台的 API 密钥从 `~/.hedge-fund/.env` 管好，别外泄。
- 本工具只是"研究玩具"，与日常 VOO 定投无关，当学习项目玩即可。

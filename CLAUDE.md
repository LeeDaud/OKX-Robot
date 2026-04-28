# Auto Trader — 项目规则

## 项目概述

Base 链上的跟单交易机器人，监控指定钱包地址的链上交易，通过 OKX DEX API 自动跟单 Virtuals 平台代币。

## 技术栈

- Python 3.11+
- Web3.py（Base 链交互）
- WebSocket（实时监听链上交易）
- OKX DEX API（交易执行）
- SQLite（本地状态持久化）

## 目录结构

```
015-Auto-Trader/
├── CLAUDE.md
├── prd.md
├── plan.md
├── .env.example
├── .env                  # 不进 git
├── requirements.txt
├── src/
│   ├── monitor/          # 链上地址监控
│   ├── executor/         # 交易执行（OKX DEX）
│   ├── risk/             # 风控规则
│   ├── config/           # 配置加载
│   └── db/               # 本地状态存储
├── tests/
└── logs/
```

## 协作流程

```
代码修改 → 我给提交信息 → 你手动 git add / commit / push → 你回复 "ok" → 我更新服务器或进入下一轮修改
```

- 每轮代码改动完成后，我会给出 commit message（仅文本，不执行命令）
- 你自己完成 `git add` / `git commit` / `git push`
- 推送后回复 "ok"，我收到后执行服务器更新或继续编码
- 我不再主动提交 commit 或执行 git 操作

## 开发约定

- 私钥、API Key 只存 `.env`，不进代码，不进 commit
- 每次改动后运行 `python -m pytest tests/ -x` 验证
- 链上写操作（发交易）必须有 dry-run 模式，默认开启
- 日志统一用 `logging` 模块，输出到 `logs/` 目录

## 验证命令

```bash
python -m pytest tests/ -x
python src/main.py --dry-run
```

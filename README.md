# LazyFP - 懒发票

> Automated Chinese Invoice Management Tool / 自动化发票管理工具

[English](#english) · [中文](#中文)

---

## English

LazyFP extracts, deduplicates, organizes, and exports Chinese invoices (fapiao) with a modern web UI.

### Features

- **PDF Invoice Extraction** — Parses Chinese VAT invoices, electronic invoices, and China Mobile billing statements
- **Regex-Driven Rules** — Configurable extraction rules in `config/rules.json`, easily extensible for new invoice formats
- **Upload Queue with SSE** — Real-time progress streaming for batch uploads
- **Deduplication & Organization** — Auto-detect duplicates, organize by purchaser/quarter
- **ZIP Export** — Export organized invoices with Excel summary
- **Dark Mode & i18n** — Chinese/English toggle, light/dark theme
- **Non-Invoice Detection** — Flags unrecognized documents for manual review

### Quick Start

#### Local Development

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Run
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

#### Docker

```bash
docker build -t lazyfp .
docker run -d -p 8000:8000 -v $(pwd)/fp:/app/fp lazyfp
```

#### Docker Compose

```bash
docker compose up -d
```

### Project Structure

```
lazyfp/
├── config/
│   ├── rules.json              # Regex extraction rules
│   ├── custom_rules.json       # User-added rules (debug panel)
│   └── settings.yaml           # Global configuration
├── extractors/
│   ├── base.py                 # BaseExtractor abstract class
│   ├── invoice_no.py           # Invoice number extraction
│   ├── date_extractor.py       # Date extraction + quarter parsing
│   ├── amount_extractor.py     # Amount extraction
│   └── company_name.py         # Purchaser/Seller name extraction
├── core/
│   ├── cache.py                # Memory cache with JSON persistence
│   ├── rule_engine.py          # Rule loading + merging (custom > builtin)
│   ├── orchestrator.py         # Extraction coordinator + fallback chain
│   └── queue_manager.py        # Upload queue with SSE streaming
├── tests/
│   └── test_extractors.py      # Unit tests for extractors
├── app.py                      # FastAPI application
├── main.py                     # CLI entry point (compatibility wrapper)
└── static/index.html           # Alpine.js + Tailwind frontend
```

### Configuration

#### Adding New Invoice Formats

Edit `config/rules.json` to add regex patterns for new invoice types:

```json
{
  "invoice_no": {
    "primary": [
      {"pattern": "your_pattern_here", "flags": 0, "desc": "Description"}
    ],
    "fallback": [...]
  }
}
```

Rules are evaluated in order: primary → fallback → extractor-specific fallback. No code changes needed.

#### Settings

Edit `config/settings.yaml` for paths, upload limits, and queue settings.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Web UI |
| GET | `/api/invoices` | List all invoices (cached) |
| POST | `/api/upload/queued` | Upload PDFs to queue |
| GET | `/api/upload/sse` | SSE stream for upload progress |
| POST | `/api/deduplicate` | Deduplicate and move to dump/ |
| POST | `/api/organize` | Organize by purchaser/quarter |
| GET | `/api/export/{purchaser}/{quarter}` | Download ZIP + Excel |
| GET | `/api/unrecognized` | List non-invoice documents |
| DELETE | `/api/invoices/{filename}` | Delete an invoice |

---

## 中文

LazyFP 是一个自动化发票管理工具，支持发票解析、去重、整理和导出，配备现代化 Web 界面。

### 功能特性

- **PDF 发票解析** — 支持增值税发票、电子发票、中国移动对账单等多种格式
- **正则规则驱动** — 提取规则配置在 `config/rules.json` 中，新增发票格式无需修改代码
- **上传队列 + SSE** — 批量上传实时进度推送
- **去重与整理** — 自动检测重复发票，按购买方/季度分类整理
- **ZIP 导出** — 导出整理后的发票及 Excel 汇总表
- **深色模式 & 多语言** — 中英文切换，明暗主题切换
- **非发票识别** — 自动标记非发票类文档供人工审核

### 快速开始

#### 本地开发

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 启动服务
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

#### Docker

```bash
docker build -t lazyfp .
docker run -d -p 8000:8000 -v $(pwd)/fp:/app/fp lazyfp
```

#### Docker Compose

```bash
docker compose up -d
```

### 项目结构

```
lazyfp/
├── config/
│   ├── rules.json              # 正则提取规则
│   ├── custom_rules.json       # 用户自定义规则（调试面板添加）
│   └── settings.yaml           # 全局配置
├── extractors/
│   ├── base.py                 # 提取器基类
│   ├── invoice_no.py           # 发票号码提取
│   ├── date_extractor.py       # 日期提取 + 季度解析
│   ├── amount_extractor.py     # 金额提取
│   └── company_name.py         # 购买方/销售方名称提取
├── core/
│   ├── cache.py                # 内存缓存 + JSON 持久化
│   ├── rule_engine.py          # 规则加载 + 合并（自定义 > 内置）
│   ├── orchestrator.py         # 提取编排器 + 回退链
│   └── queue_manager.py        # 上传队列 + SSE 流式推送
├── tests/
│   └── test_extractors.py      # 提取器单元测试
├── app.py                      # FastAPI 应用
├── main.py                     # CLI 入口（兼容层）
└── static/index.html           # Alpine.js + Tailwind 前端
```

### 配置说明

#### 添加新发票格式

编辑 `config/rules.json` 添加正则规则即可支持新发票格式：

```json
{
  "invoice_no": {
    "primary": [
      {"pattern": "你的正则表达式", "flags": 0, "desc": "描述"}
    ],
    "fallback": [...]
  }
}
```

规则按顺序匹配：primary → fallback → 提取器回退方法。无需修改代码。

#### 全局设置

编辑 `config/settings.yaml` 配置路径、上传限制、队列参数等。

### API 接口

| 方法 | 接口 | 说明 |
|------|------|------|
| GET | `/` | Web 界面 |
| GET | `/api/invoices` | 获取发票列表（缓存） |
| POST | `/api/upload/queued` | 上传 PDF 到队列 |
| GET | `/api/upload/sse` | SSE 实时上传进度 |
| POST | `/api/deduplicate` | 去重并移至 dump/ |
| POST | `/api/organize` | 按购买方/季度整理 |
| GET | `/api/export/{purchaser}/{quarter}` | 下载 ZIP + Excel |
| GET | `/api/unrecognized` | 查看非发票文档 |
| DELETE | `/api/invoices/{filename}` | 删除发票 |

---

## License / 许可证

MIT

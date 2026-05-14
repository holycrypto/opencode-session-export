# OpenCode Workflow — 会话历史导出工具

将 [OpenCode](https://github.com/opencode-ai/opencode) 的本地 SQLite 数据库中的所有对话记录（含归档会话）导出为可浏览的静态 HTML 网站。

## 调用方式

### 直接运行

```bash
python3 opencode_export.py
```

或赋予执行权限后：

```bash
chmod +x opencode_export.py
./opencode_export.py
```

无需任何参数。脚本会：
1. 读取 `~/.local/share/opencode/opencode.db`
2. 输出到当前目录下的 `./opencode_export/`
3. 用浏览器打开 `./opencode_export/index.html` 即可浏览

支持重复运行——增量导出，仅处理新增或修改的会话。

### 依赖

无第三方依赖，仅使用 Python 标准库（sqlite3、json、html、pathlib 等）。

### 自定义模板

首次运行会在脚本同目录生成 `opencode_export_template.html`，可自行修改样式。后续导出将使用该模板渲染会话页面。

## 设计思路

### 核心原则

- **单文件、零依赖** — 一个 `.py` 文件完成所有工作，降低使用门槛
- **只读访问** — 以 `?mode=ro` 连接数据库，杜绝误写风险
- **增量更新** — 通过 `.export_manifest.json` 记录已导出会话的 ID 和更新时间，重复运行时跳过未变更的会话
- **静态输出** — 纯 HTML，无需服务器，双击即可查看

### 架构分层

```
opencode_export.py
├── 数据库访问层        读取 session / message / part 三张表
├── 内容渲染层          将消息部件（文本、工具调用、推理、补丁等）转为 HTML
├── 索引生成层          生成根索引和项目索引页
├── 增量导出逻辑        Manifest 加载/保存、目录 slug 生成
└── 主流程编排          串联上述模块，完成完整导出
```

### 数据模型（来自 OpenCode 数据库）

| 表 | 说明 |
|---|---|
| `session` | 会话元信息：标题、工作目录、时间戳、文件变更统计 |
| `message` | 属于某个 session，存储角色（user/assistant）等 JSON 数据 |
| `part` | 属于某条 message，存储具体内容类型（text、tool_call、reasoning、patch、file、compaction） |

### 输出结构

```
opencode_export/
├── index.html                        # 根索引，按项目分组列出所有会话
├── .export_manifest.json             # 增量导出清单
├── {project_slug}/
│   ├── index.html                    # 项目索引，列出该项目所有会话
│   └── {session_id}.html             # 单个会话的完整对话记录
└── ...
```

项目 slug 格式为 `{目录名}_{md5前6位}`，避免同名目录冲突。

### 渲染细节

- 使用 Tailwind CDN 提供基础样式，配合自定义 CSS 实现 Notion 风格的阅读体验
- 模板采用 `{{placeholder}}` 占位符替换，简单直接
- 支持渲染多种消息部件类型：纯文本、工具调用、推理过程、代码补丁、文件引用、上下文压缩标记

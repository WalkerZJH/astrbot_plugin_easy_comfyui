# AstrBot Plugin: Easy Comfyui

<div align="center">

![Version](https://img.shields.io/badge/version-1.1.0-blue)
![Platform](https://img.shields.io/badge/platform-AstrBot-green)
![Backend](https://img.shields.io/badge/backend-ComfyUI-orange)

**🎨 基于 ComfyUI 的 Stable Diffusion 图像生成插件**

*上手即用 · 自动解析工作流 · 指令简洁 · 无需复杂参数*

</div>

---

## ✨ 特性亮点

| 特性 | 说明 |
|------|------|
| 🚀 **开箱即用** | 只需配置 ComfyUI 地址，放入工作流文件即可使用 |
| 🔧 **自动解析** | 智能识别工作流节点，自动映射提示词、采样器等参数 |
| 💬 **指令简洁** | 用户只需 `/sdl gen 描述` 即可生成图片，无需记忆复杂参数 |
| 🤖 **LLM 增强** | 可选使用 LLM 自动将中文描述翻译为专业英文提示词 |
| 🖼️ **图生图** | 支持回复图片、发送图片、@用户头像作为参考图 |
| 📊 **并发控制** | 内置任务队列和并发限制，避免服务过载 |
| 🎯 **多工作流** | 支持多个工作流自由切换，适配不同风格模型 |

---

## 📁 项目结构

```
astrbot_plugin_easy_comfyui/
├── main.py              # 插件主入口，命令注册与处理
├── comfyui_client.py    # ComfyUI API 客户端
├── workflow_parser.py   # 工作流解析器，自动识别节点
├── image_fetcher.py     # 图片获取工具（消息图片/头像）
├── _conf_schema.json    # 配置项定义
├── metadata.yaml        # 插件元信息
├── requirements.txt     # 依赖项
├── README.md            # 本文档
└── workflows/           # 工作流文件夹 ⚠️ 重要！
    ├── example1.json    # 工作流文件（ComfyUI 导出）
    └── ...
```

---

## 🚀 部署指南

### 前置要求

- [AstrBot](https://github.com/Soulter/AstrBot) 已安装并运行
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) 已部署并可访问
- Python 3.10+

### 安装步骤

#### 1. 配置 ComfyUI 地址

在 AstrBot 管理面板中配置插件：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `comfyui_url` | `http://localhost:8188` | ComfyUI API 地址 |
| `session_timeout_time` | `120` | 超时时间（秒） |
| `max_concurrent_tasks` | `3` | 最大并发任务数 |

#### 2. 添加工作流文件

将 ComfyUI 导出的工作流 JSON 文件放入 `workflows/` 文件夹：

```bash
# 工作流文件位置
<插件目录>/workflows/your_workflow.json
```

> **📌 如何导出工作流？**
> 
> 在 ComfyUI Web 界面中：设置 → 导出 → 选择 **API Format (.json)**

⚠️ **重要警告**

```
工作流文件存储在插件目录内的 workflows/ 文件夹中。
插件更新或重装时，此文件夹可能被覆盖！
请务必在更新插件前备份您的工作流文件！
```

#### 4. 重启 AstrBot

重启后发送 `/sdl check` 验证连接状态。

---

## 📖 命令手册

### 基础命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/sdl gen <提示词>` | 文生图 | `/sdl gen 一只可爱的猫咪` |
| `/sdl i2i <提示词>` | 图生图 | 回复图片后发送 `/sdl i2i 转为动漫风格` |
| `/sdl check` | 检查服务状态 | `/sdl check` |
| `/sdl help` | 显示帮助 | `/sdl help` |

### 设置命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/sdl wf` | 查看工作流列表 | `/sdl wf` |
| `/sdl wf <索引>` | 切换工作流 | `/sdl wf 2` |
| `/sdl wf reload` | 重新加载工作流 | `/sdl wf reload` |

### 管理员命令

| 命令 | 说明 |
|------|------|
| `/sdl verbose` | 切换详细输出模式 |
| `/sdl llm` | 切换 LLM 提示词生成 |
| `/sdl showprompt` | 切换显示最终提示词 |
| `/sdl debug` | 显示调试信息（节点映射等） |

### 图生图使用方式

图生图命令 `/sdl i2i` 支持三种图片来源（按优先级）：

1. **回复图片** - 回复一条包含图片的消息，然后发送命令
2. **发送图片** - 在同一条消息中发送图片和命令
3. **@用户** - @某人后发送命令，将使用其 QQ 头像

---

## ⚙️ 配置说明

### 基础配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `comfyui_url` | string | `http://localhost:8188` | ComfyUI 服务地址 |
| `verbose` | bool | `true` | 详细输出（显示进度提示） |
| `session_timeout_time` | int | `120` | 请求超时时间（秒） |
| `max_concurrent_tasks` | int | `3` | 最大并发任务数 |

### 提示词配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_generate_prompt` | bool | `true` | 使用 LLM 生成英文提示词 |
| `positive_prompt_global` | string | `masterpiece, best quality, ` | 全局正向提示词前缀 |
| `negative_prompt_global` | string | `nsfw, paintings, sketches, ` | 全局负向提示词 |
| `enable_positive_prompt_add_in_head_or_tail` | bool | `true` | 全局提示词加在开头(true)/结尾(false) |
| `prompt_guidelines` | string | `请优先使用动漫风格相关的描述词` | LLM 生成提示词的附加指导 |
| `enable_show_positive_prompt` | bool | `false` | 生成前显示最终提示词 |

### 其他配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `default_workflow_index` | int | `1` | 默认工作流索引 |

> 图像尺寸由工作流文件定义，不支持用户或管理员修改。

---

## 🔧 工作流要求

插件会自动解析工作流 JSON 并识别以下节点：

| 节点类型 | 支持的 class_type | 用途 |
|----------|-------------------|------|
| 文本编码 | `CLIPTextEncode`, `CLIPTextEncodeSDXL`, `CLIPTextEncodeSD3` 等 | 正/负向提示词 |
| 采样器 | `KSampler`, `KSamplerAdvanced`, `SamplerCustom` 等 | Seed 同步 |
| 加载图像 | `LoadImage`, `LoadImageMask` | 图生图输入 |
| 输出 | `SaveImage`, `PreviewImage` | 获取生成结果 |

### 兼容性提示

- ✅ 支持 SD1.5、SDXL、SD3 等主流模型的工作流
- ✅ 支持 LoRA、ControlNet 等扩展节点
- ✅ 自动穿透 Reroute 节点进行链路追踪

---

## ❓ 常见问题

### Q: 工作流节点未识别怎么办？

使用 `/sdl debug` 查看节点映射情况，确保工作流包含必要的节点类型。

### Q: 图生图失败提示"不支持"？

当前工作流需要包含 `LoadImage` 节点才能支持图生图。请切换到支持图生图的工作流，或在 ComfyUI 中添加 LoadImage 节点。

### Q: 如何备份工作流？

工作流文件存储在：
```
<AstrBot>/data/plugins/astrbot_plugin_easy_comfyui/workflows/
```

更新插件前请手动备份此文件夹。

---

## 📄 许可证

AGPL-3.0 license

## 🙏 致谢

- [AstrBot](https://github.com/Soulter/AstrBot) - 强大的聊天机器人框架
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) - 模块化 Stable Diffusion GUI
- [秋叶整合包](https://comfyui-wiki.com/zh/install/install-comfyui/aaaki-comfyui-launcher-user-guide) - 便捷的 ComfyUI 安装方案

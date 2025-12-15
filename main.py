"""AstrBot ComfyUI 图像生成插件"""

import asyncio
import base64
import os
import re
import shutil
import time
import uuid as uuid_mod

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Image

try:
    from .comfyui_client import ComfyUIClient
    from .image_fetcher import ImageFetcher
    from .workflow_parser import WorkflowParser
except ImportError:
    from comfyui_client import ComfyUIClient
    from image_fetcher import ImageFetcher
    from workflow_parser import WorkflowParser

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_WORKFLOWS_DIR = os.path.join(PLUGIN_DIR, "workflows")  # 插件内置工作流目录
WORKFLOWS_DIR = None  # 用户工作流目录，将在插件初始化时设置
TEMP_PATH = None  # 将在插件初始化时设置


@register(
    "astrbot_plugin_easy_comfyui",
    "WalkerZJH",
    "基于 ComfyUI 的简易 AI 生图插件",
    "1.1.0",
)
class SDGeneratorComfyUI(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._validate_config()

        global TEMP_PATH, WORKFLOWS_DIR
        data_dir = StarTools.get_data_dir(self.context, "astrbot_plugin_easy_comfyui")
        TEMP_PATH = str(data_dir / "temp")
        WORKFLOWS_DIR = str(data_dir / "workflows")
        os.makedirs(TEMP_PATH, exist_ok=True)
        os.makedirs(WORKFLOWS_DIR, exist_ok=True)
        
        # 同步内置工作流到用户目录（名称相同则更新，不存在则添加，不删除用户目录中的文件）
        self._sync_bundled_workflows()

        self.comfyui = ComfyUIClient(
            base_url=self.config.get("comfyui_url", "http://localhost:8188"),
            timeout=self.config.get("session_timeout_time", 120),
        )
        self.workflow_parser = WorkflowParser(WORKFLOWS_DIR)
        self.user_settings: dict = {}
        self.active_tasks = 0
        self.max_concurrent_tasks = config.get("max_concurrent_tasks", 3)
        self.task_semaphore = asyncio.Semaphore(self.max_concurrent_tasks)

    def _sync_bundled_workflows(self):
        """同步内置工作流到用户目录
        
        名称相同则更新，不存在则添加，不删除用户目录中已有的文件
        """
        if not os.path.exists(BUNDLED_WORKFLOWS_DIR):
            return
        
        for filename in os.listdir(BUNDLED_WORKFLOWS_DIR):
            if not filename.endswith(".json"):
                continue
            src_path = os.path.join(BUNDLED_WORKFLOWS_DIR, filename)
            dst_path = os.path.join(WORKFLOWS_DIR, filename)
            try:
                shutil.copy2(src_path, dst_path)
                logger.debug(f"同步工作流: {filename}")
            except Exception as e:
                logger.warning(f"同步工作流 {filename} 失败: {e}")

    def _validate_config(self):
        """验证配置"""
        comfyui_url = self.config.get("comfyui_url", "http://localhost:8188").strip()
        if not comfyui_url.startswith(("http://", "https://")):
            raise ValueError("ComfyUI地址必须以http://或https://开头")
        if comfyui_url.endswith("/"):
            self.config["comfyui_url"] = comfyui_url.rstrip("/")
            self.config.save_config()

    def _get_user_settings(self, user_id: str) -> dict:
        """获取用户设置"""
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {
                "workflow": self.config.get("default_workflow_index", 1),
            }
        return self.user_settings[user_id]

    async def _generate_prompt(self, prompt: str) -> str:
        """使用 LLM 生成提示词"""
        provider = self.context.get_using_provider()
        if not provider:
            return ""

        guidelines = self.config.get("prompt_guidelines", "")
        system_prompt = (
            "请根据以下描述生成用于 Stable Diffusion 的英文提示词，"
            "返回逗号分隔的英文字符串，包含主体、风格、光照、色彩等描述，"
            "直接返回prompt，不要额外说明。"
            f"{guidelines}\n描述："
        )
        response = await provider.text_chat(f"{system_prompt} {prompt}", session_id=None)
        if response.completion_text:
            return re.sub(r"<think>[\s\S]*</think>", "", response.completion_text).strip()
        return ""

    def _build_final_prompt(self, user_prompt: str) -> str:
        """构建最终提示词"""
        global_prompt = self.config.get("positive_prompt_global", "")
        add_in_head = self.config.get("enable_positive_prompt_add_in_head_or_tail", True)
        return global_prompt + user_prompt if add_in_head else user_prompt + global_prompt

    def _get_generation_params(self, user_id: str) -> str:
        """获取当前生成参数"""
        settings = self._get_user_settings(user_id)
        workflow_idx = settings["workflow"]

        workflow_info = self.workflow_parser.get_workflow(workflow_idx)
        workflow_name = workflow_info.name if workflow_info else "未设置"
        workflow_desc = workflow_info.description if workflow_info else ""

        return (
            f"🎨 当前设置:\n"
            f"- 工作流: [{workflow_idx}] {workflow_name}\n"
            f"  └ {workflow_desc}\n"
            f"- 详细输出: {'开启' if self.config.get('verbose', True) else '关闭'}\n"
            f"- LLM生成提示词: {'开启' if self.config.get('enable_generate_prompt', False) else '关闭'}\n"
            f"- 显示提示词: {'开启' if self.config.get('enable_show_positive_prompt', False) else '关闭'}"
        )

    # ==================== 命令组 ====================

    @filter.command_group("sdl")
    def sdl(self):
        """ComfyUI 生图命令组"""
        pass

    @sdl.command("check")
    async def check(self, event: AstrMessageEvent):
        """检查绘图服务状态"""
        try:
            available, status_msg = await self.comfyui.check_health()
            if available:
                running, pending = await self.comfyui.get_queue_status()
                yield event.plain_result(
                    f"✅ 绘图服务正常\n"
                    f"📊 队列: 运行中 {running}, 等待中 {pending}\n"
                    f"🔧 并发: {self.active_tasks}/{self.max_concurrent_tasks}"
                )
            else:
                yield event.plain_result(f"❌ 绘图服务不可用: {status_msg}")
        except Exception as e:
            logger.error(f"检查服务状态错误: {e}")
            yield event.plain_result("❌ 检查服务状态失败")

    @sdl.command("gen")
    async def generate_image(self, event: AstrMessageEvent, prompt: str = ""):
        """文生图"""
        user_id = event.get_sender_id()

        if not prompt or not prompt.strip():
            try:
                raw_message = getattr(event, "message_str", "")
                if raw_message:
                    match = re.search(r"/sdl\s+gen\s+(.+)", raw_message, re.IGNORECASE | re.DOTALL)
                    if match:
                        prompt = match.group(1).strip()
            except Exception as e:
                logger.warning(f"提取prompt失败: {e}")

        if not prompt or not prompt.strip():
            yield event.plain_result("❌ 请提供提示词\n用法: /sdl gen <提示词>")
            return

        async with self.task_semaphore:
            self.active_tasks += 1
            try:
                available, _ = await self.comfyui.check_health()
                if not available:
                    yield event.plain_result("❌ 绘图服务不可用")
                    return

                start_time = time.time()
                verbose = self.config.get("verbose", True)
                if verbose:
                    yield event.plain_result("🖌️ 开始画画...")

                settings = self._get_user_settings(user_id)
                workflow_idx = settings["workflow"]

                if not self.workflow_parser.get_workflow(workflow_idx):
                    yield event.plain_result(f"❌ 工作流 [{workflow_idx}] 不存在")
                    return

                if self.config.get("enable_generate_prompt", False):
                    generated = await self._generate_prompt(prompt)
                    positive_prompt = self._build_final_prompt(generated or prompt)
                else:
                    positive_prompt = self._build_final_prompt(prompt)

                if self.config.get("enable_show_positive_prompt", False):
                    yield event.plain_result(f"📝 提示词:\n{positive_prompt}")

                negative_prompt = self.config.get("negative_prompt_global", "")

                workflow_result = self.workflow_parser.prepare_workflow(
                    workflow_index=workflow_idx,
                    positive_prompt=positive_prompt,
                    negative_prompt=negative_prompt,
                )

                if not workflow_result or not workflow_result[0]:
                    yield event.plain_result("❌ 准备工作流失败")
                    return

                workflow, prepared_seed, _ = workflow_result
                timeout = self.config.get("session_timeout_time", 120)
                success, image_data, status_msg, seed = await self.comfyui.execute_workflow(
                    workflow=workflow,
                    timeout_seconds=timeout,
                    known_seed=prepared_seed,
                )

                if not success:
                    yield event.plain_result(f"❌ 生成失败: {status_msg}")
                    return

                image_base64 = base64.b64encode(image_data).decode("utf-8")
                yield event.chain_result([Image.fromBase64(image_base64)])

                if verbose:
                    elapsed = time.time() - start_time
                    yield event.plain_result(
                        f"✅ 生成成功 | ⏱️ {elapsed:.2f}s | Seed: {seed or 'N/A'}"
                    )

            except asyncio.TimeoutError:
                yield event.plain_result("⚠️ 请求超时")
            except Exception as e:
                logger.error(f"生成图像错误: {e}")
                yield event.plain_result("❌ 生成失败，请查看日志")
            finally:
                self.active_tasks -= 1

    @sdl.command("i2i")
    async def img2img(self, event: AstrMessageEvent, prompt: str = ""):
        """图生图"""
        user_id = event.get_sender_id()

        if not prompt or not prompt.strip():
            try:
                raw_message = getattr(event, "message_str", "")
                if raw_message:
                    match = re.search(r"/sdl\s+i2i\s+(.+)", raw_message, re.IGNORECASE | re.DOTALL)
                    if match:
                        prompt = match.group(1).strip()
            except Exception as e:
                logger.warning(f"提取prompt失败: {e}")

        if not prompt or not prompt.strip():
            yield event.plain_result(
                "❌ 请提供提示词\n用法: /sdl i2i <提示词>\n\n"
                "📷 图片来源:\n1. 回复图片\n2. 发送图片+命令\n3. @某人(头像)"
            )
            return

        async with self.task_semaphore:
            self.active_tasks += 1
            try:
                available, _ = await self.comfyui.check_health()
                if not available:
                    yield event.plain_result("❌ 绘图服务不可用")
                    return

                verbose = self.config.get("verbose", True)
                if verbose:
                    yield event.plain_result("🔍 获取参考图片...")

                image_data = await ImageFetcher.extract_image_data(event)
                if not image_data:
                    yield event.plain_result(
                        "❌ 未找到参考图片\n\n"
                        "📷 请通过以下方式提供:\n1. 回复图片\n2. 发送图片+命令\n3. @某人(头像)\n\n"
                        "⚠️ 引用图片需重新发送"
                    )
                    return

                if verbose:
                    yield event.plain_result("📤 上传图片...")

                upload_filename = f"i2i_{uuid_mod.uuid4().hex}.png"
                uploaded_name = await self.comfyui.upload_image(image_data, filename=upload_filename)

                if not uploaded_name:
                    yield event.plain_result("❌ 图片上传失败")
                    return

                start_time = time.time()
                if verbose:
                    yield event.plain_result("🖌️ 开始图生图...")

                settings = self._get_user_settings(user_id)
                workflow_idx = settings["workflow"]
                workflow_info = self.workflow_parser.get_workflow(workflow_idx)

                if not workflow_info:
                    yield event.plain_result(f"❌ 工作流 [{workflow_idx}] 不存在")
                    return

                if not workflow_info.node_mapping.load_image_node:
                    yield event.plain_result(f"❌ 工作流 [{workflow_idx}] 不支持图生图")
                    return

                if self.config.get("enable_generate_prompt", False):
                    generated = await self._generate_prompt(prompt)
                    positive_prompt = self._build_final_prompt(generated or prompt)
                else:
                    positive_prompt = self._build_final_prompt(prompt)

                if self.config.get("enable_show_positive_prompt", False):
                    yield event.plain_result(f"📝 提示词:\n{positive_prompt}")

                negative_prompt = self.config.get("negative_prompt_global", "")
                workflow_result = self.workflow_parser.prepare_workflow(
                    workflow_index=workflow_idx,
                    positive_prompt=positive_prompt,
                    negative_prompt=negative_prompt,
                    input_image_filename=uploaded_name,
                )

                if not workflow_result or not workflow_result[0]:
                    yield event.plain_result("❌ 准备工作流失败")
                    return

                workflow, prepared_seed, _ = workflow_result
                timeout = self.config.get("session_timeout_time", 120)
                success, image_data, status_msg, seed = await self.comfyui.execute_workflow(
                    workflow=workflow,
                    timeout_seconds=timeout,
                    known_seed=prepared_seed,
                )

                if not success:
                    yield event.plain_result(f"❌ 图生图失败: {status_msg}")
                    return

                image_base64 = base64.b64encode(image_data).decode("utf-8")
                yield event.chain_result([Image.fromBase64(image_base64)])

                if verbose:
                    elapsed = time.time() - start_time
                    yield event.plain_result(
                        f"✅ 完成 | ⏱️ {elapsed:.2f}s | Seed: {seed or 'N/A'}"
                    )

            except asyncio.TimeoutError:
                yield event.plain_result("⚠️ 请求超时")
            except Exception as e:
                logger.error(f"图生图错误: {e}")
                yield event.plain_result("❌ 图生图失败，请查看日志")
            finally:
                self.active_tasks -= 1

    @sdl.command("wf")
    async def workflow_command(self, event: AstrMessageEvent, action: str = ""):
        """工作流管理"""
        user_id = event.get_sender_id()
        settings = self._get_user_settings(user_id)
        current_wf = settings["workflow"]
        workflows = self.workflow_parser.list_workflows()

        wf_lines = []
        for idx, name, desc in workflows:
            marker = "▶️" if idx == current_wf else "  "
            wf_lines.append(f"{marker} [{idx}] {name}")
            if desc:
                wf_lines.append(f"      └ {desc}")
        wf_list_str = "\n".join(wf_lines) if wf_lines else "  (暂无工作流)"

        if not action:
            yield event.plain_result(
                f"📂 工作流\n━━━━━━━━━━━━━━━━━━━━━━\n{wf_list_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"/sdl wf <索引> - 切换\n/sdl wf reload - 重载"
            )
            return

        if action == "reload":
            self.workflow_parser.reload_workflows()
            count = self.workflow_parser.get_workflow_count()
            yield event.plain_result(f"🔄 已重载 {count} 个工作流")
            return

        try:
            index = int(action)
        except ValueError:
            yield event.plain_result(f"⚠️ 无效参数: {action}")
            return

        if index <= 0:
            yield event.plain_result("⚠️ 索引需大于0")
            return

        workflow_info = self.workflow_parser.get_workflow(index)
        if not workflow_info:
            yield event.plain_result(f"❌ 工作流 [{index}] 不存在")
            return

        settings["workflow"] = index
        yield event.plain_result(f"✅ 已切换到 [{index}] {workflow_info.name}")

    # ==================== 管理员命令 ====================

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sdl.command("verbose")
    async def set_verbose(self, event: AstrMessageEvent):
        """切换详细输出"""
        current = self.config.get("verbose", True)
        self.config["verbose"] = not current
        self.config.save_config()
        yield event.plain_result(f"📢 详细输出: {'开启' if not current else '关闭'}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sdl.command("llm")
    async def set_generate_prompt(self, event: AstrMessageEvent):
        """切换LLM提示词生成"""
        current = self.config.get("enable_generate_prompt", False)
        self.config["enable_generate_prompt"] = not current
        self.config.save_config()
        yield event.plain_result(f"🤖 LLM提示词: {'开启' if not current else '关闭'}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sdl.command("showprompt")
    async def set_show_prompt(self, event: AstrMessageEvent):
        """切换显示提示词"""
        current = self.config.get("enable_show_positive_prompt", False)
        self.config["enable_show_positive_prompt"] = not current
        self.config.save_config()
        yield event.plain_result(f"📝 显示提示词: {'开启' if not current else '关闭'}")

    @sdl.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助"""
        yield event.plain_result(
            "🎨 ComfyUI 生图帮助\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 基础:\n"
            "  /sdl gen <提示词> - 文生图\n"
            "  /sdl i2i <提示词> - 图生图\n"
            "  /sdl check - 检查状态\n\n"
            "📷 图生图:\n"
            "  回复/发送图片 + 命令\n"
            "  @某人 + 命令(用头像)\n\n"
            "📂 工作流: /sdl wf\n\n"
            "⚙️ 管理员:\n"
            "  /sdl verbose|llm|showprompt\n"
            "  /sdl debug - 调试信息\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 工作流放入 workflows 目录"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sdl.command("debug")
    async def debug_workflow(self, event: AstrMessageEvent):
        """调试信息"""
        user_id = event.get_sender_id()
        settings = self._get_user_settings(user_id)
        workflow_idx = settings["workflow"]

        params = self._get_generation_params(user_id)
        global_positive = self.config.get("positive_prompt_global", "").strip()[:50]
        global_negative = self.config.get("negative_prompt_global", "").strip()[:50]

        total = self.workflow_parser.get_workflow_count()
        workflows_list = self.workflow_parser.list_workflows()

        workflow_info = self.workflow_parser.get_workflow(workflow_idx)
        if not workflow_info:
            yield event.plain_result(f"{params}\n\n❌ 工作流 [{workflow_idx}] 不存在")
            return

        mapping = workflow_info.node_mapping
        workflow_data = workflow_info.workflow_data

        sampler_info = ""
        if mapping.sampler_node and mapping.sampler_node in workflow_data:
            inputs = workflow_data[mapping.sampler_node].get("inputs", {})
            sampler_info = f"seed={inputs.get('seed')}, steps={inputs.get('steps')}"

        latent_info = ""
        if mapping.latent_image_node and mapping.latent_image_node in workflow_data:
            inputs = workflow_data[mapping.latent_image_node].get("inputs", {})
            latent_info = f"{inputs.get('width', '?')}x{inputs.get('height', '?')}"

        wf_list = "\n".join(
            f"  {'▶' if idx == workflow_idx else ' '}[{idx}] {name}"
            for idx, name, _ in workflows_list
        )

        yield event.plain_result(
            f"🔧 调试信息\n━━━━━━━━━━━━━━━━━━━━━━\n{params}\n\n"
            f"📜 全局正向: {global_positive or '(未设置)'}...\n"
            f"🚫 全局负向: {global_negative or '(未设置)'}...\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 工作流 ({total}):\n{wf_list}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 [{workflow_idx}] {workflow_info.name}\n"
            f"  正向节点: {mapping.positive_prompt_node or '❌'}\n"
            f"  负向节点: {mapping.negative_prompt_node or '❌'}\n"
            f"  采样器: {', '.join(mapping.sampler_nodes) or '❌'}\n"
            f"  Latent: {mapping.latent_image_node or '❌'} ({latent_info})\n"
            f"  采样参数: {sampler_info}"
        )

    async def cleanup(self):
        """清理资源"""
        await self.comfyui.close()

"""ComfyUI API 客户端"""

import asyncio
import json
import random
import uuid
from typing import Any

import aiohttp
from astrbot.api import logger


class ComfyUIClient:
    """ComfyUI API 客户端"""

    def __init__(self, base_url: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session: aiohttp.ClientSession | None = None
        self.client_id = str(uuid.uuid4())

    async def ensure_session(self):
        """确保会话连接"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))

    async def close(self):
        """关闭会话"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def check_health(self) -> tuple[bool, str]:
        """检查服务状态"""
        try:
            await self.ensure_session()
            timeout = aiohttp.ClientTimeout(total=min(self.timeout, 10))  # 健康检查最多10秒
            async with self.session.get(f"{self.base_url}/system_stats", timeout=timeout) as resp:
                if resp.status == 200:
                    return True, "服务正常"
                return False, f"HTTP {resp.status}"
        except asyncio.TimeoutError:
            return False, "连接超时"
        except aiohttp.ClientError as e:
            return False, f"连接错误: {e}"
        except Exception as e:
            return False, f"未知错误: {e}"

    async def queue_prompt(self, workflow: dict[str, Any]) -> tuple[str | None, int | None]:
        """提交工作流到队列"""
        try:
            await self.ensure_session()
            fixed_workflow, current_seed = self._enforce_deterministic_workflow(workflow)
            payload = {"prompt": fixed_workflow, "client_id": self.client_id}

            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with self.session.post(
                f"{self.base_url}/prompt", json=payload, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("prompt_id"), current_seed
                error_text = await resp.text()
                logger.error(f"提交工作流失败: {resp.status} - {error_text}")
                return None, None
        except Exception as e:
            logger.error(f"提交工作流异常: {e}")
            return None, None

    async def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """获取执行历史"""
        try:
            await self.ensure_session()
            timeout = aiohttp.ClientTimeout(total=min(self.timeout, 30))  # 历史查询最多30秒
            async with self.session.get(
                f"{self.base_url}/history/{prompt_id}", timeout=timeout
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.error(f"获取历史记录异常: {e}")
            return None

    async def get_image(
        self, filename: str, subfolder: str = "", folder_type: str = "output"
    ) -> bytes | None:
        """获取生成的图像"""
        try:
            await self.ensure_session()
            params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with self.session.get(
                f"{self.base_url}/view", params=params, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.error(f"获取图像失败: {resp.status}")
                return None
        except Exception as e:
            logger.error(f"获取图像异常: {e}")
            return None

    async def upload_image(
        self, image_data: bytes, filename: str = "input.png", overwrite: bool = True
    ) -> str | None:
        """上传图像到ComfyUI"""
        try:
            await self.ensure_session()
            data = aiohttp.FormData()
            data.add_field("image", image_data, filename=filename, content_type="image/png")
            data.add_field("overwrite", str(overwrite).lower())

            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with self.session.post(
                f"{self.base_url}/upload/image", data=data, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("name")
                logger.error(f"上传图像失败: {resp.status}")
                return None
        except Exception as e:
            logger.error(f"上传图像异常: {e}")
            return None

    async def wait_for_completion(
        self,
        prompt_id: str,
        timeout_seconds: int = 120,
        poll_interval: float = 1.0,
        cancel_on_timeout: bool = True,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """等待工作流完成"""
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout_seconds:
                if cancel_on_timeout:
                    await self.cancel_prompt(prompt_id)
                return False, None, f"执行超时({timeout_seconds}s)"

            history = await self.get_history(prompt_id)
            if history and prompt_id in history:
                prompt_history = history[prompt_id]

                if "outputs" in prompt_history:
                    if "status" in prompt_history:
                        status = prompt_history["status"]
                        if status.get("status_str") == "error":
                            logger.error(f"ComfyUI执行错误: {status.get('messages', [])}")
                            return False, None, "执行出错"
                    return True, prompt_history, "执行完成"

            await asyncio.sleep(poll_interval)

    async def execute_workflow(
        self,
        workflow: dict[str, Any],
        timeout_seconds: int = 120,
        known_seed: int | None = None,
    ) -> tuple[bool, bytes | None, str, int | None]:
        """执行工作流并获取结果图像"""
        prompt_id, detected_seed = await self.queue_prompt(workflow)
        if not prompt_id:
            return False, None, "提交工作流失败", None

        used_seed = known_seed if known_seed is not None else detected_seed
        logger.info(f"ComfyUI任务提交 ID: {prompt_id}, Seed: {used_seed}")

        success, result, status_msg = await self.wait_for_completion(
            prompt_id, timeout_seconds=timeout_seconds
        )

        if not success:
            return False, None, status_msg, used_seed

        target_image = None
        if result and "outputs" in result:
            for node_output in result["outputs"].values():
                if "images" not in node_output:
                    continue
                for image_info in node_output["images"]:
                    img_data = {
                        "filename": image_info.get("filename"),
                        "subfolder": image_info.get("subfolder", ""),
                        "folder_type": image_info.get("type", "output"),
                    }
                    if img_data["folder_type"] == "output":
                        target_image = img_data
                        break
                    if target_image is None:
                        target_image = img_data
                if target_image and target_image["folder_type"] == "output":
                    break

        if target_image:
            image_data = await self.get_image(
                target_image["filename"],
                target_image["subfolder"],
                target_image["folder_type"],
            )
            if image_data:
                return True, image_data, "生成成功", used_seed

        return False, None, "未找到输出图像", used_seed

    def _enforce_deterministic_workflow(
        self, workflow: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        """强制固定随机种子"""
        workflow_copy = json.loads(json.dumps(workflow))
        main_seed = None
        ksampler_seed = None

        for node_data in workflow_copy.values():
            if not isinstance(node_data, dict) or "inputs" not in node_data:
                continue

            inputs = node_data["inputs"]
            class_type = node_data.get("class_type", "")
            seed_keys = [k for k in inputs if "seed" in k.lower()]

            for key in seed_keys:
                current_seed = inputs[key]
                if not isinstance(current_seed, (int, float)) or current_seed == -1:
                    inputs[key] = random.randint(1, 2**63 - 1)

                if "Sampler" in class_type:
                    ksampler_seed = int(inputs[key])
                elif main_seed is None:
                    main_seed = int(inputs[key])

            if "control_after_generate" in inputs:
                if inputs["control_after_generate"] in ["randomize", "increment", "decrement"]:
                    inputs["control_after_generate"] = "fixed"

        final_seed = ksampler_seed or main_seed or 0
        return workflow_copy, final_seed

    async def interrupt(self) -> bool:
        """中断当前执行"""
        try:
            await self.ensure_session()
            timeout = aiohttp.ClientTimeout(total=5)
            async with self.session.post(f"{self.base_url}/interrupt", timeout=timeout) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"中断执行异常: {e}")
            return False

    async def cancel_prompt(self, prompt_id: str) -> bool:
        """取消指定任务"""
        try:
            await self.ensure_session()
            timeout = aiohttp.ClientTimeout(total=5)
            payload = {"delete": [prompt_id]}
            async with self.session.post(
                f"{self.base_url}/api/queue", json=payload, timeout=timeout
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"删除队列任务返回: {resp.status}")
            await self.interrupt()
            return True
        except Exception as e:
            logger.error(f"取消任务异常: {e}")
            return False

    async def get_queue_status(self) -> tuple[int, int]:
        """获取队列状态"""
        try:
            await self.ensure_session()
            timeout = aiohttp.ClientTimeout(total=5)
            async with self.session.get(f"{self.base_url}/queue", timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return len(data.get("queue_running", [])), len(data.get("queue_pending", []))
                return 0, 0
        except Exception as e:
            logger.error(f"获取队列状态异常: {e}")
            return 0, 0

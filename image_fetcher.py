"""AstrBot 图片获取工具"""

import os
import tempfile
import uuid

import aiofiles
import aiohttp
from astrbot.api import logger
from astrbot.api.all import At, Image, Reply
from astrbot.api.event import AstrMessageEvent


class ImageFetcher:
    """图片获取工具类"""

    @staticmethod
    async def download_url(url: str) -> bytes | None:
        """下载 URL 图片"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    logger.warning(f"下载失败: {resp.status}, URL: {url}")
                    return None
        except Exception as e:
            logger.warning(f"下载异常: {e}")
            return None

    @staticmethod
    async def get_avatar_bytes(user_id: str) -> bytes | None:
        """获取 QQ 头像"""
        if not user_id.isdigit():
            return None
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
        return await ImageFetcher.download_url(avatar_url)

    @classmethod
    async def get_image_urls(cls, event: AstrMessageEvent) -> list[str]:
        """从事件提取图片 URL"""
        urls: list[str] = []

        if not hasattr(event, "message_obj") or not event.message_obj:
            return urls

        if hasattr(event.message_obj, "message"):
            for comp in event.message_obj.message:
                if isinstance(comp, Image):
                    url = await cls._component_to_http_url(comp)
                    if url:
                        urls.append(url)
                elif isinstance(comp, Reply) and getattr(comp, "chain", None):
                    for r in comp.chain:
                        if isinstance(r, Image):
                            url = await cls._component_to_http_url(r)
                            if url:
                                urls.append(url)
        return urls

    @staticmethod
    async def _component_to_http_url(comp) -> str | None:
        """组件转 HTTP URL"""
        try:
            fn = getattr(comp, "convert_to_web_link", None)
            if callable(fn):
                return await fn()
        except Exception:
            pass

        for attr in ("url", "file"):
            try:
                val = getattr(comp, attr, None)
                if isinstance(val, str) and val.startswith("http"):
                    return val
            except Exception:
                pass

        try:
            path_val = getattr(comp, "path", None)
            if isinstance(path_val, str) and path_val:
                img_comp = Image.fromFileSystem(path_val)
                try:
                    return await img_comp.convert_to_web_link()
                except Exception:
                    pass
        except Exception:
            pass

        return None

    @classmethod
    async def extract_image_data(cls, event: AstrMessageEvent) -> bytes | None:
        """提取图片二进制数据"""
        urls = await cls.get_image_urls(event)
        if urls:
            image_data = await cls.download_url(urls[0])
            if image_data:
                logger.info(f"成功获取图片: {urls[0][:60]}...")
                return image_data

        if hasattr(event, "message_obj") and event.message_obj and hasattr(event.message_obj, "message"):
            for comp in event.message_obj.message:
                if isinstance(comp, At):
                    qq_id = getattr(comp, "qq", None) or getattr(comp, "uin", None)
                    if qq_id:
                        avatar_data = await cls.get_avatar_bytes(str(qq_id))
                        if avatar_data:
                            logger.info(f"成功获取头像: {qq_id}")
                            return avatar_data
        return None

    @classmethod
    async def save_to_temp_file(cls, event: AstrMessageEvent, prefix: str = "img2img_") -> str | None:
        """提取图片并保存为临时文件"""
        image_data = await cls.extract_image_data(event)
        if not image_data:
            return None

        filename = f"{prefix}{uuid.uuid4().hex}.jpg"
        file_path = os.path.join(tempfile.gettempdir(), filename)

        try:
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(image_data)
            return file_path
        except Exception as e:
            logger.warning(f"保存临时文件失败: {e}")
            return None

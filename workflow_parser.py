"""ComfyUI 工作流解析器"""

import copy
import glob
import json
import os
import random
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger


@dataclass
class WorkflowNodeMapping:
    """工作流节点映射"""

    positive_prompt_node: str | None = None
    negative_prompt_node: str | None = None
    latent_image_node: str | None = None
    sampler_node: str | None = None
    sampler_nodes: list[str] = field(default_factory=list)
    load_image_node: str | None = None
    output_node: str | None = None
    positive_prompt_field: str = "text"
    negative_prompt_field: str = "text"
    has_tensorrt: bool = False


@dataclass
class WorkflowInfo:
    """工作流信息"""

    name: str
    file_path: str
    workflow_data: dict[str, Any] = field(default_factory=dict)
    node_mapping: WorkflowNodeMapping = field(default_factory=WorkflowNodeMapping)
    description: str = ""


class WorkflowParser:
    """工作流解析器"""

    CLIP_TEXT_ENCODE_TYPES = [
        "CLIPTextEncode",
        "CLIPTextEncodeSDXL",
        "AdvancedCLIPTextEncode",
        "CLIPTextEncodeSD3",
        "BNK_CLIPTextEncodeAdvanced",
    ]
    LATENT_IMAGE_TYPES = ["EmptyLatentImage", "EmptySD3LatentImage", "EmptyLatentImagePresets"]
    SAMPLER_TYPES = [
        "KSampler",
        "KSamplerAdvanced",
        "SamplerCustom",
        "SamplerCustomAdvanced",
        "KSampler (Efficient)",
        "KSamplerSelect",
    ]
    LOAD_IMAGE_TYPES = ["LoadImage", "LoadImageMask", "LoadImageFromUrl"]
    OUTPUT_TYPES = ["PreviewImage", "SaveImage", "SaveImageWebsocket"]
    TRT_LOADER_TYPES = ["TensorRT Loader", "TensorRTLoader", "TensorRTLoaderSD3", "TensorRTLoaderFlux"]
    PASSTHROUGH_TYPES = ["Reroute", "RerouteTextForCLIPTextEncodeForSDXL"]

    def __init__(self, workflows_dir: str):
        self.workflows_dir = workflows_dir
        self.workflows: dict[int, WorkflowInfo] = {}
        self._load_all_workflows()

    def _load_all_workflows(self):
        """加载所有工作流"""
        if not os.path.exists(self.workflows_dir):
            os.makedirs(self.workflows_dir, exist_ok=True)
            return

        json_files = sorted(glob.glob(os.path.join(self.workflows_dir, "*.json")))
        for idx, file_path in enumerate(json_files, start=1):
            try:
                workflow_info = self._parse_workflow_file(file_path)
                if workflow_info:
                    self.workflows[idx] = workflow_info
                    logger.info(f"已加载工作流 [{idx}]: {workflow_info.name}")
            except Exception as e:
                logger.error(f"加载工作流失败 {file_path}: {e}")

    def _parse_workflow_file(self, file_path: str) -> WorkflowInfo | None:
        """解析工作流文件"""
        with open(file_path, encoding="utf-8") as f:
            workflow_data = json.load(f)

        name = os.path.splitext(os.path.basename(file_path))[0]
        node_mapping = self._analyze_workflow_nodes(workflow_data)
        description = self._extract_workflow_description(workflow_data, name)

        return WorkflowInfo(
            name=name,
            file_path=file_path,
            workflow_data=workflow_data,
            node_mapping=node_mapping,
            description=description,
        )

    def _analyze_workflow_nodes(self, workflow_data: dict[str, Any]) -> WorkflowNodeMapping:
        """分析工作流节点"""
        mapping = WorkflowNodeMapping()
        clip_text_nodes: list[tuple[str, dict]] = []

        for node_id, node_data in workflow_data.items():
            if not isinstance(node_data, dict):
                continue

            class_type = node_data.get("class_type", "")

            if class_type in self.TRT_LOADER_TYPES:
                mapping.has_tensorrt = True

            if class_type in self.CLIP_TEXT_ENCODE_TYPES:
                clip_text_nodes.append((node_id, node_data))
            elif class_type in self.LATENT_IMAGE_TYPES:
                mapping.latent_image_node = node_id
            elif class_type in self.SAMPLER_TYPES:
                mapping.sampler_nodes.append(node_id)
                if not mapping.sampler_node:
                    mapping.sampler_node = node_id
            elif class_type in self.LOAD_IMAGE_TYPES:
                mapping.load_image_node = node_id
            elif class_type in self.OUTPUT_TYPES:
                mapping.output_node = node_id

        self._classify_clip_nodes(clip_text_nodes, mapping, workflow_data)
        return mapping

    def _trace_link_source(
        self,
        link_ref: Any,
        workflow_data: dict[str, Any],
        target_types: list[str],
        max_depth: int = 10,
    ) -> str | None:
        """递归追踪链路来源"""
        if max_depth <= 0 or not isinstance(link_ref, list) or len(link_ref) < 1:
            return None

        source_id = str(link_ref[0])
        if source_id not in workflow_data:
            return None

        source_node = workflow_data[source_id]
        source_class = source_node.get("class_type", "")

        if source_class in target_types:
            return source_id

        if source_class in self.PASSTHROUGH_TYPES:
            inputs = source_node.get("inputs", {})
            for value in inputs.values():
                if isinstance(value, list) and len(value) >= 2:
                    return self._trace_link_source(value, workflow_data, target_types, max_depth - 1)

        return source_id

    def _classify_clip_nodes(
        self,
        clip_text_nodes: list[tuple[str, dict]],
        mapping: WorkflowNodeMapping,
        workflow_data: dict[str, Any],
    ):
        """分类 CLIP 节点为正向/负向"""
        if not clip_text_nodes:
            return

        if len(clip_text_nodes) == 1:
            mapping.positive_prompt_node = clip_text_nodes[0][0]
            return

        clip_node_ids = {node_id for node_id, _ in clip_text_nodes}

        if mapping.sampler_node and mapping.sampler_node in workflow_data:
            sampler_inputs = workflow_data[mapping.sampler_node].get("inputs", {})
            positive_ref = sampler_inputs.get("positive", [])
            negative_ref = sampler_inputs.get("negative", [])

            if isinstance(positive_ref, list) and len(positive_ref) >= 1:
                traced = self._trace_link_source(positive_ref, workflow_data, self.CLIP_TEXT_ENCODE_TYPES)
                if traced and traced in clip_node_ids:
                    mapping.positive_prompt_node = traced
                elif str(positive_ref[0]) in clip_node_ids:
                    mapping.positive_prompt_node = str(positive_ref[0])

            if isinstance(negative_ref, list) and len(negative_ref) >= 1:
                traced = self._trace_link_source(negative_ref, workflow_data, self.CLIP_TEXT_ENCODE_TYPES)
                if traced and traced in clip_node_ids:
                    mapping.negative_prompt_node = traced
                elif str(negative_ref[0]) in clip_node_ids:
                    mapping.negative_prompt_node = str(negative_ref[0])

        if mapping.positive_prompt_node and mapping.negative_prompt_node:
            return

        for node_id, node_data in clip_text_nodes:
            if node_id in (mapping.positive_prompt_node, mapping.negative_prompt_node):
                continue
            title = node_data.get("_meta", {}).get("title", "").lower()
            if not mapping.negative_prompt_node and ("negative" in title or "负" in title):
                mapping.negative_prompt_node = node_id
            elif not mapping.positive_prompt_node and ("positive" in title or "正" in title):
                mapping.positive_prompt_node = node_id

        negative_keywords = ["worst quality", "low quality", "bad anatomy", "ugly"]
        positive_keywords = ["masterpiece", "best quality", "beautiful", "detailed"]

        for node_id, node_data in clip_text_nodes:
            if node_id in (mapping.positive_prompt_node, mapping.negative_prompt_node):
                continue
            text = node_data.get("inputs", {}).get("text", "").lower()
            if not mapping.negative_prompt_node and any(kw in text for kw in negative_keywords):
                mapping.negative_prompt_node = node_id
            elif not mapping.positive_prompt_node and any(kw in text for kw in positive_keywords):
                mapping.positive_prompt_node = node_id

        for node_id, _ in clip_text_nodes:
            if node_id in (mapping.positive_prompt_node, mapping.negative_prompt_node):
                continue
            if not mapping.positive_prompt_node:
                mapping.positive_prompt_node = node_id
            elif not mapping.negative_prompt_node:
                mapping.negative_prompt_node = node_id

    def _extract_workflow_description(self, workflow_data: dict[str, Any], default_name: str) -> str:
        """提取工作流描述"""
        for node_data in workflow_data.values():
            if isinstance(node_data, dict) and "Checkpoint" in node_data.get("class_type", ""):
                ckpt_name = node_data.get("inputs", {}).get("ckpt_name", "")
                if ckpt_name:
                    return f"模型: {ckpt_name}"
        return default_name

    def get_workflow(self, index: int) -> WorkflowInfo | None:
        """获取指定工作流"""
        return self.workflows.get(index)

    def get_workflow_count(self) -> int:
        """获取工作流数量"""
        return len(self.workflows)

    def list_workflows(self) -> list[tuple[int, str, str]]:
        """列出所有工作流"""
        return [(idx, wf.name, wf.description) for idx, wf in sorted(self.workflows.items())]

    def reload_workflows(self):
        """重新加载工作流"""
        self.workflows.clear()
        self._load_all_workflows()

    def prepare_workflow(
        self,
        workflow_index: int,
        positive_prompt: str,
        negative_prompt: str,
        seed: int | None = None,
        input_image_filename: str | None = None,
    ) -> tuple[dict[str, Any] | None, int | None, str | None]:
        """准备工作流数据"""
        workflow_info = self.get_workflow(workflow_index)
        if not workflow_info:
            logger.error(f"工作流索引 {workflow_index} 不存在")
            return None, None, None

        workflow = copy.deepcopy(workflow_info.workflow_data)
        mapping = workflow_info.node_mapping

        if mapping.positive_prompt_node and mapping.positive_prompt_node in workflow:
            workflow[mapping.positive_prompt_node]["inputs"][mapping.positive_prompt_field] = positive_prompt

        if mapping.negative_prompt_node and mapping.negative_prompt_node in workflow:
            workflow[mapping.negative_prompt_node]["inputs"][mapping.negative_prompt_field] = negative_prompt

        actual_seed = seed if seed is not None else random.randint(1, 2**63 - 1)
        for sampler_id in mapping.sampler_nodes:
            if sampler_id in workflow:
                if "inputs" not in workflow[sampler_id]:
                    workflow[sampler_id]["inputs"] = {}
                workflow[sampler_id]["inputs"]["seed"] = actual_seed

        if input_image_filename and mapping.load_image_node:
            if mapping.load_image_node in workflow:
                workflow[mapping.load_image_node]["inputs"]["image"] = input_image_filename

        return workflow, actual_seed, negative_prompt

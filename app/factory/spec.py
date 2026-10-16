"""影视制作工厂 —— 节点（岗位）规格与注册表。

一个节点 = 影视制作中的一个岗位：它有自己的专业职责（``role``）、上游交付物
（``inputs`` 端口）、本岗位产出（``outputs`` 端口）与可调参数（``params``）。
执行器只做「这个岗位该做的事」，不关心图结构与调度——那是 engine.py 的职责。

端口类型即岗位之间的交付物契约，见 ``PortType`` 与各 payload 形状注释。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

# ------------------------------------------------------------------ #
# 端口类型（岗位之间的交付物）                                          #
# ------------------------------------------------------------------ #

PortType = Literal[
    "brief",  # 项目需求（制片方 brief）
    "text",  # 自由文本（阐述、大纲、备注）
    "outline",  # 故事大纲 {logline, acts[], themes[]}
    "screenplay",  # 剧本 {scenes[{action, dialogue[], location...}]}
    "characters",  # 角色表 list[{name, look, wardrobe, voice...}]
    "locations",  # 场景表 list[{name, mood, palette, props[]}]
    "shots",  # 分镜表 {shots[{size, movement, lens_mm, duration_s...}], total_s}
    "look",  # 视觉基调 {style_id, palette[], grain, vignette}
    "camera_plan",  # 摄影方案 list[{shot_index, lens, rig, fps, aspect}]
    "lighting_plan",  # 灯光方案 list[{shot_index, temp_k, ratio, direction}]
    "images",  # 图像集合 {items[{ref, url, kind, shot_index}]}
    "clips",  # 视频片段 {items[{ref, url, shot_index, duration_s}]}
    "audio",  # 音频轨 {items[{kind, ref, url, duration_s, gain}], total_s}
    "subtitles",  # 字幕轨 {cues[{start, end, text}]}
    "timeline",  # 渲染时间轴（剪辑师成片规格，前端播放器直接消费）
    "model",  # 生成模型配置 {task, provider, model, size, seed}
    "report",  # 审片/合规/核查报告 {score, verdict, issues[]}
    "plan",  # 制片计划 {phases[], budget[], crew[], risks[]}
    "any",  # 万能端口（输入/汇流节点用）
]

#: 端口类型的中文标签（UI 端口 tooltip 与连线校验提示复用）。
PORT_LABEL: dict[str, str] = {
    "brief": "项目需求",
    "text": "文本",
    "outline": "故事大纲",
    "screenplay": "剧本",
    "characters": "角色表",
    "locations": "场景表",
    "shots": "分镜表",
    "look": "视觉基调",
    "camera_plan": "摄影方案",
    "lighting_plan": "灯光方案",
    "images": "图像",
    "clips": "视频片段",
    "audio": "音频",
    "subtitles": "字幕轨",
    "timeline": "时间轴",
    "model": "模型配置",
    "report": "审看报告",
    "plan": "制片计划",
    "any": "任意",
}

#: 兼容矩阵：源端口类型的数据可以流向哪些目标端口（窄化兼容，避免到处是 any）。
PORT_COMPAT: dict[str, tuple[str, ...]] = {
    "brief": ("brief", "any"),
    "text": ("text", "any"),
    "outline": ("outline", "text", "any"),
    "screenplay": ("screenplay", "any"),
    "characters": ("characters", "any"),
    "locations": ("locations", "any"),
    "shots": ("shots", "timeline", "any"),
    "look": ("look", "any"),
    "camera_plan": ("camera_plan", "shots", "any"),
    "lighting_plan": ("lighting_plan", "any"),
    "images": ("images", "clips", "timeline", "any"),
    "clips": ("clips", "timeline", "any"),
    "audio": ("audio", "timeline", "any"),
    "subtitles": ("subtitles", "timeline", "any"),
    "model": ("model", "any"),
    "report": ("report", "any"),
    "plan": ("plan", "any"),
    "timeline": ("timeline", "any"),
    "any": ("any",),
}


def ports_compatible(source: str, target: str) -> bool:
    """源端口类型的数据能否接到目标端口。"""
    if source == target or target == "any" or source == "any":
        return True
    return target in PORT_COMPAT.get(source, ())


# ------------------------------------------------------------------ #
# 部门（岗位分组，左侧面板按此折叠）                                     #
# ------------------------------------------------------------------ #

DEPARTMENTS: list[tuple[str, str, str]] = [
    ("dev", "开发 · 制片", "选题策划、制片人、预算排期、版权"),
    ("writing", "编剧", "文学策划、编剧、剧本医生"),
    ("directing", "导演", "导演阐述、分镜、场记、选角"),
    ("art", "美术", "美术指导、角色/场景设计、服化道"),
    ("camera", "摄影 · 灯光", "摄影指导、灯光师、特殊设备"),
    ("gen", "生成车间", "模型配置、关键帧、视频生成、数字人演员"),
    ("sound", "声音", "配音、音效、配乐、混音"),
    ("post", "后期", "剪辑、调色、视效、字幕、包装、封面"),
    ("qc", "质检 · 发行", "审片、合规、事实核查、发行、复盘"),
    ("io", "输入 · 输出", "项目输入、素材、成片导出"),
]

DEPT_LABEL: dict[str, str] = {key: label for key, label, _ in DEPARTMENTS}


# ------------------------------------------------------------------ #
# 规格数据结构                                                          #
# ------------------------------------------------------------------ #


@dataclass
class Port:
    """一个数据端口。``name`` 是执行器读写用的键，``label`` 给 UI 显示。"""

    name: str
    label: str
    type: PortType
    required: bool = True
    #: 允许多条入线汇聚（混音收多条音轨、剪辑收多组素材）
    many: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "many": self.many,
        }


@dataclass
class Param:
    """节点参数。``kind`` 决定右侧检查器渲染哪种控件。"""

    name: str
    label: str
    kind: Literal["text", "textarea", "number", "select", "toggle", "range"] = "text"
    default: Any = ""
    options: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    hint: str = ""
    #: 仅在「其它参数==值」时显示，形如 ``task=i2v``；空串表示恒显示
    when: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "default": self.default,
            "options": list(self.options),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "unit": self.unit,
            "hint": self.hint,
            "when": self.when,
        }


class NodeError(RuntimeError):
    """岗位执行失败：如实呈现到节点上，不做静默降级。"""


@dataclass
class NodeSpec:
    """一个岗位节点的完整规格。"""

    node_type: str
    title: str
    dept: str
    role: str
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    run: Any = None  # Callable[[NodeContext], dict[str, Any]]
    icon: str = "🎬"
    #: 是否可脱离上游独立运行（输入类节点为 True）
    source: bool = False
    #: 需要真实服务商密钥/端点才出真货（离线走 sample 通道并如实标注）
    needs_service: bool = False
    tags: list[str] = field(default_factory=list)

    def param_defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def input(self, name: str) -> Port | None:
        return next((p for p in self.inputs if p.name == name), None)

    def output(self, name: str) -> Port | None:
        return next((p for p in self.outputs if p.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.node_type,
            "title": self.title,
            "dept": self.dept,
            "dept_label": DEPT_LABEL.get(self.dept, self.dept),
            "role": self.role,
            "icon": self.icon,
            "source": self.source,
            "needs_service": self.needs_service,
            "tags": list(self.tags),
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "params": [p.to_dict() for p in self.params],
        }


# ------------------------------------------------------------------ #
# 注册表                                                               #
# ------------------------------------------------------------------ #

_REGISTRY: dict[str, NodeSpec] = {}


def node(
    node_type: str,
    *,
    title: str,
    dept: str,
    role: str,
    inputs: Iterable[Port] = (),
    outputs: Iterable[Port] = (),
    params: Iterable[Param] = (),
    icon: str = "🎬",
    source: bool = False,
    needs_service: bool = False,
    tags: Iterable[str] = (),
) -> Callable[[Any], Any]:
    """装饰器：把岗位执行器登记进注册表。"""

    def decorator(func: Any) -> Any:
        if node_type in _REGISTRY:
            raise ValueError(f"重复的节点类型：{node_type}")
        _REGISTRY[node_type] = NodeSpec(
            node_type=node_type,
            title=title,
            dept=dept,
            role=role,
            inputs=list(inputs),
            outputs=list(outputs),
            params=list(params),
            run=func,
            icon=icon,
            source=source,
            needs_service=needs_service,
            tags=list(tags),
        )
        return func

    return decorator


def get_spec(node_type: str) -> NodeSpec | None:
    return _REGISTRY.get(node_type)


def all_specs() -> list[NodeSpec]:
    return list(_REGISTRY.values())


def palette() -> list[dict[str, Any]]:
    """给前端的节点面板：按部门分组，部门内按岗位名排序。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for spec in sorted(_REGISTRY.values(), key=lambda s: (s.dept, s.title)):
        groups.setdefault(spec.dept, []).append(spec.to_dict())
    return [
        {
            "dept": key,
            "label": label,
            "description": description,
            "nodes": groups.get(key, []),
        }
        for key, label, description in DEPARTMENTS
        if groups.get(key)
    ]

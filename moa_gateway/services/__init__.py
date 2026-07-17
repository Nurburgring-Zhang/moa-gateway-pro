"""MoA Gateway Pro — Service Layer

Round-2 架构升级:把所有 capability 模块封装为 Service 方法。
每个 Service:
  - 继承 ServiceBase
  - 暴露 methods (list of (name, description, input_schema, output_schema))
  - 内部调用 capability 模块
  - 统一输入校验、错误处理、telemetry

Agent Dispatcher 通过 registry 查找 Service 并 invoke method。
"""
from __future__ import annotations

from .base import ServiceBase, ServiceMethod, ServiceRegistry, ServiceResult

__all__ = [
    "ServiceBase",
    "ServiceMethod",
    "ServiceRegistry",
    "ServiceResult",
]

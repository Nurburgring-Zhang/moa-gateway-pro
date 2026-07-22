"""Service Base + Registry.

Service 是 capability 模块的封装,提供:
  - 强类型 input/output (schema 描述,虽然仍是 dict)
  - 统一错误处理
  - telemetry
  - 暴露给 AgentDispatcher 的标准 invoke 接口
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServiceResult:
    """Service 方法调用的统一返回结构。"""

    ok: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None  # "input_invalid" | "service_error" | "internal"
    latency_ms: float = 0.0
    service: str = ""
    method: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
            "service": self.service,
            "method": self.method,
            "meta": self.meta,
        }

    def raise_if_failed(self) -> ServiceResult:
        """如果 ok=False 抛 HTTPException (供 FastAPI endpoint 用)"""
        if self.ok:
            return self
        from fastapi import HTTPException

        if self.error_code == "input_invalid":
            raise HTTPException(422, self.error or "invalid input")
        if self.error_code == "not_found":
            raise HTTPException(404, self.error or "not found")
        if self.error_code == "rate_limited":
            raise HTTPException(429, self.error or "rate limited")
        if self.error_code in ("auth_required", "forbidden"):
            code = 401 if self.error_code == "auth_required" else 403
            raise HTTPException(code, self.error or "auth")
        if self.error_code == "conflict":
            raise HTTPException(409, self.error or "conflict")
        # 默认 400
        raise HTTPException(400, self.error or "service error")


@dataclass
class ServiceMethod:
    """Service 方法的 schema 描述."""

    name: str
    description: str
    func: Callable[..., Any]
    is_async: bool = False
    input_required: list[str] = field(default_factory=list)  # required input keys
    input_optional: list[str] = field(default_factory=list)  # optional input keys
    examples: list[dict] = field(default_factory=list)
    status: str = "implemented"  # "implemented" | "passthrough" | "todo"


class ServiceBase:
    """所有 Service 的基类."""

    name: str = "base"
    description: str = ""
    version: str = "1.0.0"

    def __init__(self):
        self._methods: dict[str, ServiceMethod] = {}
        self._register_methods()

    def _register_methods(self):
        """子类重写, 注册所有 method."""
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if isinstance(attr, ServiceMethod):
                self._methods[attr.name] = attr

    def list_methods(self) -> list[dict]:
        return [
            {
                "name": m.name,
                "description": m.description,
                "is_async": m.is_async,
                "input_required": m.input_required,
                "input_optional": m.input_optional,
                "status": m.status,
                "examples": m.examples[:2],  # 限 2 例
            }
            for m in self._methods.values()
        ]

    async def invoke(self, method_name: str, payload: dict[str, Any]) -> ServiceResult:
        """invoke a method by name with payload. Returns ServiceResult.

        这是 AgentDispatcher 调用的统一入口.
        """
        t0 = time.perf_counter()
        m = self._methods.get(method_name)
        if not m:
            return ServiceResult(
                ok=False,
                error=f"method '{method_name}' not found in service '{self.name}'",
                error_code="not_found",
                service=self.name,
                method=method_name,
            )
        # 校验必填字段
        if m.input_required:
            missing = [k for k in m.input_required if k not in payload]
            if missing:
                return ServiceResult(
                    ok=False,
                    error=f"missing required fields: {missing}",
                    error_code="input_invalid",
                    service=self.name,
                    method=method_name,
                )
        try:
            if m.is_async:
                data = await m.func(**payload)
            else:
                data = m.func(**payload)
            return ServiceResult(
                ok=True,
                data=data,
                latency_ms=(time.perf_counter() - t0) * 1000,
                service=self.name,
                method=method_name,
            )
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
            return ServiceResult(
                ok=False,
                error=f"{type(e).__name__}: {e}",
                error_code="input_invalid",
                latency_ms=(time.perf_counter() - t0) * 1000,
                service=self.name,
                method=method_name,
            )
        except Exception as e:
            return ServiceResult(
                ok=False,
                error=f"{type(e).__name__}: {e}",
                error_code="service_error",
                latency_ms=(time.perf_counter() - t0) * 1000,
                service=self.name,
                method=method_name,
                meta={"traceback": traceback.format_exc(limit=5)},
            )

    def get_method(self, name: str) -> ServiceMethod | None:
        return self._methods.get(name)


def service_method(
    name: str,
    description: str = "",
    is_async: bool = False,
    input_required: list[str] | None = None,
    input_optional: list[str] | None = None,
    examples: list[dict] | None = None,
    status: str = "implemented",
):
    """decorator: 标记一个方法为 service method."""

    def deco(func):
        m = ServiceMethod(
            name=name,
            description=description,
            func=func,
            is_async=is_async,
            input_required=input_required or [],
            input_optional=input_optional or [],
            examples=examples or [],
            status=status,
        )
        func._is_service_method = True
        func._method_meta = m
        return func

    return deco


class ServiceRegistry:
    """全局 Service 注册中心."""

    _instance: ServiceRegistry | None = None

    def __init__(self):
        self._services: dict[str, ServiceBase] = {}

    @classmethod
    def instance(cls) -> ServiceRegistry:
        if cls._instance is None:
            cls._instance = ServiceRegistry()
        return cls._instance

    def register(self, service: ServiceBase) -> None:
        self._services[service.name] = service

    def get(self, name: str) -> ServiceBase | None:
        return self._services.get(name)

    def list_services(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "methods": s.list_methods(),
            }
            for s in self._services.values()
        ]

    async def dispatch(
        self,
        service_name: str,
        method_name: str,
        payload: dict[str, Any] | None = None,
    ) -> ServiceResult:
        """dispatch a call to (service, method) with payload."""
        s = self.get(service_name)
        if not s:
            return ServiceResult(
                ok=False,
                error=f"service '{service_name}' not found",
                error_code="not_found",
            )
        return await s.invoke(method_name, payload or {})

"""Route modules for MoA Gateway Pro.

Each module exports a `router` (APIRouter instance) that is
included by the main app in server.py.
"""
from .admin import router as admin_router
from .admin_console import router as admin_console_router
from .agent import router as agent_router
from .assistant import router as assistant_router
from .audio import router as audio_router
from .auth import router as auth_router
from .benchmark import router as benchmark_router
from .capability import router as capability_router
from .chat import router as chat_router
from .compliance import router as compliance_router
from .embodied import router as embodied_router
from .health import router as health_router
from .image_edit import router as image_edit_router
from .mcp import router as mcp_router
from .metrics import router as metrics_router
from .moa import router as moa_router
from .models import router as models_router
from .observability import router as observability_router
from .optimizer import router as optimizer_router
from .orchestrator import router as orchestrator_router
from .tasks import router as tasks_router
from .threed import router as threed_router
from .video import router as video_router
from .vision import router as vision_router
from .webui import router as webui_router
from .workflow import router as workflow_router
from .world_model import router as world_model_router

__all__ = [
    "health_router",
    "metrics_router",
    "mcp_router",
    "models_router",
    "chat_router",
    "moa_router",
    "capability_router",
    "auth_router",
    "admin_router",
    "admin_console_router",
    "agent_router",
    "webui_router",
    "compliance_router",
    "workflow_router",
    "observability_router",
    "benchmark_router",
    "optimizer_router",
    "orchestrator_router",
    "vision_router",
    "audio_router",
    "image_edit_router",
    "video_router",
    "threed_router",
    "embodied_router",
    "world_model_router",
    "assistant_router",
    "tasks_router",
]

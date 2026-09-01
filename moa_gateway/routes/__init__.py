"""Route modules for MoA Gateway Pro.

Each module exports a `router` (APIRouter instance) that is
included by the main app in server.py.
"""
from .a2a import router as a2a_router
from .admin import router as admin_router
from .admin_console import router as admin_console_router
from .agent import router as agent_router
from .assistant import router as assistant_router
from .audio import router as audio_router
from .auth import router as auth_router
from .benchmark import router as benchmark_router
from .capability import router as capability_router
from .channels import router as channels_router
from .chat import router as chat_router
from .compliance import router as compliance_router
from .compression import router as compression_router
from .dialogue import router as dialogue_router
from .efficiency import router as efficiency_router
from .embodied import router as embodied_router
from .free_tiers import router as free_tiers_router
from .health import router as health_router
from .image_edit import router as image_edit_router
from .mcp import router as mcp_router
from .memory import router as memory_router
from .metrics import router as metrics_router
from .moa import router as moa_router
from .models import router as models_router
from .multimodal import router as multimodal_router
from .music import router as music_router
from .observability import router as observability_router
from .optimizer import router as optimizer_router
from .orchestrator import router as orchestrator_router
from .quota import router as quota_router
from .routing_strategies import router as routing_strategies_router
from .skillhub import router as skillhub_router
from .subagent import router as subagent_router
from .task_pipeline import router as task_pipeline_router
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
    "multimodal_router",
    "task_pipeline_router",
    "chat_router",
    "moa_router",
    "capability_router",
    "auth_router",
    "admin_router",
    "admin_console_router",
    "agent_router",
    "webui_router",
    "compliance_router",
    "dialogue_router",
    "workflow_router",
    "observability_router",
    "benchmark_router",
    "optimizer_router",
    "vision_router",
    "audio_router",
    "image_edit_router",
    "video_router",
    "music_router",
    "threed_router",
    "embodied_router",
    "world_model_router",
    "assistant_router",
    "tasks_router",
    # v4.1.0 integration routers (OmniRoute / OpenClacky / MemoraX Code)
    "a2a_router",
    "compression_router",
    "free_tiers_router",
    "channels_router",
    "efficiency_router",
    "memory_router",
    "quota_router",
    "routing_strategies_router",
    "skillhub_router",
    "subagent_router",
    "orchestrator_router",
]

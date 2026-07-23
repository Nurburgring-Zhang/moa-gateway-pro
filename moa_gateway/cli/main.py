"""MoA Gateway Pro CLI — argparse-based entry point (no click/typer dependency)."""
from __future__ import annotations

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="moa",
        description="MoA Gateway Pro — Commercial-grade Multi-Model AI Gateway",
    )
    subparsers = parser.add_subparsers(dest="command")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start gateway server")
    p_serve.add_argument("--port", type=int, default=8910)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--workers", type=int, default=1)

    # chat
    p_chat = subparsers.add_parser("chat", help="Chat with models")
    p_chat.add_argument("model", nargs="?", default="auto", help="Model name")
    p_chat.add_argument("-m", "--message", help="Single message (non-interactive)")
    p_chat.add_argument("--stream", action="store_true", help="Stream response")

    # run-moa
    p_moa = subparsers.add_parser("run-moa", help="Run MOA orchestration")
    p_moa.add_argument("-m", "--message", required=True, help="Prompt message")
    p_moa.add_argument("--preset", default="balanced", help="MOA preset")

    # models
    p_models = subparsers.add_parser("models", help="Model management")
    p_models_sub = p_models.add_subparsers(dest="models_command")
    p_models_sub.add_parser("list", help="List available models")
    p_add = p_models_sub.add_parser("add", help="Add a model")
    p_add.add_argument("name", help="Endpoint ID")
    p_add.add_argument("--provider", required=True)
    p_add.add_argument("--model", required=True)
    p_add.add_argument("--base-url", default="")
    p_add.add_argument("--api-key", default="")
    p_models_sub.add_parser("remove", help="Remove a model").add_argument("name")

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover free model APIs")
    p_discover.add_argument("--list", action="store_true", help="List known platforms")
    p_discover.add_argument("--run", action="store_true", help="Run discovery now")

    # prompts
    p_prompts = subparsers.add_parser("prompts", help="Prompt template management")
    p_prompts_sub = p_prompts.add_subparsers(dest="prompts_command")
    p_prompts_sub.add_parser("list", help="List prompt templates")
    p_show = p_prompts_sub.add_parser("show", help="Show a prompt template")
    p_show.add_argument("id", help="Template ID")
    p_prompts_sub.add_parser("categories", help="List prompt categories")

    # mcp
    p_mcp = subparsers.add_parser("mcp", help="MCP tool management")
    p_mcp_sub = p_mcp.add_subparsers(dest="mcp_command")
    p_mcp_sub.add_parser("list", help="List MCP servers")
    p_mcp_sub.add_parser("tools", help="List all MCP tools")

    # config
    p_config = subparsers.add_parser("config", help="Configuration")
    p_config_sub = p_config.add_subparsers(dest="config_command")
    p_config_sub.add_parser("show", help="Show current configuration")
    p_config_sub.add_parser("init", help="Initialize configuration")

    # params
    p_params = subparsers.add_parser("params", help="Parameter templates")
    p_params_sub = p_params.add_subparsers(dest="params_command")
    p_params_sub.add_parser("list", help="List parameter templates")
    p_show2 = p_params_sub.add_parser("show", help="Show a parameter template")
    p_show2.add_argument("task_type", help="Task type")


    # workflow
    p_workflow = subparsers.add_parser("workflow", help="Workflow management")
    p_workflow_sub = p_workflow.add_subparsers(dest="workflow_command")
    p_workflow_sub.add_parser("list", help="List available workflows")
    p_wf_run = p_workflow_sub.add_parser("run", help="Execute a workflow")
    p_wf_run.add_argument("name", help="Workflow name")
    p_wf_run.add_argument("-c", "--context", help="JSON context string")
    p_wf_show = p_workflow_sub.add_parser("show", help="Show workflow details")
    p_wf_show.add_argument("name", help="Workflow name")

    # ask (AI command suggestion)
    p_ask = subparsers.add_parser("ask", help="AI command suggestion")
    p_ask.add_argument("query", nargs="?", help="Natural language query")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    _dispatch(args)


def _dispatch(args):
    """Route command to handler."""
    cmd = args.command

    if cmd == "serve":
        _cmd_serve(args)
    elif cmd == "chat":
        _cmd_chat(args)
    elif cmd == "run-moa":
        _cmd_run_moa(args)
    elif cmd == "models":
        _cmd_models(args)
    elif cmd == "discover":
        _cmd_discover(args)
    elif cmd == "prompts":
        _cmd_prompts(args)
    elif cmd == "mcp":
        _cmd_mcp(args)
    elif cmd == "config":
        _cmd_config(args)
    elif cmd == "params":
        _cmd_params(args)
    elif cmd == "workflow":
        _cmd_workflow(args)
    elif cmd == "ask":
        _cmd_ask(args)
    else:
        print(f"Unknown command: {cmd}")


def _get_base_url():
    """Get gateway base URL from env or config."""
    return os.environ.get("MOA_GATEWAY_URL", "http://127.0.0.1:8910")


def _get_api_key():
    """Get API key from env."""
    return os.environ.get("MOA_API_KEY", "")


def _get_auth_headers():
    """Get auth headers."""
    key = _get_api_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


# === Command handlers ===


def _cmd_serve(args):
    """Start gateway server."""
    import subprocess

    python = sys.executable
    cmd = [
        python, "-m", "uvicorn", "moa_gateway.server:app",
        "--host", args.host, "--port", str(args.port),
        "--workers", str(args.workers),
    ]
    subprocess.run(cmd)


def _cmd_chat(args):
    """Chat with models."""
    if args.message:
        _send_chat(args.model, args.message, args.stream)
    else:
        from moa_gateway.cli.chat_repl import ChatREPL

        repl = ChatREPL(
            model=args.model,
            base_url=_get_base_url(),
            api_key=_get_api_key(),
        )
        repl.run()


def _send_chat(model, message, stream=False):
    """Send a single chat message."""
    import httpx

    try:
        r = httpx.post(
            f"{_get_base_url()}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": message}],
                "stream": stream,
            },
            headers=_get_auth_headers(),
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            print(content)
        else:
            print(f"Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Connection error: {e}")


def _cmd_run_moa(args):
    """Run MOA orchestration.

    FIX: /v1/moa/execute expects ChatCompletionRequest (with *messages*),
    not {prompt: ...}.  MoAResult.to_dict() returns *final_content*.
    """
    import httpx

    try:
        r = httpx.post(
            f"{_get_base_url()}/v1/moa/execute",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": args.message}],
                "preset": args.preset,
            },
            headers=_get_auth_headers(),
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            content = (
                data.get("final_content")
                or data.get("aggregated_content")
                or str(data)[:500]
            )
            print(content)
            # Print extra metadata if available
            refs = data.get("references", [])
            if refs:
                print(f"\n--- MOA metadata ---")
                print(f"  preset:     {data.get('preset', '?')}")
                print(f"  strategy:   {data.get('strategy', '?')}")
                print(f"  references: {len(refs)} models")
                print(f"  consensus:  {data.get('consensus_score', '?')}")
                print(f"  cost:       ${data.get('total_cost', 0):.6f}")
                print(f"  latency:    {data.get('total_latency_ms', 0):.0f}ms")
        else:
            print(f"Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Connection error: {e}")


def _cmd_models(args):
    """Model management.

    FIX: /v1/models returns *owned_by* (not provider).
    POST /api/endpoints requires admin auth (JWT), not just API key.
    """
    import httpx

    if args.models_command == "list" or not args.models_command:
        try:
            r = httpx.get(
                f"{_get_base_url()}/v1/models",
                headers=_get_auth_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                models = data.get("data", [])
                print(f"\nAvailable models ({len(models)}):")
                print(f"  {'ID':<30s}  {'Provider':<20s}  Description")
                print(f"  {'-'*30}  {'-'*20}  {'-'*40}")
                for m in models:
                    mid = m.get("id", "?")
                    owner = m.get("owned_by", "?")
                    desc = m.get("description", "")[:60]
                    print(f"  {mid:<30s}  {owner:<20s}  {desc}")
            else:
                print(f"Error {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Connection error: {e}")
    elif args.models_command == "add":
        try:
            r = httpx.post(
                f"{_get_base_url()}/api/endpoints",
                json={
                    "endpoint_id": args.name,
                    "provider": args.provider,
                    "model": args.model,
                    "api_base": args.base_url,
                    "api_key_plain": args.api_key,
                    "enabled": True,
                },
                headers=_get_auth_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                print(f"Model '{args.name}' added successfully")
            else:
                print(f"Error {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Connection error: {e}")
    elif args.models_command == "remove":
        try:
            r = httpx.delete(
                f"{_get_base_url()}/api/endpoints/{args.name}",
                headers=_get_auth_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                print(f"Model '{args.name}' removed")
            else:
                print(f"Error {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Connection error: {e}")


def _cmd_discover(args):
    """Discover free models."""
    if args.list:
        from moa_gateway.discovery.free_model_catalog import get_all_platforms

        platforms = get_all_platforms()
        print(f"\nKnown free model platforms ({len(platforms)}):")
        print(f"{'Platform':<20} {'Base URL':<50} {'Auth':<15} {'Free Type'}")
        print("-" * 100)
        for p in platforms:
            print(
                f"{p.platform_id:<20} {p.base_url:<50} "
                f"{p.auth_type:<15} {p.free_tier_type}"
            )
    elif args.run:
        import asyncio

        from moa_gateway.discovery.discovery_engine import FreeModelDiscoveryEngine

        engine = FreeModelDiscoveryEngine()
        print("Discovering free models...")
        models = asyncio.run(engine.discover_all())
        platform_ids = set(m.platform_id for m in models)
        print(
            f"\nDiscovered {len(models)} free models "
            f"from {len(platform_ids)} platforms"
        )
        for m in models[:20]:
            print(f"  [{m.platform_id}] {m.model_id} (tier={m.inferred_tier})")
        if len(models) > 20:
            print(f"  ... and {len(models) - 20} more")
    else:
        print("Use --list to see platforms or --run to start discovery")


def _cmd_prompts(args):
    """Prompt template management.

    FIX: list_templates() returns list[dict] with name/source/category keys.
    FIX: list_categories() does not exist — derive from list_templates().
    """
    if args.prompts_command == "list" or not args.prompts_command:
        try:
            from moa_gateway.prompts import list_templates

            templates = list_templates()
            print(f"\nPrompt templates ({len(templates)}):")
            print(f"  {'Name':<30s}  {'Category':<15s}  {'Source':<10s}  Size")
            print(f"  {'-'*30}  {'-'*15}  {'-'*10}  {'-'*8}")
            for t in templates:
                name = t.get("name", "?")
                cat = t.get("category") or "-"
                src = t.get("source", "?")
                size = t.get("size", 0)
                print(f"  {name:<30s}  {cat:<15s}  {src:<10s}  {size}")
        except Exception as e:
            print(f"Error: {e}")
    elif args.prompts_command == "show":
        try:
            from moa_gateway.prompts import get_prompt

            content = get_prompt(args.id)
            print(content)
        except Exception as e:
            print(f"Error: {e}")
    elif args.prompts_command == "categories":
        try:
            from moa_gateway.prompts import list_templates

            templates = list_templates()
            cats = {}
            for t in templates:
                cat = t.get("category") or "(uncategorized)"
                cats.setdefault(cat, []).append(t.get("name", "?"))
            print(f"\nPrompt categories ({len(cats)}):")
            for cat, names in sorted(cats.items()):
                print(f"  {cat} ({len(names)} templates):")
                for n in names:
                    print(f"    - {n}")
        except Exception as e:
            print(f"Error: {e}")


def _cmd_mcp(args):
    """MCP management.

    FIX: /v1/mcp/tools returns {tools: [...], total: N}, not flat list.
    FIX: /v1/mcp/servers returns {servers: [...], total: N}.
    """
    import httpx

    if args.mcp_command == "list" or not args.mcp_command:
        try:
            r = httpx.get(
                f"{_get_base_url()}/v1/mcp/servers",
                headers=_get_auth_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                servers = data.get("servers", [])
                total = data.get("total", len(servers))
                print(f"\nMCP servers ({total}):")
                for s in servers:
                    name = s.get("name", s.get("url", "?"))
                    print(f"  {name}")
                if not servers:
                    print("  (no external MCP servers registered)")
            else:
                print(f"Error {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Error: {e}")
    elif args.mcp_command == "tools":
        try:
            r = httpx.get(
                f"{_get_base_url()}/v1/mcp/tools",
                headers=_get_auth_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                tools = data.get("tools", [])
                total = data.get("total", len(tools))
                print(f"\nMCP tools ({total}):")
                print(f"  {'Name':<30s}  Description")
                print(f"  {'-'*30}  {'-'*60}")
                for t in tools:
                    name = t.get("name", "?")
                    desc = (t.get("description") or "")[:60]
                    print(f"  {name:<30s}  {desc}")
                if not tools:
                    print("  (no tools available)")
            else:
                print(f"Error {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Error: {e}")


def _cmd_config(args):
    """Configuration."""
    if args.config_command == "show":
        try:
            from moa_gateway.config import get_settings

            s = get_settings()
            print(f"\n=== MoA Gateway Pro Configuration ===")
            print(f"Server:     {s.server.host}:{s.server.port}")
            print(f"Workers:    {s.server.workers}")
            print(f"Log level:  {s.server.log_level}")
            print(f"Models:     {len(s.models)} endpoints configured")
            print(f"  enabled:  {sum(1 for m in s.models if m.enabled)}")
            print(f"Discovery:  enabled={s.discovery.enabled}")
            print(f"Cache:      enabled={s.cache.enabled}")
            print(f"RateLimit:  enabled={s.ratelimit.enabled}, "
                  f"rpm={s.ratelimit.per_key_rpm}")
            print(f"MoA:        enabled={s.moa.enabled}, "
                  f"default={s.moa.default_preset}")
            print(f"  presets:  {list(s.moa.presets.keys())}")
        except Exception as e:
            print(f"Error: {e}")
    elif args.config_command == "init":
        print("Configuration initialization not yet implemented.")
        print("Use 'start.py init-data' to initialize the data directory.")
    else:
        print("Use 'show' or 'init'")


def _cmd_params(args):
    """Parameter templates.

    ParamTemplateManager.list_all() returns:
        {templates: {task_type: {temperature, top_p, max_tokens, description}},
         model_overrides: {provider: {top_k, ...}},
         total: N}
    """
    try:
        from moa_gateway.param_templates.manager import ParamTemplateManager  # noqa: F401
    except ImportError:
        print("Parameter templates module not available.")
        print("This feature requires moa_gateway.param_templates package.")
        return

    if args.params_command == "list" or not args.params_command:
        try:
            mgr = ParamTemplateManager()
            data = mgr.list_all()
            templates = data.get("templates", {})
            overrides = data.get("model_overrides", {})
            total = data.get("total", len(templates))
            print(f"\nParameter templates ({total}):")
            print(f"  {'Task Type':<20s}  {'Temp':<6s}  {'Top P':<6s}  {'Max Tok':<8s}  Description")
            print(f"  {'-'*20}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*40}")
            for name in sorted(templates.keys()):
                p = templates[name]
                if not isinstance(p, dict):
                    continue
                temp = p.get("temperature", "?")
                top_p = p.get("top_p", "?")
                max_tok = p.get("max_tokens", "?")
                desc = p.get("description", "")[:40]
                print(f"  {name:<20s}  {temp:<6}  {top_p:<6}  {max_tok:<8}  {desc}")
            if overrides:
                print(f"\nModel overrides ({len(overrides)}):")
                for prov, ov in sorted(overrides.items()):
                    print(f"  {prov:<20s}  {ov}")
        except Exception as e:
            print(f"Error: {e}")
    elif args.params_command == "show":
        try:
            mgr = ParamTemplateManager()
            template = mgr.get_template(args.task_type)
            if template:
                print(f"\n{args.task_type}:")
                for k, v in sorted(template.items()):
                    print(f"  {k:<20s}  {v}")
            else:
                print(f"No template found for task type: {args.task_type}")
                available = mgr.list_templates()
                if available:
                    print(f"Available: {', '.join(available)}")
        except Exception as e:
            print(f"Error: {e}")



def _cmd_workflow(args):
    """Workflow management."""
    import asyncio

    if args.workflow_command == "list" or not args.workflow_command:
        try:
            from moa_gateway.workflows.workflow_loader import WorkflowLoader

            loader = WorkflowLoader()
            workflows = loader.list_workflows()
            print(f"\nAvailable workflows ({len(workflows)}):")
            print(f"  {'Name':<30s}  {'Version':<8s}  Steps  Description")
            print(f"  {'-'*30}  {'-'*8}  {'-'*5}  {'-'*40}")
            for w in workflows:
                print(f"  {w['name']:<30s}  {w['version']:<8s}  {w['steps']:<5s}  {w['description'][:40]}")
            if not workflows:
                print("  (no workflows found)")
        except Exception as e:
            print(f"Error: {e}")
    elif args.workflow_command == "run":
        import json

        try:
            from moa_gateway.workflows.workflow_loader import WorkflowLoader

            loader = WorkflowLoader()
            wf = loader.get_workflow(args.name)
            if wf is None:
                print(f"Workflow '{args.name}' not found")
                return
            ctx = {}
            if args.context:
                try:
                    ctx = json.loads(args.context)
                except json.JSONDecodeError:
                    ctx = {"user_input": args.context}
            print(f"Executing workflow '{args.name}'...")
            result = asyncio.run(wf.execute(ctx))
            if result.get("success"):
                print("\n--- Workflow Results ---")
                for step in result.get("steps", []):
                    print(f"  [{step['step_id']}] {'OK' if step['success'] else 'FAIL'}")
                    if step.get("output"):
                        print(f"    {step['output'][:200]}")
            else:
                print(f"Workflow failed: {result.get('error', 'unknown')}")
        except Exception as e:
            print(f"Error: {e}")
    elif args.workflow_command == "show":
        try:
            from moa_gateway.workflows.workflow_loader import WorkflowLoader

            loader = WorkflowLoader()
            wf = loader.get_workflow(args.name)
            if wf is None:
                print(f"Workflow '{args.name}' not found")
                return
            print(f"\nWorkflow: {wf.name}")
            print(f"Description: {wf.description}")
            print(f"Version: {wf.version}")
            print(f"Steps ({len(wf.steps)}):")
            for s in wf.steps:
                deps = ", ".join(s.depends_on) if s.depends_on else "(none)"
                print(f"  - {s.id} (type={s.type}, depends_on=[{deps}])")
        except Exception as e:
            print(f"Error: {e}")


def _cmd_ask(args):
    """AI command suggestion."""
    import asyncio

    from moa_gateway.cli.ai_suggest import AICommandSuggester

    if not args.query:
        print("Usage: moa ask <natural language description>")
        print("Example: moa ask 'list all available models'")
        return

    suggester = AICommandSuggester()
    suggestions = asyncio.run(suggester.suggest(args.query))

    if not suggestions:
        print("No matching commands found.")
        return

    print(f"\nSuggested commands for: '{args.query}'\n")
    for i, s in enumerate(suggestions, 1):
        print(f"  {i}. {s['full_command']}")
        print(f"     Confidence: {s['confidence']:.0%} - {s['explanation']}")
    print()


if __name__ == "__main__":
    main()

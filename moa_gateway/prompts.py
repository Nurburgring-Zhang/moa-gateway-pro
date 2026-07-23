"""moa_gateway.prompts — Prompt 模板管理器

允许用户:
- 自定义 aggregator / critic / compose role 的 prompt
- 热更新(无需重启)
- 占位符替换 {user_query} {reference_responses} 等

优先级:文件系统 prompts/{name}.md > moa_gateway/prompts/{name}.md > 内置默认
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Template as Jinja2Template

logger = logging.getLogger(__name__)

# 默认搜索路径
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = Path(__file__).resolve().parent / "prompts"
USER_DIR = Path.home() / ".moa-gateway" / "prompts"


def _search_dirs() -> list[Path]:
    """用户目录 > 项目默认目录(避免重复,用户优先)"""
    out = []
    if USER_DIR.exists():
        out.append(USER_DIR)
    if DEFAULT_DIR.exists():
        out.append(DEFAULT_DIR)
    return out


# 内置 fallback(当文件找不到时用)
BUILTIN: dict[str, str] = {
    "aggregator": """你是一个多模型答案的聚合器(aggregator)。综合多个独立模型的回答,给出一份最优的最终答案。

原则:
1. 分析每个参考回答的优劣,识别共识与分歧
2. 保留可验证的内容,剔除错误论断
3. 互补融合不同回答的长处
4. 对分歧做出明确裁决
5. 结构化输出(标题/列表/代码块)
6. 不编造参考里没有的事实

输出: 先简述综合判断,再给最终答案(可用 markdown)。""",
    "critic": """你是互审员。审查聚合后的答案,找问题并提建议。

维度:
1. 事实性 — 有无错误
2. 完整性 — 是否遗漏关键点
3. 逻辑性 — 推理连贯性
4. 实用性 — 对用户有无帮助
5. 风险性 — 误导、安全/法律/财务风险

输出 JSON: {"issues": [...], "suggestions": [...], "verdict": "pass|needs_revision"}""",
    "compose_feasibility": """你从可行性角度分析。关注:技术能不能实现、实现成本、依赖风险、技术债务、关键障碍。""",
    "compose_performance": """你从性能角度分析。关注:时间复杂度、空间复杂度、吞吐量、延迟、可扩展性瓶颈。""",
    "compose_security": """你从安全性角度分析。关注:认证授权、注入风险、数据泄露、攻击面、加密、合规。""",
    "compose_ux": """你从用户体验角度分析。关注:易用性、错误处理、文档、可访问性、上手成本。""",
    "compose_architecture": """你从架构角度分析。关注:模块划分、接口设计、依赖关系、演进路径。""",
    "compose_business": """你从业务角度分析。关注:ROI、商业价值、风险、市场、用户付费意愿。""",
    "compose_general": """你从通用角度分析这个问题。基于事实给出有深度的回答,关注逻辑、关键点、可执行性。""",
    "judge_reflection": """反思上一轮自己的回答:
1. 准确性?
2. 是否遗漏?
3. 推理连贯?
4. 需要补充细节?

如果很好,只输出: VERDICT: PASS
否则给出修订后的完整答案。""",
    "chain_research": """你是研究员。基于用户问题,搜索相关信息,列出关键事实、数据、来源。""",
    "chain_analyze": """你是分析师。基于研究结果,深度分析问题:why、how、对比、权衡。""",
    "chain_summarize": """你是综述员。基于以上所有内容,给出综合答案。要求结构化、可执行、清晰。""",
}


def get_prompt(name: str, **kwargs) -> str:
    '''Get prompt template with placeholder substitution.

    Backward compatible: old templates use {placeholder} + .format();
    new templates use {{ placeholder }} + Jinja2.
    '''
    raw = _load_template(name)
    if raw.startswith('---'):
        parts = raw.split('---', 2)
        body = parts[2].strip() if len(parts) >= 3 else raw
        if not kwargs:
            return body
        try:
            return Jinja2Template(body).render(**kwargs)
        except Exception:
            return body
    if not kwargs:
        return raw
    try:
        return raw.format(**{k: v for k, v in kwargs.items() if f'{{{k}}}' in raw})
    except (KeyError, IndexError):
        return raw

def _load_template(name: str) -> str:
    '''Load template: USER_DIR (with subdirs) > DEFAULT_DIR (with subdirs) > builtin.'''
    fname = f'{name}.md'
    # 1. Flat files (backward compatible)
    for d in _search_dirs():
        p = d / fname
        if p.exists():
            try:
                return p.read_text(encoding='utf-8').strip()
            except Exception as e:
                logger.warning('read %s failed: %s', p, e)
    # 2. Subdirectory files (new templates)
    for d in _search_dirs():
        if not d.exists():
            continue
        for subdir in sorted(d.iterdir()):
            if not subdir.is_dir():
                continue
            p = subdir / fname
            if p.exists():
                try:
                    return p.read_text(encoding='utf-8').strip()
                except Exception as e:
                    logger.warning('read %s failed: %s', p, e)
    return BUILTIN.get(name, f'# {name}\n\n请基于上下文回答用户问题。')

def list_templates() -> list[dict[str, Any]]:
    '''List all templates (flat + subdirectory + builtin).'''
    seen = set()
    out = []
    # 1. Flat files (backward compatible)
    for src, d in [('user', USER_DIR), ('default', DEFAULT_DIR)]:
        if not d.exists():
            continue
        for p in sorted(d.glob('*.md')):
            if p.name == 'README.md':
                continue
            name = p.stem
            if name in seen:
                continue
            seen.add(name)
            out.append({
                'name': name,
                'source': src,
                'path': str(p),
                'size': p.stat().st_size,
                'read_only': (src == 'default'),
                'category': None,
            })
    # 2. Subdirectory files (new templates)
    for src, d in [('user', USER_DIR), ('default', DEFAULT_DIR)]:
        if not d.exists():
            continue
        for subdir in sorted(d.iterdir()):
            if not subdir.is_dir():
                continue
            for p in sorted(subdir.glob('*.md')):
                name = p.stem
                if name in seen:
                    continue
                seen.add(name)
                out.append({
                    'name': name,
                    'source': src,
                    'path': str(p),
                    'size': p.stat().st_size,
                    'read_only': (src == 'default'),
                    'category': subdir.name,
                })
    # 3. Builtin templates
    for name in BUILTIN:
        if name not in seen:
            out.append({
                'name': name,
                'source': 'builtin',
                'path': None,
                'size': len(BUILTIN[name]),
                'read_only': True,
                'category': None,
            })
    return out

def save_template(name: str, content: str) -> str:
    """保存用户自定义模板(写到 USER_DIR)"""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    p = USER_DIR / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return str(p)


def delete_template(name: str) -> bool:
    """删除用户自定义模板"""
    p = USER_DIR / f"{name}.md"
    if p.exists():
        p.unlink()
        return True
    return False


def render(name: str, **kwargs) -> str:
    """get_prompt 的别名"""
    return get_prompt(name, **kwargs)


# 占位符辅助
PLACEHOLDERS = {
    "user_query": "用户原始问题",
    "reference_responses": "多模型参考答案(自动拼接)",
    "original_messages": "原始对话历史(JSON)",
    "aspects": "其他视角概要(compose 模式)",
    "current_draft": "当前草稿(critic 修订时用)",
    "role": "当前 role 名",
    "query": "用户问题(同 user_query)",
}


# ============================================================
# Template categories & Jinja2 rendering (v2 extension)
# ============================================================


def list_categories() -> list[str]:
    '''Return all template categories (subdirectory names).'''
    cats: set[str] = set()
    for d in [USER_DIR, DEFAULT_DIR]:
        if d.exists():
            for subdir in d.iterdir():
                if subdir.is_dir() and any(subdir.glob('*.md')):
                    cats.add(subdir.name)
    return sorted(cats)


def get_by_category(category: str) -> list[dict[str, Any]]:
    '''List templates in a specific category.'''
    out: list[dict[str, Any]] = []
    for d in [USER_DIR, DEFAULT_DIR]:
        cat_dir = d / category
        if not (cat_dir.exists() and cat_dir.is_dir()):
            continue
        for p in sorted(cat_dir.glob('*.md')):
            entry: dict[str, Any] = {
                'name': p.stem,
                'category': category,
                'path': str(p),
                'size': p.stat().st_size,
                'description': '',
                'variables': {},
            }
            try:
                meta = load_template_with_metadata(p.stem)
                if meta.get('has_frontmatter'):
                    m = meta['metadata']
                    entry['description'] = m.get('description', '')
                    entry['variables'] = m.get('variables', {})
                    for fld in ('temperature', 'top_p', 'max_tokens'):
                        if fld in m:
                            entry[fld] = m[fld]
            except Exception as e:
                logger.warning('load metadata for %s failed: %s', p.stem, e)
            out.append(entry)
    return out


def load_template_with_metadata(name: str) -> dict[str, Any]:
    '''Load template, parse YAML frontmatter metadata.'''
    raw = _load_template(name)
    result: dict[str, Any] = {
        'name': name,
        'has_frontmatter': False,
        'metadata': {},
        'body': raw,
    }
    if not raw.startswith('---'):
        return result
    parts = raw.split('---', 2)
    if len(parts) < 3:
        return result
    frontmatter_text = parts[1].strip()
    body = parts[2].strip()
    try:
        metadata = yaml.safe_load(frontmatter_text) or {}
    except Exception as e:
        logger.warning('parse frontmatter for %s failed: %s', name, e)
        metadata = {}
    result = {
        'name': name,
        'has_frontmatter': True,
        'metadata': metadata,
        'body': body,
    }
    for field in ('id', 'category', 'temperature', 'top_p', 'max_tokens', 'description', 'variables'):
        if field in metadata:
            result[field] = metadata[field]
    return result


def render_prompt(name: str, variables: dict | None = None) -> dict[str, Any]:
    '''Jinja2 render template, return {system, user, params}.'''
    if variables is None:
        variables = {}
    meta = load_template_with_metadata(name)
    params: dict[str, Any] = {}
    system = ''
    user = ''
    if meta['has_frontmatter']:
        m = meta['metadata']
        for field in ('temperature', 'top_p', 'max_tokens'):
            if field in m:
                params[field] = m[field]
        try:
            body_yaml = yaml.safe_load(meta['body']) or {}
            if isinstance(body_yaml, dict):
                system_text = str(body_yaml.get('system', ''))
                user_text = str(body_yaml.get('user', ''))
            else:
                system_text = ''
                user_text = meta['body']
        except Exception as e:
            logger.warning('parse body YAML for %s failed: %s', name, e)
            system_text = ''
            user_text = meta['body']
        if system_text:
            try:
                system = Jinja2Template(system_text).render(**variables)
            except Exception as e:
                logger.warning('render system for %s failed: %s', name, e)
                system = system_text
        if user_text:
            try:
                user = Jinja2Template(user_text).render(**variables)
            except Exception as e:
                logger.warning('render user for %s failed: %s', name, e)
                user = user_text
    else:
        user = meta['body']
        if variables:
            try:
                user = Jinja2Template(user).render(**variables)
            except Exception:
                pass
    return {'system': system, 'user': user, 'params': params}


# Parameter template manager.
# Three-layer override: task template -> provider override -> request params.
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATES_PATH = Path(__file__).resolve().parent / 'param_templates.yaml'
_PARAM_KEYS = frozenset({'temperature', 'top_p', 'max_tokens', 'top_k', 'frequency_penalty', 'presence_penalty', 'stop', 'seed'})


class ParamTemplateManager:
    '''Manage API parameter templates with three-layer override.

    Layer 1 (lowest): task template defaults
    Layer 2 (mid):    provider-specific overrides
    Layer 3 (highest): explicit request parameters
    '''

    def __init__(self, templates_path=None):
        self._templates_path = Path(templates_path) if templates_path else _DEFAULT_TEMPLATES_PATH
        self._templates = {}
        self._overrides = {}
        self._load()

    def _load(self):
        if not self._templates_path.exists():
            logger.warning('param templates file not found: %s', self._templates_path)
            return
        try:
            data = yaml.safe_load(self._templates_path.read_text(encoding='utf-8')) or {}
        except Exception as e:
            logger.error('failed to load param templates: %s', e)
            return
        self._templates = data.get('templates', {}) or {}
        self._overrides = data.get('model_overrides', {}) or {}
        logger.info('loaded %d param templates, %d provider overrides', len(self._templates), len(self._overrides))

    def reload(self):
        '''Hot-reload templates from disk.'''
        self._load()

    def get_template(self, task_type):
        '''Get parameter template for a task type.'''
        tpl = self._templates.get(task_type)
        return dict(tpl) if tpl else {}

    def apply_template(self, request_params, task_type, provider=None):
        '''Apply three-layer override and return final parameters.'''
        tpl = self._templates.get(task_type, {})
        result = {k: v for k, v in tpl.items() if k in _PARAM_KEYS}
        if provider and provider in self._overrides:
            result.update(self._overrides[provider])
        if request_params:
            result.update(request_params)
        return result

    def list_templates(self):
        '''List all available task type names.'''
        return sorted(self._templates.keys())

    def list_all(self):
        '''Return all templates and overrides with full details.'''
        return {'templates': dict(self._templates), 'model_overrides': dict(self._overrides), 'total': len(self._templates)}

    def get_description(self, task_type):
        '''Get the description for a task type.'''
        tpl = self._templates.get(task_type, {})
        return tpl.get('description', '')


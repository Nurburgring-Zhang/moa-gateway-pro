# Pull Request 模板
## 描述
<!-- 简要说明这个 PR 解决了什么问题 / 增加了什么功能 -->

## 类型
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update
- [ ] Refactor / cleanup

## 改动
<!-- 列出主要改动点 -->

## 测试
- [ ] 本地 `python -m pytest tests/ -v` 全部通过
- [ ] 本地 `python scripts/audit_ui_e2e.py` 端到端通过
- [ ] 本地 `python scripts/test_4_presets.py` 4 preset 跑通
- [ ] 新增测试覆盖(如果适用)

## 关联
<!-- 关联 issue / 讨论 -->

## Checklist
- [ ] 没有泄露 secret(API key / token / 密码)
- [ ] 没有把 `.venv` / `data/` / `*.db` / `.fernet_key` 加进 commit
- [ ] README / docs 同步更新
- [ ] CHANGELOG 更新(如果重大改动)
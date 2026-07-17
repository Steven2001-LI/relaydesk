# 协作规则（Baton，原 RelayDesk）
- 当前阶段规范：docs/packaging-fix-spec.md（阶段推进时更新此行）
- 离线测试：python -m pytest langgraph_cs -q（改名后为 python -m pytest baton -q）
- 所有工作在阶段分支上做，不直接改 main，不重写 git 历史
- 每阶段收尾：git push + gh pr create --draft，PR 描述附门禁原始输出与变更摘要表
- reviews/ 与 docs/superpowers/ 已 gitignore，过程产物不入库
- 本仓库唯一写者是 Claude Code，评审由外部只读方完成
- 若你是评审方（Codex）：一律只读，禁止任何修改

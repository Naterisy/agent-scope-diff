# Agent Scope Diff

同一个仓库，换个目录启动 Codex，它可能会读到不同的规则。Agent Scope Diff 把两个目录的规则发现结果并排展示，告诉你哪个 `AGENTS.md` 生效、哪个被同目录的 override 盖住、哪个被字节上限截断。

需要 Python 3.11 或更新版本，不需要 API Key，也不需要装 Codex：

```sh
python3 agent_scope_diff.py compare examples/demo-project/apps/web examples/demo-project/apps/api --root examples/demo-project --codex-home examples/demo-home --json | python3 report.py > scope.html
```

打开生成的 `scope.html`。示例里 Web 会选 `AGENTS.override.md`，API 会选自己的 `AGENTS.md`，项目根目录规则两边都有。也可以只查一个目录：

```sh
python3 agent_scope_diff.py /path/to/repo/apps/web
```

程序在本地模拟 Codex CLI 的文件发现，不读取会话记录，也不能证明某次会话实际收到了或遵守了哪些规则。它默认不读全局规则，如需检查请显式传入 `--codex-home`。HTML 不放规则正文、指纹或机器绝对路径，但仍显示相对文件名，转发前建议检查一眼。完整用法见 [英文 README](README.md)。

# 修复 pi-code-reasoning 5.1.0 的 host dependency 告警

Pi 0.99.1 的 `docs/packages.md` 要求扩展把 host 提供的 `typebox` 声明为 `peerDependencies: { "typebox": "*" }`。`@feniix/pi-code-reasoning@5.1.0` 仍把它放在 `dependencies`，因此加载时出现告警。上游已归档；2026-09-30 查询 npm 的 latest 仍为此版本。

本补丁只移动这项声明，保留 `@feniix/bridgekit`、其他 manifest 字段、npm 根 lock 和已安装的依赖树。Pi 的告警扫描的是扩展根 manifest；BridgeKit 的编译代码仍可能加载独立的物理 TypeBox。此次修复证明的是声明合规和三个 reasoning 工具可运行，不承诺消除所有传递依赖副本。

## 应用或重装后重放

在仓库根目录运行，需要 Bash 和 Python 3：

```bash
bash pi-agent/patches/pi-code-reasoning-5.1.0/apply.sh
```

默认目标为 `${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/npm/node_modules/@feniix/pi-code-reasoning`。其他安装位置可直接传入包目录：

```bash
bash pi-agent/patches/pi-code-reasoning-5.1.0/apply.sh /path/to/pi-code-reasoning
```

脚本只接受精确的原始 5.1.0 manifest，或精确的已修复 manifest；已修复时成功退出且不写文件。其他版本、本地改动或 `package.json` 文件软链接都会明确拒绝，保留原内容。写入采用同目录临时文件加原子替换，保留文件权限。它不会自动执行 npm、重启或重载 Pi。

Pi 扩展更新、npm 重装可能覆盖已安装 manifest，届时重新运行此脚本。源码 `pi-copy-links` 的兼容修复另由仓库维护，不需要此脚本。完成后在 Pi 空闲时 `/reload`；若刚刚原地升级 Pi 本体，应正常退出并重启 Pi，以免旧进程保留升级前的模块。

## 核验与回退

原始 manifest SHA-256：`cc47dba462775b26022fafc099f4c5f04b48fc39096f32bf4cfa6a5f8d7379fa`。
修复后 SHA-256：`3bcf117a73f2a8c250e7228b96e24bd91a2c3e9b1030ec05e6b12a7d3d81b832`。

`host-typebox-peer.patch` 展示完整两字段差异。回退前确认当前 manifest 为修复后哈希，再以 `patch --reverse --dry-run` 检查该差异；不要覆盖后来的其他修改。`test/fixtures/package.json` 是 npm 公开发布的 5.1.0 原始 manifest，用于离线测试，不是本机配置。

```bash
python3 pi-agent/patches/pi-code-reasoning-5.1.0/test/replay.py
```

修复时用 Pi 0.99.1 的 bundled resource loader 加载实际扩展，未出现此 dependency 告警；主机参数校验接受合法输入、拒绝缺字段/非法范围，隔离实例的 reasoning/status/reset 调用完成 `0 → 1 → 0` 状态变化。自动测试不调用模型、不重置用户当前 reasoning 状态。

# 两台机器按同一提交获取材料

本分支仅交付材料。不要在运行代码目录执行 `git pull`、`reset`、`switch` 或整分支 `cherry-pick` 来安装材料；使用 `fetch + archive`，保持当前 HEAD、暂存区和未提交修复。

在现有仓库目录内，用 Bash 执行下列代码。将 `KIT_COMMIT` 设置为用户消息中的完整 40 位提交。该值在 Git 提交生成后提供，不自写进本提交。

```bash
set -euo pipefail
umask 077
KIT_COMMIT='<用户提供的40位提交>'
KIT_REF='refs/heads/codex/argus-controlled-evaluation-20261004'
KIT_PATH='cczoo/agent-cc/documents_ly/paper-experiment-kit-20261004'
KIT_REMOTE='https://github.com/13-pieces-teen/confidential-computing-zoo.git'

git fetch --no-tags "$KIT_REMOTE" "$KIT_REF"
git cat-file -e "$KIT_COMMIT^{commit}"
git merge-base --is-ancestor "$KIT_COMMIT" FETCH_HEAD

KIT_DEST=$(mktemp -d /root/argus-paper-kit-20261004.XXXXXX)
git archive --format=tar "$KIT_COMMIT" "$KIT_PATH" | tar -xf - -C "$KIT_DEST"
KIT_DIR="$KIT_DEST/$KIT_PATH"
(cd "$KIT_DIR" && sha256sum -c SHA256SUMS)
printf 'Material directory: %s\n' "$KIT_DIR"
```

原仓库的 origin 无需改动。若 GitHub 直连不可用，沿用本机已有批准的 Git 网络通道；模型请求的取消代理设置不应扩展成全局网络配置变更。无法获取时报告具体 fetch 错误，不用旧包替代本提交。

导出的目录就是双方后续使用的只读材料入口。结果另存，双方回执中记录材料提交和各自真实运行代码的提交/补丁摘要；两种版本分开。

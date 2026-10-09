# 固定 Git 版本读取方法

本文件用于 Linux 上的 IP1/IP2。它只下载材料，不 checkout 运行代码，也不修改部署仓库。以用户消息给出的完整提交 SHA 为准，不把可移动的分支名当作版本锁。

仓库：`https://github.com/13-pieces-teen/confidential-computing-zoo.git`

分支：`docs/argus-minimal-experiments-20261009`

目录：`cczoo/agent-cc/experiments/argus/paper-minimal-20261009/`

## 独立目录获取

先将下面的占位符替换为用户给出的40位提交SHA。使用本机正常Git网络与既有凭据，不把token放进命令，不改变forced-command通道。

```bash
set -euo pipefail
umask 077
repo_url='https://github.com/13-pieces-teen/confidential-computing-zoo.git'
expected_commit='REPLACE_WITH_DELIVERED_40_CHARACTER_COMMIT'
kit_path='cczoo/agent-cc/experiments/argus/paper-minimal-20261009'
[[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]]
fetch_root=$(mktemp -d /tmp/argus-paper-minimal.XXXXXX)
git init -q "$fetch_root/git"
git -C "$fetch_root/git" remote add origin "$repo_url"
git -C "$fetch_root/git" config remote.origin.promisor true
git -C "$fetch_root/git" config remote.origin.partialclonefilter blob:none
git -C "$fetch_root/git" -c protocol.version=2 fetch --depth=1 --no-tags --filter=blob:none origin "$expected_commit"
actual_commit=$(git -C "$fetch_root/git" rev-parse 'FETCH_HEAD^{commit}')
test "$actual_commit" = "$expected_commit"
mkdir "$fetch_root/materials"
for name in .gitattributes README.md PLAN.md IP1-PROMPT.md IP2-PROMPT.md GIT-GET.md SHA256SUMS; do
  git -C "$fetch_root/git" show "$expected_commit:$kit_path/$name" > "$fetch_root/materials/$name"
done
(cd "$fetch_root/materials" && sha256sum -c SHA256SUMS)
printf '%s\n' "$actual_commit" > "$fetch_root/MATERIAL-COMMIT.txt"
printf 'Materials: %s\n' "$fetch_root/materials"
```

浅拉固定提交并仅导出七个列明文件；无需展开完整仓库。`--filter=blob:none` 由 GitHub 支持，Git会按需取得文档blob。SHA清单校验的是Git保存的LF字节；不要先用编辑器转换换行。

若固定SHA fetch受当前Git代理限制，可在同一全新目录fetch `refs/heads/docs/argus-minimal-experiments-20261009`，但仍必须核对得到的SHA等于用户给定值后才读取。分支已移动则不要自动改用新HEAD。无法获取时记录具体错误并通过已有获准文件通道取得同一固定版本，仍核对SHA；不改变TLS验证或旧管理通道权限。

## 获取后执行

1. 读 PLAN.md，再读 IP1-PROMPT.md 或 IP2-PROMPT.md。
2. 把材料完整SHA记入本批manifest；另列实际运行工具/二进制/镜像摘要。
3. 只使用当前已部署工具与现有健康服务。本包不授权部署其祖先代码。
4. 各自按已有results分支交付结果，不向docs分支提交，不覆盖旧实验原件。

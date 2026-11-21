# PR review workflow

Spark's PR surface is read-only by default: it fetches metadata, files, diff,
comments and CI status from GitHub / GitCode / Gitee. Publishing a review is a
separate command that requires `--confirm`.

## Remote/token

| remote | default API | token env |
| --- | --- | --- |
| `github.com` | `https://api.github.com` | `GITHUB_TOKEN` / `GH_TOKEN` |
| `gitcode.com` | `https://api.gitcode.com/api/v5` | `GITCODE_TOKEN` |
| `gitee.com` | `https://gitee.com/api/v5` | `GITEE_TOKEN` |

## Token setup

```bash
spark pr status --repo owner/name       # 平台、仓库、token 来源（只显示是否已配置）
spark pr token github                  # 交互输入，不进入 shell 历史，文件权限 600
spark pr token gitcode                 # 或 gitee
spark pr token github --clear          # 删除本机保存的 token
```

- 保存位置：`$SPARK_HOME/pr-tokens.json`，权限 600。
- 环境变量优先于本地文件：`GITHUB_TOKEN`/`GH_TOKEN`、`GITCODE_TOKEN`、
  `GITEE_TOKEN`。
- GitHub 读取 token 需要 `repo`（或公开仓库的 `public_repo`）权限；发布 review
  还需要 `Pull requests: write`。GitCode/Gitee 使用平台的 access token。
- 未配置 token 仍可读公开仓库，但受未认证速率限制；`spark pr status` 会给出
  配置入口和所需权限。

`spark pr` reads `origin` from the current repository; `--repo owner/name`
overrides it. To force a platform, prefix the override with its host:
`github.com/owner/name`, `gitcode.com/owner/name`, or `gitee.com/owner/name`.

## Local flow

```bash
# 1. Read context + deterministic review brief
spark pr review 42 --repo owner/name

# 2. Read the diff too (review brief includes risk/test/CI checklist)
spark pr review 42 --diff

# 3. Preview the body, then explicitly publish it
spark pr publish 42 --body-file review.md --event COMMENT
spark pr publish 42 --body-file review.md --event COMMENT --confirm

# GitHub only: request changes / approve are also accepted
spark pr publish 42 --body-file review.md --event REQUEST_CHANGES --confirm
```

`publish` without `--confirm` only prints a preview and exits non-zero, so a
script cannot accidentally write to a PR. GitCode/Gitee publish a PR comment;
GitHub publishes a review object.

## CI trigger

`.github/workflows/spark-pr-review.yml` listens for a PR comment beginning with
`/spark review`, generates the deterministic brief, and publishes it with
`--confirm`. This action does not call a model and does not need model
credentials; it only needs the repository's `GITHUB_TOKEN` with
`pull-requests: write`.

For GitCode/Gitee CI, run the same two commands from a job, set
`PR_NUMBER`/`GITCODE_TOKEN` (or `GITEE_TOKEN`) in the environment, and keep the
publish step behind a manual approval or an explicit slash command in your CI
configuration.

## Safety properties

- PR text, diff and comments are wrapped in `<untrusted>` fences before they
  reach the model.
- Tokens come from the environment or `$SPARK_HOME/pr-tokens.json` (0600); they are
  never written into tool payloads or command lines.
- `pr_review` is a read-only tool; `publish` is CLI-only and requires `--confirm`.
- Browser and PR tools both honor `agent.network_access = false`.

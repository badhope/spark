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
- Tokens are read from the environment and never written into tool payloads or
  command lines.
- `pr_review` is a read-only tool; `publish` is CLI-only and requires `--confirm`.
- Browser and PR tools both honor `agent.network_access = false`.

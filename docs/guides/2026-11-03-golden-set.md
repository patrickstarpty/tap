# Golden set 指南

创建日期：2026-11-03。Golden set 是仓库内的知识问答回归集：确定性模型回归纳入 `make test` 与 CI，真实模型运行只报告分数。背景见[可靠问答设计](../superpowers/specs/2026-11-03-reliable-answering-design.md)。

## 1. 文件与格式

目录 `apps/tap-ai-backend/tests/quality/golden/`：

| 文件 | 内容 |
| --- | --- |
| `corpus/*.md` | 固定语料，脱敏、可公开 |
| `kb-golden.v1.jsonl` | 每行一条用例（当前 31 条：`fact` 13、`procedure` 5、`abstain` 5、`relation` 8） |
| `baseline.fake.json` | 确定性模型基线分数，按类别与 `total` 记录 |

用例示例：

```json
{"id": "kb-fact-001", "category": "fact", "question": "账户连续输错几次密码会被锁定？", "sources": ["account-lockout-policy.md"], "expect": {"abstain": false, "citedDocuments": ["account-lockout-policy.md"], "mustContain": ["5 次"]}}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定 ID，如 `kb-fact-001` |
| `category` | `fact` / `procedure` / `abstain` / `relation` |
| `question` | 问题 |
| `sources` | 参与检索的语料文件名 |
| `expect.abstain` | 是否应拒答 |
| `expect.citedDocuments` | 期望被引用的语料文件名（命中任一即可） |
| `expect.mustContain` | 回答须包含的关键词（大小写不敏感） |
| `expect.relations` | 仅 `relation`：期望引用的关系类型 |

运行器 `tap.quality.golden` 把 `sources` 语料写入进程内检索适配器，再经生产回答编排作答。指标按类别与总计计算：

- `citationRecall`：应作答用例中，回答引用命中 `citedDocuments` 的比例。
- `keywordRecall`：应作答且有 `mustContain` 的用例中，关键词命中比例的平均值。
- `relationRecall`：具有 `expect.relations` 的应答用例中，实际关系引用命中的关系类型比例；不根据答案中出现的文字推测关系引用。
- `abstainAccuracy`：拒答与否与 `expect.abstain` 一致的比例。
- `answered`：应作答用例中未拒答且未出错的比例。

关系用例的 `service-relations.md` 同时含自然语言说明与明确的 `主体 --RELATION--> 客体` 语句。运行器用确定性抽取夹具生成不可变快照，经生产 `GraphAnswerEnricher`、切片反向索引和回答引用校验作答；期望值不参与抽取或模型生成。这里度量关系回答与引用链路，不代表真实模型的图谱抽取质量。

## 2. 命令

```sh
make quality-golden       # 确定性模型回归，与 baseline.fake.json 比较
make quality-golden-real  # 真实模型，只报告，不设门
```

- 每次运行在终端输出表格，并把 JSON 报告写入 `apps/tap-ai-backend/.quality/golden-<timestamp>.json`（已 gitignore）。
- 确定性回归中任一基线指标下降或缺失即失败（零容忍）。同一回归由 `tests/quality/test_golden_regression.py` 纳入 `make test`，并在 `.github/workflows/ci.yml` 中运行。
- 新增或修改用例使确定性分数变化时，用 `--write-baseline` 更新基线并随用例一起提交：

```sh
uv run --project apps/tap-ai-backend python scripts/run-quality-golden.py \
  --write-baseline apps/tap-ai-backend/tests/quality/golden/baseline.fake.json
```

## 3. 写真实模型基线

`make quality-golden-real` 读取仓库根目录 `.env`，要求 `LITELLM_BASE_URL` 与 `LITELLM_MASTER_KEY`，缺失时报错退出。聊天与 embedding 模型取 `TAPPER_DEFAULT_CHAT_MODEL`（默认 `qwen-plus`）、`TAPPER_EMBEDDING_MODEL`（默认 `text-embedding-v4`）与 `TAPPER_EMBEDDING_DIMENSION`（默认 `1536`）。

记录真实模型业务基线时，把结果写到仓库外或 `.quality/` 下，不覆盖确定性基线：

```sh
TAP_GOLDEN_REAL_MODEL=1 uv run --project apps/tap-ai-backend python scripts/run-quality-golden.py \
  --real --write-baseline apps/tap-ai-backend/.quality/baseline.real.json
```

真实模型分数属于业务验证：30–50 条真实标注问答的基线由使用者用上述命令产出，不阻塞工程完成。

## 4. MySQL 持久化测试

`make test` 默认跳过需要 MySQL 的持久化集成测试，并在结尾输出摘要 `MySQL persistence tests: N skipped (set TAP_RUN_MYSQL_INTEGRATION=1 with owned MySQL)`。本地有 Docker 时设置 `TAP_RUN_MYSQL_INTEGRATION=1` 运行（测试经本地 Docker 自建并清理独立的 MySQL compose 项目，不使用 `TAP_DATABASE_URL`）；设置 `TAP_REQUIRE_MYSQL_INTEGRATION=1` 时，缺 MySQL 会使测试失败而不是跳过。

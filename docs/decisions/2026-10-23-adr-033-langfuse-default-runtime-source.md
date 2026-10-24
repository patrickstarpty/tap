---
id: ADR-033
status: proposed
date: 2026-10-23
accepted-date: null
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-033：Langfuse 作为默认组件与团队视图运行数据的唯一查询来源

## 背景

V2.1 Agent 团队视图（[设计](../superpowers/specs/2026-10-22-agent-team-view-design.md)）需要按 Agent、按人、按周期展示运行次数、失败、时长、成本与服务对象。此前 Langfuse 是可选组件（profile `langfuse`），仅作运营看板，业务进程不感知它；MySQL 的 `trace_span` 与 `model_call` 是链路原始记录，但没有面向聚合查询的结构。

## 决策

- Langfuse 由可选组件改为默认组件，随 Tapper demo 的默认依赖启动。
- 团队视图的运行数据以 Langfuse Metrics API v1 为唯一查询来源；`agent:<agentKey>` 与 `outcome:<state>` 标签只打在根 span 为 `turn.request` 的 trace 上。满意度、负责人、维护履历等 MySQL 数据每次实时查询，只缓存 Langfuse 运行数据（TTL 300 秒）。
- Langfuse 固定 3.x（本地实测 3.225.11）。Langfuse 不可达时返回 `runtimeStatus: "unavailable"`，运行派生字段为 null，不返回 5xx。

## 考虑过的方案

- 直接查询 MySQL `trace_span` / `model_call`：不新增依赖，但要在 MySQL 上自建按日期、标签、用户的聚合，并重复实现 Langfuse 已有的成本与延迟口径；拒绝。
- 自建汇总表：查询快，但需要新的写路径、回填与口径维护，与 Langfuse 同步链路重复；拒绝。

## 后果

- 本地资源占用上升：默认启动 `langfuse-web`、`langfuse-worker`、`langfuse-postgres`，并依赖 `clickhouse`。
- Langfuse v4 升级时需要把 Metrics API v1 迁移到 v2。
- 隔离 e2e 不依赖真实 Langfuse，使用基于 Turn 表的运行数据适配器；真实 Langfuse 另行冒烟验证。
- 「最近运行」不在 Metrics API 度量中，改用 Traces 列表 API，每个 Agent 一次并随其他查询缓存。

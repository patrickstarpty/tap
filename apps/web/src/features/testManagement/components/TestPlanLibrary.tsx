import { FileTextOutlined } from "@ant-design/icons";
import { Button, Input, Spin } from "antd";
import { useEffect, useRef, useState } from "react";

import { useTestPlanGeneration, useTestPlans } from "../api/queries";

const COPY = {
  en: {
    heading: "Test Management",
    description: "Review generated Test Plans and their source evidence.",
    table: "Test Plans",
    count: "Test Plans",
    search: "Search Test Plans",
    name: "Name",
    cases: "Cases",
    citations: "Citations",
    origin: "Origin",
    status: "Status",
    action: "Open",
    empty: "No Test Plans yet",
    emptyDetail:
      "Create a grounded answer in Tapper, then generate a reviewable draft.",
    noMatch: "No matching Test Plans",
    goTapper: "Go to Tapper",
    loading: "Loading Test Plans",
    error: "Test Plans could not be loaded. Try again.",
    generating: "Generating a Test Plan draft… This can take a minute.",
    generationFailed:
      "The Test Plan draft could not be generated. Try again from Tapper.",
    generationStatusFailed:
      "Generation status could not be loaded. Refresh to try again.",
  },
  zh: {
    heading: "测试管理",
    description: "查看生成的测试计划及其来源证据。",
    table: "测试计划列表",
    count: "个测试计划",
    search: "搜索测试计划",
    name: "名称",
    cases: "用例",
    citations: "引用",
    origin: "来源",
    status: "状态",
    action: "打开",
    empty: "还没有测试计划",
    emptyDetail: "先在 Tapper 中获得有来源的回答，再生成可评审的草稿。",
    noMatch: "没有匹配的测试计划",
    goTapper: "前往 Tapper",
    loading: "正在加载测试计划",
    error: "测试计划暂时无法加载，请重试。",
    generating: "正在生成测试计划草稿，可能需要一分钟。",
    generationFailed: "测试计划草稿生成失败，请返回 Tapper 重试。",
    generationStatusFailed: "暂时无法获取生成进度，请刷新页面重试。",
  },
} as const;

const STATUS = {
  en: {
    DRAFT: "Draft",
    VALIDATING: "Validating",
    PUBLISHED: "Published",
    SUPERSEDED: "Superseded",
  },
  zh: {
    DRAFT: "草稿",
    VALIDATING: "验证中",
    PUBLISHED: "已发布",
    SUPERSEDED: "已取代",
  },
} as const;

export function TestPlanLibrary({
  projectId,
  locale,
  onOpen,
  onGoTapper,
  generationJobId = null,
  generationError = null,
}: {
  projectId: string;
  locale: "en" | "zh";
  onOpen: (planId: string, revisionId: string) => void;
  onGoTapper: () => void;
  generationJobId?: string | null;
  generationError?: string | null;
}) {
  const plans = useTestPlans(projectId);
  const generation = useTestPlanGeneration(projectId, generationJobId);
  const refreshedJob = useRef<string | null>(null);
  const [query, setQuery] = useState("");
  const text = COPY[locale];
  useEffect(() => {
    if (
      generationJobId !== null &&
      generation.data?.status === "DRAFT_READY" &&
      refreshedJob.current !== generationJobId
    ) {
      refreshedJob.current = generationJobId;
      void plans.refetch();
    }
  }, [generationJobId, generation.data?.status, plans]);
  const items = (plans.data ?? []).filter((plan) =>
    plan.title.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <section
      className="tap-module tap-test-management tap-durable-plans"
      aria-labelledby="test-management-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="test-management-heading">{text.heading}</h1>
          <p>{text.description}</p>
        </div>
      </header>
      {plans.isPending ? <Spin tip={text.loading} /> : null}
      {plans.isError ? <p role="alert">{text.error}</p> : null}
      {generationError ? <p role="alert">{generationError}</p> : null}
      {generationJobId !== null && generation.isError ? (
        <p role="alert">{text.generationStatusFailed}</p>
      ) : null}
      {generation.data?.status === "FAILED" ? (
        <p role="alert">{text.generationFailed}</p>
      ) : null}
      {generationJobId !== null &&
      !generation.isError &&
      generation.data?.status !== "DRAFT_READY" &&
      generation.data?.status !== "FAILED" ? (
        <p role="status">{text.generating}</p>
      ) : null}
      {!plans.isPending && !plans.isError ? (
        <div className="tap-plan-workspace">
          <div className="tap-list-toolbar">
            <span>
              {plans.data?.length ?? 0} {text.count}
            </span>
            <Input.Search
              aria-label={text.search}
              placeholder={text.search}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {items.length === 0 &&
          generationJobId !== null &&
          !generation.isError &&
          generation.data?.status !== "FAILED" &&
          generation.data?.status !== "DRAFT_READY" ? null : items.length ===
            0 ? (
            <div className="tap-data-workspace">
              <FileTextOutlined aria-hidden="true" />
              <h2>{plans.data?.length ? text.noMatch : text.empty}</h2>
              {!plans.data?.length ? (
                <>
                  <p>{text.emptyDetail}</p>
                  <Button onClick={onGoTapper}>{text.goTapper}</Button>
                </>
              ) : null}
            </div>
          ) : (
            <div className="tap-durable-plan-scroll">
              <div
                className="tap-plan-table"
                role="table"
                aria-label={text.table}
              >
                <div
                  className="tap-plan-row tap-plan-row-header tap-plan-row--durable"
                  role="row"
                >
                  <span role="columnheader">{text.name}</span>
                  <span role="columnheader">{text.cases}</span>
                  <span role="columnheader">{text.citations}</span>
                  <span role="columnheader">{text.origin}</span>
                  <span role="columnheader">{text.status}</span>
                  <span role="columnheader" aria-label={text.action} />
                </div>
                {items.map((plan) => (
                  <div
                    className="tap-plan-row tap-plan-row--durable"
                    role="row"
                    key={plan.revisionId}
                  >
                    <span role="cell">
                      <FileTextOutlined aria-hidden="true" />
                      <span>
                        <strong>{plan.title.replaceAll("-", " ")}</strong>
                        <small title={plan.objective}>{plan.objective}</small>
                      </span>
                    </span>
                    <span role="cell">{plan.cases.length}</span>
                    <span role="cell">{plan.citations.length}</span>
                    <span role="cell">
                      {plan.origin === "VALIDATION"
                        ? locale === "zh"
                          ? "验收"
                          : "Validation"
                        : locale === "zh"
                          ? "产品"
                          : "Product"}
                    </span>
                    <span role="cell">{STATUS[locale][plan.status]}</span>
                    <span role="cell">
                      <Button
                        onClick={() => onOpen(plan.testPlanId, plan.revisionId)}
                      >
                        {text.action}
                      </Button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

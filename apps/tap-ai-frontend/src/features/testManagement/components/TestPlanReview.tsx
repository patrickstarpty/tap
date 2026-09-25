import { Alert, Button, Input, Spin } from "antd";
import { useEffect, useMemo, useState } from "react";

import { TestPlanApiError, type TestPlanRevision } from "../api/client";
import {
  useForkTestPlan,
  usePublishTestPlan,
  useReviewTestPlan,
  useTestPlan,
  useUpdateTestPlan,
} from "../api/queries";
import { TestPlanDetail } from "./TestPlanDetail";

const commandKey = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;

function validateDraft(plan: TestPlanRevision, locale: "en" | "zh") {
  const errors: string[] = [];
  for (const testCase of plan.cases) {
    for (const scenario of testCase.scenarios) {
      const order = scenario.steps.map((step) => step.keyword).join(",");
      if (!/^Given(?:,And)*,When(?:,And)*,Then(?:,And)*$/u.test(order)) {
        errors.push(
          locale === "zh"
            ? `场景“${scenario.title}”必须按 Given、When、Then 排列。`
            : `Scenario “${scenario.title}” must follow Given, When, Then order.`,
        );
      }
      if (
        scenario.steps.some(
          (step) => step.keyword === "Then" && !step.expectedResult?.trim(),
        )
      ) {
        errors.push(
          locale === "zh"
            ? `场景“${scenario.title}”的 Then 必须填写预期结果。`
            : `Scenario “${scenario.title}” needs an expected result for Then.`,
        );
      }
    }
  }
  return errors;
}

function ReviewWorkspace({
  initial,
  projectId,
  locale,
  reload,
  onBack,
  onOpenRevision,
  onOpenAutomation,
}: {
  initial: TestPlanRevision;
  projectId: string;
  locale: "en" | "zh";
  reload: () => Promise<TestPlanRevision | undefined>;
  onBack: () => void;
  onOpenRevision?: (planId: string, revisionId: string) => void;
  onOpenAutomation?: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [reason, setReason] = useState("");
  const [conflict, setConflict] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const update = useUpdateTestPlan(projectId);
  const review = useReviewTestPlan(projectId);
  const publish = usePublishTestPlan(projectId);
  const fork = useForkTestPlan(projectId);
  const validationErrors = useMemo(
    () => validateDraft(draft, locale),
    [draft, locale],
  );
  const currentReview = (draft.reviewDecisions ?? []).at(-1);
  const canPublish =
    draft.status === "DRAFT" &&
    !draft.needsReview &&
    currentReview?.reviewedContentDigest === draft.contentDigest &&
    (currentReview.disposition === "ACCEPTED_UNCHANGED" ||
      currentReview.disposition === "ACCEPTED_MODIFIED");
  useEffect(() => {
    if (initial.status !== draft.status) setDraft(initial);
  }, [draft.status, initial]);

  const save = async () => {
    setMessage(null);
    if (validationErrors.length > 0) return;
    try {
      const saved = await update.mutateAsync({
        plan: draft,
        key: commandKey("edit"),
      });
      if (saved) setDraft(saved);
      setConflict(false);
      setMessage(locale === "zh" ? "草稿已保存。" : "Draft saved.");
    } catch (error) {
      if (error instanceof TestPlanApiError && error.status === 409) {
        setConflict(true);
        return;
      }
      setMessage(
        locale === "zh" ? "草稿保存失败。" : "Draft could not be saved.",
      );
    }
  };

  const decide = async (
    disposition: "ACCEPTED_UNCHANGED" | "ACCEPTED_MODIFIED" | "REJECTED",
  ) => {
    if (!reason.trim()) return;
    const reviewed = await review.mutateAsync({
      plan: draft,
      disposition,
      reason: reason.trim(),
      key: commandKey("review"),
    });
    if (reviewed) setDraft(reviewed);
  };

  return (
    <section
      className="tap-module tap-durable-review"
      aria-label={locale === "zh" ? "测试计划评审" : "Test Plan review"}
    >
      <div className="tap-plan-review-actions">
        <Button onClick={onBack}>
          {locale === "zh" ? "返回列表" : "Back to plans"}
        </Button>
        {draft.status === "PUBLISHED" ? (
          <Button
            loading={fork.isPending}
            onClick={async () => {
              const next = await fork.mutateAsync({
                plan: draft,
                key: commandKey("fork"),
              });
              if (next) onOpenRevision?.(next.testPlanId, next.revisionId);
            }}
          >
            {locale === "zh" ? "创建新草稿" : "Create new draft"}
          </Button>
        ) : null}
        {onOpenAutomation ? (
          <Button onClick={onOpenAutomation}>
            {locale === "zh" ? "关联自动化" : "Open Automation"}
          </Button>
        ) : null}
        <Button
          type="primary"
          disabled={!canPublish}
          loading={publish.isPending}
          onClick={() =>
            publish.mutate({ plan: draft, key: commandKey("publish") })
          }
        >
          {draft.status === "DRAFT"
            ? locale === "zh"
              ? "批准并发布"
              : "Approve and publish"
            : locale === "zh"
              ? "已发布"
              : "Published"}
        </Button>
      </div>

      {conflict ? (
        <Alert
          type="warning"
          showIcon
          message={
            locale === "zh"
              ? "服务端草稿已更新；你的未发送修改仍保留。"
              : "The server draft changed; your unsent edits are preserved."
          }
          action={
            <Button
              onClick={async () => {
                const latest = await reload();
                if (latest) {
                  setDraft((unsent) => ({
                    ...latest,
                    title: unsent.title,
                    objective: unsent.objective,
                    scopeItems: unsent.scopeItems,
                    prerequisites: unsent.prerequisites,
                    risks: unsent.risks,
                    cases: unsent.cases,
                    citations: unsent.citations,
                    assumptions: unsent.assumptions,
                    unknowns: unsent.unknowns,
                    coverageGaps: unsent.coverageGaps,
                  }));
                }
                setConflict(false);
              }}
            >
              {locale === "zh" ? "重新加载版本" : "Reload version"}
            </Button>
          }
        />
      ) : null}

      {draft.status === "DRAFT" ? (
        <section
          className="tap-plan-editor"
          aria-label={locale === "zh" ? "编辑草稿" : "Edit draft"}
        >
          <h2>{locale === "zh" ? "编辑草稿" : "Edit draft"}</h2>
          <label>
            <span>{locale === "zh" ? "计划目标" : "Plan objective"}</span>
            <Input.TextArea
              aria-label={locale === "zh" ? "计划目标" : "Plan objective"}
              value={draft.objective}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  objective: event.target.value,
                }))
              }
            />
          </label>
          {draft.cases.flatMap((testCase, caseIndex) =>
            testCase.scenarios.flatMap((scenario, scenarioIndex) =>
              scenario.steps.map((step, stepIndex) =>
                step.keyword === "Then" ? (
                  <label key={step.stepId}>
                    <span>
                      {locale === "zh"
                        ? "Then 预期结果"
                        : "Then expected result"}
                    </span>
                    <Input
                      aria-label={`${locale === "zh" ? "预期结果" : "Expected result"} ${step.stepId}`}
                      value={step.expectedResult ?? ""}
                      onChange={(event) =>
                        setDraft((current) => {
                          const cases = structuredClone(current.cases);
                          cases[caseIndex]!.scenarios[scenarioIndex]!.steps[
                            stepIndex
                          ]!.expectedResult = event.target.value;
                          return { ...current, cases };
                        })
                      }
                    />
                  </label>
                ) : null,
              ),
            ),
          )}
          {validationErrors.map((error) => (
            <p role="alert" key={error}>
              {error}
            </p>
          ))}
          <Button loading={update.isPending} onClick={() => void save()}>
            {locale === "zh" ? "保存草稿" : "Save draft"}
          </Button>
        </section>
      ) : null}

      <TestPlanDetail plan={draft} locale={locale} />

      {draft.status === "DRAFT" ? (
        <section
          className="tap-plan-review-decision"
          aria-label={locale === "zh" ? "业务评审" : "Business review"}
        >
          <h2>{locale === "zh" ? "业务评审" : "Business review"}</h2>
          <Input.TextArea
            aria-label={locale === "zh" ? "评审理由" : "Review reason"}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={
              locale === "zh"
                ? "说明采纳或拒绝的业务理由"
                : "Explain the business decision"
            }
          />
          <div className="tap-plan-review-actions">
            <Button
              disabled={!reason.trim()}
              onClick={() => void decide("ACCEPTED_UNCHANGED")}
            >
              {locale === "zh" ? "原样采纳" : "Accept unchanged"}
            </Button>
            <Button
              disabled={!reason.trim()}
              onClick={() => void decide("ACCEPTED_MODIFIED")}
            >
              {locale === "zh" ? "修改后采纳" : "Accept modified"}
            </Button>
            <Button
              danger
              disabled={!reason.trim()}
              onClick={() => void decide("REJECTED")}
            >
              {locale === "zh" ? "拒绝" : "Reject"}
            </Button>
          </div>
        </section>
      ) : null}

      {message ? <p role="status">{message}</p> : null}
      {publish.isError || review.isError ? (
        <Alert
          type="error"
          showIcon
          message={
            locale === "zh"
              ? "操作失败，请检查门禁后重试。"
              : "The action failed. Check the review gates and try again."
          }
        />
      ) : null}
    </section>
  );
}

export function TestPlanReview({
  projectId,
  planId,
  revisionId,
  locale,
  onBack,
  onOpenRevision,
  onOpenAutomation,
}: {
  projectId: string;
  planId: string;
  revisionId: string;
  locale: "en" | "zh";
  onBack: () => void;
  onOpenRevision?: (planId: string, revisionId: string) => void;
  onOpenAutomation?: () => void;
}) {
  const plan = useTestPlan(projectId, planId, revisionId);
  if (plan.isPending) {
    return (
      <Spin
        description={locale === "zh" ? "正在加载评审内容" : "Loading review"}
      />
    );
  }
  if (plan.isError || !plan.data) {
    return (
      <Alert
        type="error"
        showIcon
        message={
          locale === "zh"
            ? "测试计划暂时无法加载"
            : "Test Plan could not be loaded"
        }
      />
    );
  }
  return (
    <ReviewWorkspace
      key={plan.data.revisionId}
      initial={plan.data}
      projectId={projectId}
      locale={locale}
      reload={async () => (await plan.refetch()).data}
      onBack={onBack}
      onOpenRevision={onOpenRevision}
      onOpenAutomation={onOpenAutomation}
    />
  );
}

import { Alert, Button, Spin } from "antd";

import { usePublishTestPlan, useTestPlan } from "../api/queries";
import { TestPlanDetail } from "./TestPlanDetail";

export function TestPlanReview({
  projectId,
  planId,
  revisionId,
  locale,
  onBack,
}: {
  projectId: string;
  planId: string;
  revisionId: string;
  locale: "en" | "zh";
  onBack: () => void;
}) {
  const plan = useTestPlan(projectId, planId, revisionId);
  const publish = usePublishTestPlan(projectId);
  if (plan.isPending)
    return (
      <Spin
        description={locale === "zh" ? "正在加载评审内容" : "Loading review"}
      />
    );
  if (plan.isError || !plan.data)
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
  const current = plan.data;
  return (
    <section
      className="tap-module tap-durable-review"
      aria-label={locale === "zh" ? "测试计划评审" : "Test Plan review"}
    >
      <div className="tap-plan-review-actions">
        <Button onClick={onBack}>
          {locale === "zh" ? "返回列表" : "Back to plans"}
        </Button>
        <Button
          type="primary"
          disabled={current.status !== "DRAFT"}
          loading={publish.isPending}
          onClick={() =>
            publish.mutate({
              plan: current,
              key: `publish-${current.revisionId}`,
            })
          }
        >
          {current.status === "DRAFT"
            ? locale === "zh"
              ? "批准并发布"
              : "Approve and publish"
            : locale === "zh"
              ? "已发布"
              : "Published"}
        </Button>
      </div>
      <TestPlanDetail plan={current} locale={locale} />
      {publish.isError ? (
        <Alert
          type="error"
          showIcon
          message={
            locale === "zh"
              ? "发布失败；计划可能已被其他评审者更新，请重新加载。"
              : "Publish failed. This plan may have changed; reload and try again."
          }
        />
      ) : null}
    </section>
  );
}

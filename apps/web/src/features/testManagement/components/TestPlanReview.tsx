import { Alert, Button, Spin } from "antd";

import { usePublishTestPlan, useTestPlan } from "../api/queries";
import { TestPlanDetail } from "./TestPlanDetail";

export function TestPlanReview({
  projectId,
  planId,
  revisionId,
  onBack,
}: {
  projectId: string;
  planId: string;
  revisionId: string;
  onBack: () => void;
}) {
  const plan = useTestPlan(projectId, planId, revisionId);
  const publish = usePublishTestPlan(projectId);
  if (plan.isPending) return <Spin tip="正在加载评审内容" />;
  if (plan.isError || !plan.data)
    return <Alert type="error" showIcon message="测试计划暂时无法加载" />;
  const current = plan.data;
  return (
    <section aria-label="测试计划评审">
      <Button onClick={onBack}>返回列表</Button>
      <TestPlanDetail plan={current} />
      {publish.isError ? (
        <Alert
          type="error"
          showIcon
          message="发布失败；计划可能已被其他评审者更新，请重新加载。"
        />
      ) : null}
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
        {current.status === "DRAFT" ? "批准并发布" : "已发布"}
      </Button>
    </section>
  );
}

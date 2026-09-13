import { Button, Empty, Input, Spin } from "antd";
import { useState } from "react";

import { useTestPlans } from "../api/queries";

export function TestPlanLibrary({
  projectId,
  onOpen,
}: {
  projectId: string;
  onOpen: (planId: string, revisionId: string) => void;
}) {
  const plans = useTestPlans(projectId);
  const [query, setQuery] = useState("");
  if (plans.isPending) return <Spin tip="正在加载测试计划" />;
  if (plans.isError) return <p role="alert">测试计划暂时无法加载，请重试。</p>;
  const items = (plans.data ?? []).filter((plan) =>
    plan.title.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <section aria-labelledby="test-plan-library-title">
      <h2 id="test-plan-library-title">测试计划</h2>
      <Input.Search
        aria-label="搜索测试计划"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {items.length === 0 ? (
        <Empty description="暂无测试计划" />
      ) : (
        <ul>
          {items.map((plan) => (
            <li key={plan.revisionId}>
              <strong>{plan.title}</strong> <span>{plan.status}</span>{" "}
              <Button onClick={() => onOpen(plan.testPlanId, plan.revisionId)}>
                打开
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

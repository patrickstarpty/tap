import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FlowchartEditor } from "./FlowchartEditor";

it("edits labels, lanes, boxes and directed edges before saving a correction", async () => {
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue(true);
  render(
    <FlowchartEditor
      graph={{
        nodes: [
          { id: "a", label: "开始", lane: "", box: [0, 0, 20, 20] },
          { id: "b", label: "结束", lane: "", box: [30, 30, 50, 50] },
        ],
        edges: [{ source: "a", target: "b", condition: "", certain: false }],
      }}
      pending={false}
      onSave={save}
      onCancel={() => {}}
    />,
  );
  await user.clear(screen.getByLabelText("节点 a 名称"));
  await user.type(screen.getByLabelText("节点 a 名称"), "审批");
  await user.type(screen.getByLabelText("节点 a 泳道"), "财务");
  await user.clear(screen.getByLabelText("节点 a 左边界"));
  await user.type(screen.getByLabelText("节点 a 左边界"), "2");
  await user.click(screen.getByRole("button", { name: "反转连线 1" }));
  await user.type(screen.getByLabelText("连线 1 条件"), "通过");
  await user.click(screen.getByRole("checkbox", { name: "已确认连线 1" }));
  await user.click(screen.getByRole("button", { name: "保存更正并重新索引" }));
  expect(save).toHaveBeenCalledWith({
    nodes: [
      { id: "a", label: "审批", lane: "财务", box: [2, 0, 20, 20] },
      { id: "b", label: "结束", lane: "", box: [30, 30, 50, 50] },
    ],
    edges: [{ source: "b", target: "a", condition: "通过", certain: true }],
  });
});

it("adds missing nodes and arrows and removes incorrect arrows", async () => {
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue(true);
  render(
    <FlowchartEditor
      graph={{
        nodes: [
          { id: "a", label: "开始", lane: "", box: [0, 0, 20, 20] },
          { id: "b", label: "结束", lane: "", box: [30, 30, 50, 50] },
        ],
        edges: [
          { source: "a", target: "b", condition: "wrong", certain: true },
        ],
      }}
      pending={false}
      onSave={save}
      onCancel={() => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: "删除连线 1" }));
  await user.click(screen.getByRole("button", { name: "添加节点" }));
  await user.type(screen.getByLabelText("节点 node_1 名称"), "审批");
  await user.click(screen.getByRole("button", { name: "添加连线" }));
  expect(
    screen.getByRole("checkbox", { name: "已确认连线 1" }),
  ).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "保存更正并重新索引" }));
  expect(save.mock.calls[0]![0].nodes).toHaveLength(3);
  expect(save.mock.calls[0]![0].edges).toEqual([
    { source: "a", target: "b", condition: "", certain: false },
  ]);
});

import {
  Alert,
  Button,
  Checkbox,
  Input,
  InputNumber,
  Select,
  Space,
} from "antd";
import { useState } from "react";
import type { KnowledgeFlowchart } from "../api/types";

export function FlowchartEditor({
  graph,
  pending,
  onSave,
  onCancel,
}: {
  graph: KnowledgeFlowchart;
  pending: boolean;
  onSave: (graph: KnowledgeFlowchart) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(() => structuredClone(graph));
  const valid = draft.nodes.every(
    (node) =>
      node.label.trim() &&
      node.box.length === 4 &&
      node.box.every((value) => Number.isInteger(value) && value >= 0) &&
      node.box[0]! < node.box[2]! &&
      node.box[1]! < node.box[3]!,
  );
  const options = draft.nodes.map((node) => ({
    value: node.id,
    label: `${node.id} · ${node.label}`,
  }));
  const changeNode = (
    index: number,
    change: Partial<KnowledgeFlowchart["nodes"][number]>,
  ) =>
    setDraft((value) => ({
      ...value,
      nodes: value.nodes.map((node, at) =>
        at === index ? { ...node, ...change } : node,
      ),
    }));
  const changeEdge = (
    index: number,
    change: Partial<KnowledgeFlowchart["edges"][number]>,
  ) =>
    setDraft((value) => ({
      ...value,
      edges: value.edges.map((edge, at) =>
        at === index ? { ...edge, ...change } : edge,
      ),
    }));
  const addNode = () =>
    setDraft((value) => {
      let index = 1;
      while (value.nodes.some((node) => node.id === `node_${index}`))
        index += 1;
      return {
        ...value,
        nodes: [
          ...value.nodes,
          { id: `node_${index}`, label: "", lane: "", box: [0, 0, 1, 1] },
        ],
      };
    });
  const removeNode = (id: string) =>
    setDraft((value) => ({
      ...value,
      nodes: value.nodes.filter((node) => node.id !== id),
      edges: value.edges.filter(
        (edge) => edge.source !== id && edge.target !== id,
      ),
    }));
  const addEdge = () =>
    setDraft((value) => ({
      ...value,
      edges: [
        ...value.edges,
        {
          source: value.nodes[0]!.id,
          target: value.nodes[1]?.id ?? value.nodes[0]!.id,
          condition: "",
          certain: false,
        },
      ],
    }));
  return (
    <section aria-label="流程图更正">
      <h4>更正流程图</h4>
      <p>对照原图修改节点和连线。保存后需重新核对和批准。</p>
      <fieldset disabled={pending} style={{ border: 0, padding: 0, margin: 0 }}>
        <legend>节点</legend>
        {draft.nodes.map((node, index) => (
          <fieldset key={node.id} style={{ marginBottom: 12 }}>
            <legend>{node.id}</legend>
            <Button
              aria-label={`删除节点 ${node.id}`}
              disabled={draft.nodes.length <= 1}
              onClick={() => removeNode(node.id)}
            >
              删除节点及其连线
            </Button>
            <Space orientation="vertical" style={{ width: "100%" }}>
              <label>
                名称
                <Input
                  aria-label={`节点 ${node.id} 名称`}
                  maxLength={200}
                  value={node.label}
                  onChange={(event) =>
                    changeNode(index, { label: event.target.value })
                  }
                />
              </label>
              <label>
                泳道
                <Input
                  aria-label={`节点 ${node.id} 泳道`}
                  maxLength={100}
                  value={node.lane}
                  onChange={(event) =>
                    changeNode(index, { lane: event.target.value })
                  }
                />
              </label>
              <Space wrap>
                {["左边界", "上边界", "右边界", "下边界"].map(
                  (name, coordinate) => (
                    <label key={name}>
                      {name}
                      <InputNumber
                        aria-label={`节点 ${node.id} ${name}`}
                        min={0}
                        max={8192}
                        precision={0}
                        value={node.box[coordinate]}
                        onChange={(value) =>
                          changeNode(index, {
                            box: node.box.map((old, at) =>
                              at === coordinate ? (value ?? 0) : old,
                            ),
                          })
                        }
                      />
                    </label>
                  ),
                )}
              </Space>
            </Space>
          </fieldset>
        ))}
        <Button disabled={draft.nodes.length >= 100} onClick={addNode}>
          添加节点
        </Button>
        <h5>连线</h5>
        {draft.edges.map((edge, index) => (
          <fieldset key={index} style={{ marginBottom: 12 }}>
            <legend>连线 {index + 1}</legend>
            <Button
              aria-label={`删除连线 ${index + 1}`}
              onClick={() =>
                setDraft((value) => ({
                  ...value,
                  edges: value.edges.filter((_, at) => at !== index),
                }))
              }
            >
              删除连线
            </Button>
            <Space wrap>
              <label>
                起点
                <Select
                  aria-label={`连线 ${index + 1} 起点`}
                  value={edge.source}
                  options={options}
                  onChange={(source) => changeEdge(index, { source })}
                  style={{ minWidth: 130 }}
                />
              </label>
              <label>
                终点
                <Select
                  aria-label={`连线 ${index + 1} 终点`}
                  value={edge.target}
                  options={options}
                  onChange={(target) => changeEdge(index, { target })}
                  style={{ minWidth: 130 }}
                />
              </label>
              <Button
                aria-label={`反转连线 ${index + 1}`}
                onClick={() =>
                  changeEdge(index, {
                    source: edge.target,
                    target: edge.source,
                  })
                }
              >
                反转方向
              </Button>
            </Space>
            <label>
              条件
              <Input
                aria-label={`连线 ${index + 1} 条件`}
                value={edge.condition}
                maxLength={200}
                onChange={(event) =>
                  changeEdge(index, { condition: event.target.value })
                }
              />
            </label>
            <Checkbox
              checked={edge.certain}
              onChange={(event) =>
                changeEdge(index, { certain: event.target.checked })
              }
            >
              已确认连线 {index + 1}
            </Checkbox>
          </fieldset>
        ))}
        <Button disabled={draft.edges.length >= 200} onClick={addEdge}>
          添加连线
        </Button>
      </fieldset>
      {!valid ? (
        <Alert
          type="error"
          title="请填写节点名称，并确保右边界大于左边界、下边界大于上边界。"
        />
      ) : null}
      <Space wrap>
        <Button
          type="primary"
          loading={pending}
          disabled={!valid || pending}
          onClick={() => void onSave(draft)}
        >
          保存更正并重新索引
        </Button>
        <Button disabled={pending} onClick={onCancel}>
          取消更正
        </Button>
      </Space>
    </section>
  );
}

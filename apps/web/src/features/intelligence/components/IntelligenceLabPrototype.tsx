import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Button, Checkbox, Input } from "antd";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";

import {
  BASIS_COPY,
  BLUEPRINT_STEPS,
  PROTOTYPE_CLAIMS,
  PROTOTYPE_GOAL,
  PROTOTYPE_SOURCES,
  PROTOTYPE_STEPS,
  type ClaimBasis,
  type PrototypeClaim,
} from "./prototypeData";
import "./IntelligenceLabPrototype.css";

type LabPhase = "draft" | "running" | "ready";
type ArtifactKey = "report" | "assumptions" | "blueprint";
type ReviewState = "pending" | "accepted" | "revision" | "rejected";

interface IntelligenceLabPrototypeProps {
  initialPhase?: "draft" | "ready";
}

const ARTIFACT_TABS: readonly {
  key: ArtifactKey;
  label: string;
  mobileLabel: string;
}[] = [
  { key: "report", label: "Intelligence Report", mobileLabel: "报告" },
  {
    key: "assumptions",
    label: "Assumption Register",
    mobileLabel: "假设清单",
  },
  {
    key: "blueprint",
    label: "Automation Blueprint",
    mobileLabel: "自动化蓝图",
  },
];

const ANALYSIS_STEPS = [
  "正在分析目标与约束",
  "正在区分事实、推断与未知",
  "正在设计自动化蓝图",
  "正在执行结构和引用检查",
] as const;

function preferredScrollBehavior(): ScrollBehavior {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ? "auto"
    : "smooth";
}

function claimsForSnapshot(
  selectedSourceIds: ReadonlySet<string>,
): readonly PrototypeClaim[] {
  return PROTOTYPE_CLAIMS.map((claim) => {
    if (claim.sourceId === undefined || selectedSourceIds.has(claim.sourceId)) {
      return claim;
    }
    return {
      id: claim.id,
      statement: claim.statement,
      basis: claim.basis === "evidence" ? "assumption" : claim.basis,
      explanation:
        claim.basis === "evidence"
          ? "相关演示资料未进入本次输入快照，这条规则只能作为待确认假设。"
          : "这项工程建议只依据用户提供的目标与人工步骤，不包含资料引用。",
    };
  });
}

function BasisBadge({ basis }: { basis: ClaimBasis }) {
  return (
    <span className={`intelligence-basis intelligence-basis-${basis}`}>
      {BASIS_COPY[basis]}
    </span>
  );
}

function PrototypeLabel() {
  return (
    <span className="intelligence-prototype-label">交互原型 · 演示数据</span>
  );
}

function BriefComposer({
  goal,
  manualSteps,
  selectedSourceIds,
  onGoalChange,
  onStepChange,
  onAddStep,
  onSourceToggle,
  onClearSources,
  onSubmit,
}: {
  goal: string;
  manualSteps: readonly string[];
  selectedSourceIds: ReadonlySet<string>;
  onGoalChange: (goal: string) => void;
  onStepChange: (index: number, value: string) => void;
  onAddStep: () => void;
  onSourceToggle: (sourceId: string) => void;
  onClearSources: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <section
      className="intelligence-start"
      aria-labelledby="intelligence-lab-heading"
    >
      <header className="intelligence-intro">
        <div>
          <h2 id="intelligence-lab-heading">
            把模糊目标变成可审核的自动化蓝图
          </h2>
          <p>
            从一句目标或几个人工步骤开始。Athena
            会明确区分依据、推断、假设和未知， 然后交付可审核的结构化产物。
          </p>
        </div>
        <PrototypeLabel />
      </header>

      <form className="intelligence-start-grid" onSubmit={onSubmit}>
        <section className="intelligence-brief">
          <div className="intelligence-section-heading">
            <div>
              <h3>描述你想自动化的工作</h3>
              <p>只需要目标；其余上下文可以逐步补充。</p>
            </div>
            <span className="intelligence-required-chip">仅目标必填</span>
          </div>

          <div className="intelligence-field">
            <label htmlFor="intelligence-goal">自动化目标</label>
            <Input.TextArea
              id="intelligence-goal"
              value={goal}
              rows={4}
              maxLength={2_000}
              onChange={(event) => onGoalChange(event.target.value)}
            />
            <small>{goal.length} / 2,000</small>
          </div>

          <fieldset className="intelligence-manual-steps">
            <legend>人工步骤（可选）</legend>
            <p>把你目前知道的操作顺序写下来，不完整也可以。</p>
            <ol>
              {manualSteps.map((step, index) => (
                <li key={index}>
                  <span aria-hidden="true">{index + 1}</span>
                  <Input
                    aria-label={`人工步骤 ${index + 1}`}
                    value={step}
                    onChange={(event) =>
                      onStepChange(index, event.target.value)
                    }
                  />
                </li>
              ))}
            </ol>
            <Button
              type="text"
              aria-label="添加一步"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={onAddStep}
            >
              添加一步
            </Button>
          </fieldset>
        </section>

        <aside className="intelligence-context-picker" aria-label="任务上下文">
          <div className="intelligence-section-heading">
            <div>
              <h3>限定本次输入范围</h3>
              <p>只有你选择的内容可以作为资料依据。</p>
            </div>
          </div>

          <div className="intelligence-context-truth">
            <InfoCircleOutlined aria-hidden="true" />
            <p>Release、需求文档、产品源码和资料都不是必填项。</p>
          </div>

          <div
            className="intelligence-context-facts"
            aria-label="当前缺失的上下文"
          >
            <span>无 Release</span>
            <span>无正式需求</span>
            <span>无产品源码</span>
          </div>

          <fieldset className="intelligence-source-picker">
            <legend>可选资料</legend>
            <Button
              className="intelligence-source-clear"
              type="link"
              size="small"
              disabled={selectedSourceIds.size === 0}
              onClick={onClearSources}
            >
              清空
            </Button>
            <p>{selectedSourceIds.size} 份资料会进入本次不可变输入快照。</p>
            <div className="intelligence-source-list">
              {PROTOTYPE_SOURCES.map((source) => (
                <Checkbox
                  key={source.id}
                  checked={selectedSourceIds.has(source.id)}
                  onChange={() => onSourceToggle(source.id)}
                >
                  <span className="intelligence-source-copy">
                    <strong>{source.title}</strong>
                    <small>{source.detail}</small>
                  </span>
                </Checkbox>
              ))}
            </div>
          </fieldset>

          <p className="intelligence-assumption-note">
            {selectedSourceIds.size === 0
              ? "没有资料时将进入 assumption-first 路径，不会生成伪引用。"
              : "资料只提供依据；它不会自动变成已批准的业务事实。"}
          </p>
        </aside>

        <div className="intelligence-brief-footer">
          <div>
            <strong>系统会先生成草稿</strong>
            <span>
              已选择 {selectedSourceIds.size}{" "}
              份资料；不会发送模型请求、运行浏览器、修改系统或发布测试资产。
            </span>
          </div>
          <Button
            type="primary"
            size="large"
            htmlType="submit"
            aria-label="开始模拟分析"
            disabled={goal.trim().length === 0}
            icon={<ArrowRightOutlined aria-hidden="true" />}
            iconPlacement="end"
          >
            开始模拟分析
          </Button>
        </div>
      </form>
    </section>
  );
}

function RunningWorkspace({
  goal,
  activeStep,
  selectedSourceCount,
}: {
  goal: string;
  activeStep: number;
  selectedSourceCount: number;
}) {
  return (
    <section
      className="intelligence-running"
      aria-labelledby="analysis-heading"
    >
      <div className="intelligence-running-topline">
        <PrototypeLabel />
        <span>Task INT-0248</span>
      </div>
      <div className="intelligence-running-layout">
        <div className="intelligence-orbit" aria-hidden="true">
          <span />
          <SafetyCertificateOutlined />
        </div>
        <div>
          <p className="intelligence-running-status" aria-live="polite">
            {ANALYSIS_STEPS[activeStep]}
          </p>
          <h2 id="analysis-heading" tabIndex={-1}>
            正在把输入整理成可审核产物
          </h2>
          <p className="intelligence-running-goal">{goal}</p>
          <div className="intelligence-running-meta">
            <span>{selectedSourceCount} 份已选资料</span>
            <span>无源码访问</span>
            <span>不会执行目标系统</span>
          </div>
        </div>
      </div>
      <ol className="intelligence-analysis-timeline" aria-label="分析进度">
        {ANALYSIS_STEPS.map((step, index) => {
          const state =
            index < activeStep
              ? "complete"
              : index === activeStep
                ? "active"
                : "waiting";
          return (
            <li key={step} data-state={state}>
              <span className="intelligence-timeline-marker">
                {state === "complete" ? <CheckOutlined /> : index + 1}
              </span>
              <span>{step.replace("正在", "")}</span>
            </li>
          );
        })}
      </ol>
      <p className="intelligence-running-disclosure">
        这是前端原型中的模拟过程，不会发送模型请求或读取真实系统。
      </p>
    </section>
  );
}

function ClaimRow({
  claim,
  selected,
  onSelect,
}: {
  claim: PrototypeClaim;
  selected: boolean;
  onSelect: (claimId: string) => void;
}) {
  return (
    <button
      type="button"
      className="intelligence-claim-row"
      aria-label={`查看依据：${claim.statement}`}
      aria-pressed={selected}
      onClick={() => onSelect(claim.id)}
    >
      <BasisBadge basis={claim.basis} />
      <span className="intelligence-claim-copy">
        <strong>{claim.statement}</strong>
        <small>{claim.explanation}</small>
      </span>
      {selected ? (
        <span className="intelligence-current-basis">正在查看</span>
      ) : (
        <ArrowRightOutlined aria-hidden="true" />
      )}
    </button>
  );
}

function ReportView({
  onSelectClaim,
  claims,
  selectedClaimId,
  selectedSourceCount,
}: {
  onSelectClaim: (claimId: string) => void;
  claims: readonly PrototypeClaim[];
  selectedClaimId: string | null;
  selectedSourceCount: number;
}) {
  return (
    <section
      id="intelligence-panel-report"
      role="tabpanel"
      aria-label="Intelligence Report"
      className="intelligence-artifact"
    >
      <div className="intelligence-artifact-title">
        <div>
          <h2 id="intelligence-review-heading" tabIndex={-1}>
            Intelligence Report
          </h2>
          <p>退款申请自动化的可行性、边界与风险</p>
        </div>
        <span className="intelligence-version">v1 · 已封存</span>
      </div>

      <div className="intelligence-report-summary">
        <SafetyCertificateOutlined aria-hidden="true" />
        <div>
          <strong>可以先设计浏览器流程，但现在还不能承诺它能够执行。</strong>
          <p>
            {selectedSourceCount === 0
              ? "没有选择资料，本次结果只使用用户目标与人工步骤。资料型规则已转为待确认假设。"
              : "当前输入足以定义主路径、审批分支和恢复策略；环境地址、账号、稳定定位方式与真实结果仍未知。"}
          </p>
        </div>
      </div>

      <section className="intelligence-report-section">
        <h3>关键结论</h3>
        <p>点击任一结论，核对它的依据与限制。</p>
        <div className="intelligence-claim-list">
          {claims.map((claim) => (
            <ClaimRow
              key={claim.id}
              claim={claim}
              selected={selectedClaimId === claim.id}
              onSelect={onSelectClaim}
            />
          ))}
        </div>
      </section>

      <section className="intelligence-next-actions">
        <h3>进入真实自动化前还需要</h3>
        <ul>
          <li>确认测试环境、测试账号和可退款订单数据。</li>
          <li>观察真实页面结构，再决定页面定位方式与等待策略。</li>
          <li>由人工审核阈值规则和异常接管条件。</li>
        </ul>
      </section>
    </section>
  );
}

function AssumptionView({
  onSelectClaim,
  claims,
  selectedClaimId,
}: {
  onSelectClaim: (claimId: string) => void;
  claims: readonly PrototypeClaim[];
  selectedClaimId: string | null;
}) {
  const assumptions = claims.filter(
    (claim) => claim.basis === "assumption" || claim.basis === "unknown",
  );
  const assumptionCount = assumptions.filter(
    (claim) => claim.basis === "assumption",
  ).length;
  const unknownCount = assumptions.filter(
    (claim) => claim.basis === "unknown",
  ).length;
  return (
    <section
      id="intelligence-panel-assumptions"
      role="tabpanel"
      aria-label="Assumption Register"
      className="intelligence-artifact"
    >
      <div className="intelligence-artifact-title">
        <div>
          <h2>Assumption Register</h2>
          <p>系统没有偷偷补齐的信息，都在这里等待确认。</p>
        </div>
        <span className="intelligence-version">
          {assumptions.length} 项待处理
        </span>
      </div>

      <div className="intelligence-assumption-summary">
        <div>
          <strong>{assumptionCount}</strong>
          <span>待确认假设</span>
        </div>
        <div>
          <strong>{unknownCount}</strong>
          <span>未知项</span>
        </div>
        <div>
          <strong>0</strong>
          <span>输入冲突</span>
        </div>
      </div>

      <section className="intelligence-report-section">
        <h3>需要人工回答</h3>
        <p>这些问题不会阻止生成草稿，但会阻止系统声称可执行。</p>
        <div className="intelligence-claim-list">
          {assumptions.map((claim) => (
            <ClaimRow
              key={claim.id}
              claim={claim}
              selected={selectedClaimId === claim.id}
              onSelect={onSelectClaim}
            />
          ))}
        </div>
      </section>

      <div className="intelligence-confirmation-callout">
        <strong>建议一次确认两个问题</strong>
        <p>测试账号能看到什么数据？真实页面是否提供可用于自动化的稳定标识？</p>
      </div>
    </section>
  );
}

function BlueprintView({
  onSelectClaim,
  claims,
  selectedClaimId,
}: {
  onSelectClaim: (claimId: string) => void;
  claims: readonly PrototypeClaim[];
  selectedClaimId: string | null;
}) {
  return (
    <section
      id="intelligence-panel-blueprint"
      role="tabpanel"
      aria-label="Automation Blueprint"
      className="intelligence-artifact"
    >
      <div className="intelligence-artifact-title">
        <div>
          <h2>退款申请流程自动化蓝图</h2>
          <p>面向浏览器流程的可审核草稿，不绑定具体框架。</p>
        </div>
        <span className="intelligence-execution-badge">未执行</span>
      </div>

      <div className="intelligence-blueprint-facts" aria-label="蓝图摘要">
        <span>
          <strong>类型</strong> Web 流程
        </span>
        <span>
          <strong>步骤</strong> 7
        </span>
        <span>
          <strong>断言</strong> 2
        </span>
        <span>
          <strong>执行环境</strong> 未指定
        </span>
      </div>

      <section className="intelligence-blueprint-section">
        <h3>流程步骤</h3>
        <ol className="intelligence-blueprint-steps">
          {BLUEPRINT_STEPS.map((step, index) => {
            const claim = claims.find(
              (candidate) => candidate.id === step.claimId,
            );
            return (
              <li key={step.key}>
                <span className="intelligence-step-index">{index + 1}</span>
                <span className="intelligence-step-kind">{step.kind}</span>
                <div>
                  <strong>{step.title}</strong>
                  <p>{step.detail}</p>
                  {claim === undefined ? null : (
                    <button
                      type="button"
                      aria-label={`查看“${step.title}”的依据：${claim.statement}`}
                      aria-pressed={selectedClaimId === claim.id}
                      onClick={() => onSelectClaim(claim.id)}
                    >
                      <BasisBadge basis={claim.basis} />
                      <span>
                        {selectedClaimId === claim.id
                          ? "正在查看依据"
                          : "查看依据"}
                      </span>
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="intelligence-blueprint-section intelligence-blueprint-boundary">
        <h3>安全与恢复边界</h3>
        <div>
          <p>
            <strong>禁止：</strong>状态不明时重复提交退款。
          </p>
          <p>
            <strong>恢复：</strong>
            保留申请编号、页面状态和最后成功步骤，转人工处理。
          </p>
          <p>
            <strong>清理：</strong>退出测试账号，不自动删除或回滚业务记录。
          </p>
        </div>
      </section>
    </section>
  );
}

function EvidencePanel({
  selectedClaim,
  selectedSourceCount,
  evidenceRef,
}: {
  selectedClaim: PrototypeClaim | null;
  selectedSourceCount: number;
  evidenceRef: RefObject<HTMLElement | null>;
}) {
  const selectedSource = PROTOTYPE_SOURCES.find(
    (source) => source.id === selectedClaim?.sourceId,
  );
  return (
    <aside
      ref={evidenceRef}
      className="intelligence-evidence"
      aria-live="polite"
      tabIndex={-1}
    >
      <div className="intelligence-evidence-heading">
        <h3>{selectedClaim === null ? "依据与限制" : "这条结论的依据"}</h3>
        <span>{selectedSourceCount} 份资料</span>
      </div>

      {selectedClaim === null ? (
        <div className="intelligence-evidence-empty">
          <FileTextOutlined aria-hidden="true" />
          <strong>选择一条结论或蓝图步骤</strong>
          <p>这里会显示它使用的资料、推断方式以及尚未解决的限制。</p>
        </div>
      ) : (
        <div className="intelligence-evidence-detail">
          <BasisBadge basis={selectedClaim.basis} />
          <strong>{selectedClaim.statement}</strong>
          <p>{selectedClaim.explanation}</p>
          {selectedClaim.sourceLabel === undefined ? (
            <div className="intelligence-no-citation">
              <InfoCircleOutlined aria-hidden="true" />
              <span>没有资料引用；此项必须由人工确认。</span>
            </div>
          ) : (
            <>
              <div className="intelligence-source-reference">
                <span>来自已选择资料 · {selectedClaim.sourceLabel}</span>
                <small>{selectedClaim.sourceLocation}</small>
              </div>
              <blockquote>{selectedClaim.quote}</blockquote>
              <dl className="intelligence-revision-facts">
                <div>
                  <dt>资料版本</dt>
                  <dd>{selectedSource?.revision ?? "版本未知"}</dd>
                </div>
                <div>
                  <dt>输入快照</dt>
                  <dd>ctx_8c4f…91a2</dd>
                </div>
              </dl>
            </>
          )}
        </div>
      )}

      <section className="intelligence-validation">
        <h4>确定性检查</h4>
        <ul>
          <li>
            <CheckOutlined aria-hidden="true" /> 结构符合原型字段规则
          </li>
          <li>
            <CheckOutlined aria-hidden="true" /> 内部引用可以解析
          </li>
          <li>
            <ClockCircleOutlined aria-hidden="true" /> 真实执行证据不存在
          </li>
        </ul>
        <div className="intelligence-execution-truth">
          <span>执行状态</span>
          <strong>未执行</strong>
        </div>
      </section>
    </aside>
  );
}

function ReviewActions({
  reviewState,
  revisionText,
  revisionSubmitted,
  onReviewStateChange,
  onRevisionTextChange,
  onRevisionSubmit,
  onReopen,
}: {
  reviewState: ReviewState;
  revisionText: string;
  revisionSubmitted: boolean;
  onReviewStateChange: (state: ReviewState) => void;
  onRevisionTextChange: (value: string) => void;
  onRevisionSubmit: () => void;
  onReopen: () => void;
}) {
  const terminal = reviewState === "accepted" || reviewState === "rejected";
  return (
    <footer className="intelligence-review-actions">
      <div>
        <span>Review Package · rp_0248_v1</span>
        <strong role="status">
          {reviewState === "pending" && "等待人工审核"}
          {reviewState === "revision" &&
            (revisionSubmitted ? "已记录修订要求" : "正在填写修订要求")}
          {reviewState === "accepted" && "原型结果已接受"}
          {reviewState === "rejected" && "原型结果已拒绝"}
        </strong>
      </div>

      {terminal ? (
        <div className="intelligence-review-buttons">
          <Button onClick={onReopen}>
            {reviewState === "accepted" ? "撤销接受" : "重新打开审核"}
          </Button>
        </div>
      ) : reviewState === "revision" ? (
        <div className="intelligence-revision-form">
          <label htmlFor="intelligence-revision">修订要求</label>
          <Input.TextArea
            id="intelligence-revision"
            rows={2}
            placeholder="说明需要补充或修改什么"
            value={revisionText}
            onChange={(event) => onRevisionTextChange(event.target.value)}
          />
          <div className="intelligence-revision-buttons">
            <Button onClick={onReopen}>取消</Button>
            <Button
              type="primary"
              disabled={revisionText.trim().length === 0 || revisionSubmitted}
              onClick={onRevisionSubmit}
            >
              提交修订要求
            </Button>
          </div>
        </div>
      ) : (
        <div className="intelligence-review-buttons">
          <Button onClick={() => onReviewStateChange("rejected")}>拒绝</Button>
          <Button onClick={() => onReviewStateChange("revision")}>
            要求修订
          </Button>
          <Button
            type="primary"
            icon={<CheckOutlined aria-hidden="true" />}
            onClick={() => onReviewStateChange("accepted")}
          >
            接受草稿
          </Button>
        </div>
      )}
    </footer>
  );
}

function ReviewWorkspace({
  goal,
  selectedSourceIds,
  onReset,
}: {
  goal: string;
  selectedSourceIds: ReadonlySet<string>;
  onReset: () => void;
}) {
  const [artifact, setArtifact] = useState<ArtifactKey>("report");
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [reviewState, setReviewState] = useState<ReviewState>("pending");
  const [revisionText, setRevisionText] = useState("");
  const [revisionSubmitted, setRevisionSubmitted] = useState(false);
  const evidenceRef = useRef<HTMLElement>(null);
  const claims = useMemo(
    () => claimsForSnapshot(selectedSourceIds),
    [selectedSourceIds],
  );
  const selectedClaim =
    claims.find((claim) => claim.id === selectedClaimId) ?? null;
  const taskReviewLabel =
    reviewState === "accepted"
      ? "草稿已接受"
      : reviewState === "rejected"
        ? "草稿已拒绝"
        : reviewState === "revision"
          ? revisionSubmitted
            ? "修订要求已记录"
            : "正在要求修订"
          : "等待人工审核";
  const timelineReviewLabel =
    reviewState === "accepted"
      ? "审核决定：已接受"
      : reviewState === "rejected"
        ? "审核决定：已拒绝"
        : reviewState === "revision"
          ? revisionSubmitted
            ? "修订要求已记录"
            : "正在填写修订要求"
          : "等待人工审核";
  const reviewTimelineState =
    reviewState === "accepted" || reviewState === "rejected"
      ? "complete"
      : "active";

  const chooseArtifact = (nextArtifact: ArtifactKey) => {
    setArtifact(nextArtifact);
    setSelectedClaimId(null);
  };

  const selectClaim = (claimId: string) => {
    setSelectedClaimId(claimId);
    if (window.innerWidth <= 720) {
      window.requestAnimationFrame(() => {
        evidenceRef.current?.focus({ preventScroll: true });
        evidenceRef.current?.scrollIntoView?.({
          behavior: preferredScrollBehavior(),
          block: "start",
        });
      });
    }
  };

  const handleArtifactKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") {
      nextIndex = (index + 1) % ARTIFACT_TABS.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + ARTIFACT_TABS.length) % ARTIFACT_TABS.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = ARTIFACT_TABS.length - 1;
    }
    if (nextIndex === null) return;

    event.preventDefault();
    chooseArtifact(ARTIFACT_TABS[nextIndex].key);
    const tabs =
      event.currentTarget.parentElement?.querySelectorAll<HTMLElement>(
        '[role="tab"]',
      );
    tabs?.[nextIndex]?.focus();
  };

  return (
    <section
      className="intelligence-workspace"
      aria-label="Intelligence 任务工作区"
    >
      <header className="intelligence-task-header">
        <Button
          type="text"
          aria-label="新建任务"
          icon={<ArrowLeftOutlined aria-hidden="true" />}
          onClick={onReset}
        >
          新建任务
        </Button>
        <div>
          <span>INT-0248 · 浏览器流程设计</span>
          <strong>{goal}</strong>
        </div>
        <div
          className="intelligence-task-state"
          data-review-state={reviewState}
          aria-label="任务审核状态"
        >
          <span className="intelligence-ready-dot" aria-hidden="true" />
          {taskReviewLabel}
        </div>
        <PrototypeLabel />
      </header>

      <div className="intelligence-workspace-grid">
        <aside className="intelligence-task-rail">
          <section>
            <h3>任务范围</h3>
            <dl>
              <div>
                <dt>资料</dt>
                <dd>{selectedSourceIds.size} 份已固定</dd>
              </div>
              <div>
                <dt>源码</dt>
                <dd>未提供</dd>
              </div>
              <div>
                <dt>环境</dt>
                <dd>未连接</dd>
              </div>
              <div>
                <dt>动作</dt>
                <dd>仅生成草稿</dd>
              </div>
            </dl>
          </section>

          <section>
            <h3>任务时间线</h3>
            <ol className="intelligence-task-timeline">
              <li data-state="complete">
                <CheckOutlined />
                <span>
                  <strong>输入快照已固定</strong>
                  <small>21:10</small>
                </span>
              </li>
              <li data-state="complete">
                <CheckOutlined />
                <span>
                  <strong>分析产物已生成</strong>
                  <small>21:10</small>
                </span>
              </li>
              <li data-state="complete">
                <CheckOutlined />
                <span>
                  <strong>结构与引用已检查</strong>
                  <small>21:10</small>
                </span>
              </li>
              <li
                data-state={reviewTimelineState}
                data-review-state={reviewState}
              >
                {reviewState === "accepted" ? (
                  <CheckOutlined aria-hidden="true" />
                ) : reviewState === "rejected" ? (
                  <CloseCircleOutlined aria-hidden="true" />
                ) : (
                  <ClockCircleOutlined aria-hidden="true" />
                )}
                <span>
                  <strong>{timelineReviewLabel}</strong>
                  <small>当前</small>
                </span>
              </li>
            </ol>
          </section>

          <section className="intelligence-input-snapshot">
            <h3>输入快照</h3>
            <p>ctx_8c4f…91a2</p>
            <span>目标 + 人工步骤 + {selectedSourceIds.size} 份资料</span>
          </section>
        </aside>

        <div className="intelligence-artifact-workspace">
          <div
            className="intelligence-artifact-tabs"
            role="tablist"
            aria-label="任务产物"
          >
            {ARTIFACT_TABS.map((tab, index) => (
              <button
                key={tab.key}
                id={`intelligence-tab-${tab.key}`}
                type="button"
                role="tab"
                aria-label={tab.label}
                aria-selected={artifact === tab.key}
                aria-controls={`intelligence-panel-${tab.key}`}
                tabIndex={artifact === tab.key ? 0 : -1}
                onClick={() => chooseArtifact(tab.key)}
                onKeyDown={(event) => handleArtifactKeyDown(event, index)}
              >
                <span className="intelligence-tab-label" aria-hidden="true">
                  {tab.label}
                </span>
                <span
                  className="intelligence-tab-label-mobile"
                  aria-hidden="true"
                >
                  {tab.mobileLabel}
                </span>
              </button>
            ))}
          </div>
          {artifact === "report" ? (
            <ReportView
              claims={claims}
              selectedClaimId={selectedClaimId}
              selectedSourceCount={selectedSourceIds.size}
              onSelectClaim={selectClaim}
            />
          ) : null}
          {artifact === "assumptions" ? (
            <AssumptionView
              claims={claims}
              selectedClaimId={selectedClaimId}
              onSelectClaim={selectClaim}
            />
          ) : null}
          {artifact === "blueprint" ? (
            <BlueprintView
              claims={claims}
              selectedClaimId={selectedClaimId}
              onSelectClaim={selectClaim}
            />
          ) : null}
        </div>

        <EvidencePanel
          evidenceRef={evidenceRef}
          selectedClaim={selectedClaim}
          selectedSourceCount={selectedSourceIds.size}
        />

        <ReviewActions
          reviewState={reviewState}
          revisionText={revisionText}
          revisionSubmitted={revisionSubmitted}
          onRevisionTextChange={(value) => {
            setRevisionText(value);
            setRevisionSubmitted(false);
          }}
          onRevisionSubmit={() => setRevisionSubmitted(true)}
          onReopen={() => {
            setReviewState("pending");
            setRevisionSubmitted(false);
          }}
          onReviewStateChange={(nextState) => {
            setReviewState(nextState);
            if (nextState === "revision") setRevisionSubmitted(false);
          }}
        />
      </div>
    </section>
  );
}

export function IntelligenceLabPrototype({
  initialPhase = "draft",
}: IntelligenceLabPrototypeProps = {}) {
  const [phase, setPhase] = useState<LabPhase>(initialPhase);
  const [goal, setGoal] = useState(PROTOTYPE_GOAL);
  const [manualSteps, setManualSteps] = useState<string[]>([
    ...PROTOTYPE_STEPS,
  ]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(
    () =>
      new Set(
        PROTOTYPE_SOURCES.filter((source) => source.selected).map(
          (source) => source.id,
        ),
      ),
  );
  const [activeAnalysisStep, setActiveAnalysisStep] = useState(0);

  useEffect(() => {
    if (phase !== "running") return;
    const timers = [
      window.setTimeout(() => setActiveAnalysisStep(1), 420),
      window.setTimeout(() => setActiveAnalysisStep(2), 820),
      window.setTimeout(() => setActiveAnalysisStep(3), 1_220),
      window.setTimeout(() => setPhase("ready"), 1_650),
    ];
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [phase]);

  useEffect(() => {
    if (phase === "draft") return;
    const frame = window.requestAnimationFrame(() => {
      const heading = document.getElementById(
        phase === "running"
          ? "analysis-heading"
          : "intelligence-review-heading",
      );
      heading?.focus({ preventScroll: true });
      heading?.scrollIntoView?.({
        behavior: preferredScrollBehavior(),
        block: "start",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [phase]);

  const selectedSources = useMemo(
    () =>
      PROTOTYPE_SOURCES.filter((source) => selectedSourceIds.has(source.id)),
    [selectedSourceIds],
  );

  if (phase === "running") {
    return (
      <RunningWorkspace
        goal={goal}
        activeStep={activeAnalysisStep}
        selectedSourceCount={selectedSources.length}
      />
    );
  }

  if (phase === "ready") {
    return (
      <ReviewWorkspace
        goal={goal}
        selectedSourceIds={selectedSourceIds}
        onReset={() => {
          setPhase("draft");
          setActiveAnalysisStep(0);
        }}
      />
    );
  }

  return (
    <BriefComposer
      goal={goal}
      manualSteps={manualSteps}
      selectedSourceIds={selectedSourceIds}
      onGoalChange={setGoal}
      onStepChange={(index, value) =>
        setManualSteps((current) =>
          current.map((step, stepIndex) =>
            stepIndex === index ? value : step,
          ),
        )
      }
      onAddStep={() => setManualSteps((current) => [...current, ""])}
      onSourceToggle={(sourceId) =>
        setSelectedSourceIds((current) => {
          const next = new Set(current);
          if (next.has(sourceId)) next.delete(sourceId);
          else next.add(sourceId);
          return next;
        })
      }
      onClearSources={() => setSelectedSourceIds(new Set())}
      onSubmit={(event) => {
        event.preventDefault();
        if (goal.trim().length === 0) return;
        setActiveAnalysisStep(0);
        setPhase("running");
      }}
    />
  );
}

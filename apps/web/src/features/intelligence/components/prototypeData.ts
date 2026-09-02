export type ClaimBasis = "evidence" | "inference" | "assumption" | "unknown";

export interface PrototypeSource {
  id: string;
  title: string;
  detail: string;
  revision: string;
  selected: boolean;
}

export interface PrototypeClaim {
  id: string;
  basis: ClaimBasis;
  statement: string;
  explanation: string;
  sourceId?: string;
  sourceLabel?: string;
  sourceLocation?: string;
  quote?: string;
}

export const PROTOTYPE_GOAL =
  "为供应商门户设计退款申请流程自动化，覆盖提交、复核和异常恢复。";

export const PROTOTYPE_STEPS = [
  "登录供应商门户并打开已完成订单。",
  "进入退款申请，填写退款原因和金额。",
  "提交申请；高金额订单等待人工复核。",
  "记录最终状态，失败时保留页面信息。",
] as const;

export const PROTOTYPE_SOURCES: readonly PrototypeSource[] = [
  {
    id: "refund-manual",
    title: "退款运营手册 v2.3",
    detail: "PDF · 18 页 · 业务规则",
    revision: "rev_2026_08_18",
    selected: true,
  },
  {
    id: "support-exceptions",
    title: "客服异常处理清单",
    detail: "DOCX · 7 页 · 异常路径",
    revision: "rev_2026_08_27",
    selected: true,
  },
  {
    id: "portal-fields",
    title: "供应商门户字段说明",
    detail: "MD · 31 行 · 页面字段",
    revision: "rev_2026_08_31",
    selected: false,
  },
] as const;

export const PROTOTYPE_CLAIMS: readonly PrototypeClaim[] = [
  {
    id: "claim-entry",
    basis: "evidence",
    statement: "退款申请从已完成订单的详情页发起，并要求填写退款原因。",
    explanation: "这条业务规则可以直接进入自动化步骤和表单断言。",
    sourceId: "refund-manual",
    sourceLabel: "退款运营手册 v2.3",
    sourceLocation: "第 4 页 · 退款申请条件",
    quote: "已完成订单可在订单详情中发起退款；提交前必须选择退款原因。",
  },
  {
    id: "claim-review",
    basis: "evidence",
    statement: "退款金额超过 5,000 元时转人工复核",
    explanation: "蓝图应把人工复核建模为可等待、可恢复的状态，而不是立即通过。",
    sourceId: "refund-manual",
    sourceLabel: "退款运营手册 v2.3",
    sourceLocation: "第 6 页 · 审批阈值",
    quote: "单笔退款金额大于 5,000 元时，申请状态进入待人工复核。",
  },
  {
    id: "claim-recovery",
    basis: "inference",
    statement: "审批等待适合拆成可恢复的状态检查节点。",
    explanation:
      "这是根据人工步骤和异常清单形成的工程建议，不是来源中的原始业务规则。",
    sourceId: "support-exceptions",
    sourceLabel: "客服异常处理清单",
    sourceLocation: "第 3 页 · 长时间待处理",
    quote: "超过处理时限的申请由客服接管，并保留原申请编号。",
  },
  {
    id: "claim-account",
    basis: "assumption",
    statement: "测试账号能够访问至少一笔可退款的已完成订单。",
    explanation: "当前没有账号、环境或测试数据说明，执行前需要人工确认。",
  },
  {
    id: "claim-selector",
    basis: "unknown",
    statement: "门户是否提供稳定的元素标识仍未知。",
    explanation: "没有源码、DOM 快照或可访问测试环境，因此不能选择定位策略。",
  },
] as const;

export const BLUEPRINT_STEPS = [
  {
    key: "open",
    kind: "动作",
    title: "打开供应商门户",
    detail: "进入受控测试环境，确认登录页可访问。",
    claimId: "claim-account",
  },
  {
    key: "authenticate",
    kind: "前置",
    title: "登录测试账号",
    detail: "使用获批测试账号完成登录，不在蓝图中保存凭据。",
    claimId: "claim-account",
  },
  {
    key: "locate-order",
    kind: "动作",
    title: "定位一笔已完成订单",
    detail: "打开订单详情并确认退款入口可用。",
    claimId: "claim-entry",
  },
  {
    key: "compose",
    kind: "动作",
    title: "填写退款信息",
    detail: "输入原因、金额，并保留申请前的订单状态。",
    claimId: "claim-entry",
  },
  {
    key: "submit",
    kind: "动作",
    title: "提交退款申请",
    detail: "提交后记录申请编号、提示文本和页面状态。",
    claimId: "claim-review",
  },
  {
    key: "assert",
    kind: "断言",
    title: "校验审批分支",
    detail: "金额超过 5,000 元时应显示“待人工复核”。",
    claimId: "claim-review",
  },
  {
    key: "recover",
    kind: "恢复",
    title: "保存失败现场并交还人工",
    detail: "超时或状态不明时停止重复提交，保留申请编号与页面信息。",
    claimId: "claim-recovery",
  },
] as const;

export const BASIS_COPY: Readonly<Record<ClaimBasis, string>> = {
  evidence: "有依据",
  inference: "工程推断",
  assumption: "待确认假设",
  unknown: "未知",
};

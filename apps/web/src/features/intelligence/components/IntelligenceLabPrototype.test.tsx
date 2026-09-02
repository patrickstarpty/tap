import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderApp } from "../../../shared/testing/renderApp";
import { IntelligenceLabPrototype } from "./IntelligenceLabPrototype";

describe("IntelligenceLabPrototype", () => {
  it("starts from a goal while keeping release, requirements, source code, and sources optional", () => {
    renderApp(<IntelligenceLabPrototype />);

    expect(screen.getByRole("textbox", { name: "自动化目标" })).toHaveValue(
      "为供应商门户设计退款申请流程自动化，覆盖提交、复核和异常恢复。",
    );
    expect(
      screen.getByText("Release、需求文档、产品源码和资料都不是必填项。"),
    ).toBeVisible();
    expect(
      within(screen.getByRole("group", { name: "可选资料" })).getAllByRole(
        "checkbox",
      ),
    ).toHaveLength(3);
    const sourcePicker = screen.getByRole("group", { name: "可选资料" });
    const submit = screen.getByRole("button", { name: "开始模拟分析" });
    expect(
      sourcePicker.compareDocumentPosition(submit) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByText(/已选择 2 份资料；不会发送模型请求/)).toBeVisible();
    expect(submit).toBeEnabled();
  });

  it("turns the brief into a review-ready task after a visible analysis sequence", async () => {
    const user = userEvent.setup();
    renderApp(<IntelligenceLabPrototype />);

    await user.click(screen.getByRole("button", { name: "开始模拟分析" }));
    expect(screen.getByText("正在分析目标与约束")).toBeVisible();

    expect(
      await screen.findByRole(
        "heading",
        { name: "Intelligence Report" },
        { timeout: 2_500 },
      ),
    ).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("等待人工审核");
  });

  it("uses an assumption-first result without inventing citations when no source is selected", async () => {
    const user = userEvent.setup();
    renderApp(<IntelligenceLabPrototype />);

    await user.click(screen.getByRole("button", { name: "清空" }));
    expect(
      screen.getByText(
        "没有资料时将进入 assumption-first 路径，不会生成伪引用。",
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "开始模拟分析" }));

    const report = await screen.findByRole(
      "tabpanel",
      { name: "Intelligence Report" },
      { timeout: 2_500 },
    );
    expect(
      within(report).getByText(
        "没有选择资料，本次结果只使用用户目标与人工步骤。资料型规则已转为待确认假设。",
      ),
    ).toBeVisible();
    expect(within(report).queryByText("有依据")).not.toBeInTheDocument();
    expect(screen.queryByText(/来自已选择资料/)).not.toBeInTheDocument();
  });

  it("connects blueprint steps, claim basis, execution truth, and review actions", async () => {
    const user = userEvent.setup();
    renderApp(<IntelligenceLabPrototype initialPhase="ready" />);

    await user.click(screen.getByRole("tab", { name: "Automation Blueprint" }));
    const blueprint = screen.getByRole("tabpanel", {
      name: "Automation Blueprint",
    });
    expect(
      within(blueprint).getByRole("heading", {
        name: "退款申请流程自动化蓝图",
      }),
    ).toBeVisible();
    expect(within(blueprint).getByText("未执行")).toBeVisible();
    expect(within(blueprint).getByText("提交退款申请")).toBeVisible();

    await user.click(
      within(blueprint).getByRole("button", {
        name: "查看“提交退款申请”的依据：退款金额超过 5,000 元时转人工复核",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "这条结论的依据" }),
    ).toBeVisible();
    expect(
      screen.getByText("来自已选择资料 · 退款运营手册 v2.3"),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "要求修订" }));
    await user.type(
      screen.getByRole("textbox", { name: "修订要求" }),
      "补充超时后的人工接管步骤",
    );
    await user.click(screen.getByRole("button", { name: "提交修订要求" }));

    expect(screen.getByText("已记录修订要求")).toBeVisible();
  });

  it("shows the immutable revision of the source used by each claim", async () => {
    const user = userEvent.setup();
    renderApp(<IntelligenceLabPrototype initialPhase="ready" />);

    await user.click(
      screen.getByRole("button", {
        name: "查看依据：审批等待适合拆成可恢复的状态检查节点。",
      }),
    );

    expect(screen.getByText("来自已选择资料 · 客服异常处理清单")).toBeVisible();
    expect(screen.getByText("rev_2026_08_27")).toBeVisible();
  });

  it("keeps review status consistent and lets a reviewer reopen a decision", async () => {
    const user = userEvent.setup();
    renderApp(<IntelligenceLabPrototype initialPhase="ready" />);

    await user.click(screen.getByRole("button", { name: "接受草稿" }));
    expect(screen.getByLabelText("任务审核状态")).toHaveTextContent(
      "草稿已接受",
    );
    expect(screen.getByRole("status")).toHaveTextContent("原型结果已接受");
    expect(screen.getByText("审核决定：已接受")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "撤销接受" }));
    expect(screen.getByLabelText("任务审核状态")).toHaveTextContent(
      "等待人工审核",
    );
    expect(screen.getByRole("button", { name: "接受草稿" })).toBeEnabled();
  });

  it("supports arrow-key navigation across artifact tabs", async () => {
    const user = userEvent.setup();
    renderApp(<IntelligenceLabPrototype initialPhase="ready" />);

    const reportTab = screen.getByRole("tab", { name: "Intelligence Report" });
    const assumptionsTab = screen.getByRole("tab", {
      name: "Assumption Register",
    });
    reportTab.focus();
    await user.keyboard("{ArrowRight}");

    expect(assumptionsTab).toHaveFocus();
    expect(assumptionsTab).toHaveAttribute("aria-selected", "true");
    expect(reportTab).toHaveAttribute("tabindex", "-1");
  });

  it("moves focus to each new phase and respects reduced-motion scrolling", async () => {
    const user = userEvent.setup();
    const originalScrollIntoView = Element.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    const matchMedia = vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    });
    Element.prototype.scrollIntoView = scrollIntoView;

    try {
      renderApp(<IntelligenceLabPrototype />);
      await user.click(screen.getByRole("button", { name: "开始模拟分析" }));

      const runningHeading = screen.getByRole("heading", {
        name: "正在把输入整理成可审核产物",
      });
      await waitFor(() => expect(runningHeading).toHaveFocus());
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: "auto",
        block: "start",
      });

      const reportHeading = await screen.findByRole(
        "heading",
        { name: "Intelligence Report" },
        { timeout: 2_500 },
      );
      await waitFor(() => expect(reportHeading).toHaveFocus());
    } finally {
      matchMedia.mockRestore();
      Element.prototype.scrollIntoView = originalScrollIntoView;
    }
  });
});

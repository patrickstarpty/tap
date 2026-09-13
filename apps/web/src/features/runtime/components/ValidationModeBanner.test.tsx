import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ValidationModeBanner } from "./ValidationModeBanner";

describe("ValidationModeBanner", () => {
  it("persistently explains the fixed actor without offering dismissal", () => {
    const { rerender } = render(<ValidationModeBanner state="ready" />);
    const banner = screen.getByRole("status", { name: "Validation Mode" });
    expect(banner).toHaveTextContent(
      "操作统一记录到固定 Validation Actor，不代表个人身份",
    );
    expect(within(banner).queryByRole("button")).not.toBeInTheDocument();
    rerender(<ValidationModeBanner state="ready" />);
    expect(banner).toBeVisible();
  });

  it("does not claim a resolved identity while connecting or unavailable", () => {
    const { rerender } = render(<ValidationModeBanner state="connecting" />);
    expect(screen.getByRole("status")).toHaveTextContent("正在连接运行环境");
    expect(screen.queryByText(/操作统一记录/)).not.toBeInTheDocument();
    rerender(<ValidationModeBanner state="unavailable" />);
    expect(screen.getByRole("status")).toHaveTextContent("运行环境连接失败");
    expect(screen.getByRole("status")).toHaveTextContent("服务器操作暂不可用");
  });
});

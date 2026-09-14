import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ValidationModeBanner } from "./ValidationModeBanner";

describe("ValidationModeBanner", () => {
  it("stays out of the primary workspace when the runtime is ready", () => {
    render(<ValidationModeBanner state="ready" />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not claim a resolved identity while connecting or unavailable", () => {
    const { rerender } = render(<ValidationModeBanner state="connecting" />);
    expect(screen.getByRole("status")).toHaveTextContent("正在连接运行环境");
    expect(screen.queryByText(/操作统一记录/)).not.toBeInTheDocument();
    rerender(<ValidationModeBanner state="unavailable" />);
    expect(screen.getByRole("status")).toHaveTextContent("运行环境连接失败");
    expect(screen.getByRole("status")).toHaveTextContent(
      "当前无法使用服务器功能",
    );
  });
});

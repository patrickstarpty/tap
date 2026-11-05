import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  answerResponse,
  retrievalCitation,
} from "../testing/fakeKnowledgeClient";
import { GroundedAnswer } from "./GroundedAnswer";

// The app stylesheet sits on Tailwind's preflight, which resets every list to
// `list-style: none` with no padding; load both, in app order, into jsdom.
// Resolved through the package's own `exports` and this file's location, so the
// test does not depend on the working directory or node_modules layout.
const preflight = readFileSync(
  createRequire(import.meta.url).resolve("tailwindcss/preflight.css"),
  "utf8",
);
const appStyles = readFileSync(
  join(import.meta.dirname, "../../../app/styles.css"),
  "utf8",
).replace(/^@import\s+"tailwindcss";/mu, "");

function mountStyles(): HTMLStyleElement {
  const style = document.createElement("style");
  style.textContent = `${preflight}\n${appStyles}`;
  document.head.append(style);
  return style;
}

let mounted: HTMLStyleElement | null = null;
afterEach(() => {
  mounted?.remove();
  mounted = null;
});

describe("GroundedAnswer Markdown lists", () => {
  it("keeps the numbers of a numbered list and the bullets of a bulleted list", () => {
    mounted = mountStyles();
    const markdown =
      "验证步骤如下：\n\n1. 登录系统，输入验证码123456。\n2. 申请贷款，金额不超过80%。\n\n- 备注一\n- 备注二";
    const { container } = render(
      <GroundedAnswer
        response={answerResponse({
          answer: markdown,
          claims: [
            {
              claimId: "claim-steps",
              text: "登录系统，输入验证码123456。",
              answerStart: Array.from("验证步骤如下：\n\n1. ").length,
              answerEnd: Array.from(
                "验证步骤如下：\n\n1. 登录系统，输入验证码123456。",
              ).length,
              citationIds: ["citation-a"],
            },
          ],
          citations: [retrievalCitation("citation-a")],
        })}
        onOpenCitation={() => undefined}
      />,
    );

    const ordered = container.querySelector(".tapper-markdown ol");
    const bulleted = container.querySelector(".tapper-markdown ul");
    expect(ordered?.querySelectorAll("li")).toHaveLength(2);
    // jsdom does not expand the `list-style` shorthand, so read it directly.
    expect(getComputedStyle(ordered!).listStyle).toMatch(/\bdecimal\b/u);
    expect(getComputedStyle(bulleted!).listStyle).toMatch(/\bdisc\b/u);
    expect(getComputedStyle(ordered!).paddingLeft).not.toMatch(/^0(px)?$/u);
  });
});

describe("span-level citations in lists", () => {
  it("puts each cited step's badge inside its own list item and leaves generated steps unbadged", () => {
    const markdown =
      "验证步骤：\n\n1. 登录测试环境，输入验证码123456\n2. 打开保单贷款页面\n3. 申请金额不超过现金价值的80%";
    const span = (text: string) => {
      const start = Array.from(
        markdown.slice(0, markdown.indexOf(text)),
      ).length;
      return { answerStart: start, answerEnd: start + Array.from(text).length };
    };
    const { container } = render(
      <GroundedAnswer
        response={answerResponse({
          answer: markdown,
          claims: [
            {
              claimId: "claim-login",
              text: "登录测试环境，输入验证码123456",
              ...span("登录测试环境，输入验证码123456"),
              citationIds: ["citation-a"],
            },
            {
              claimId: "claim-limit",
              text: "申请金额不超过现金价值的80%",
              ...span("申请金额不超过现金价值的80%"),
              citationIds: ["citation-b"],
            },
          ],
          citations: [
            retrievalCitation("citation-a"),
            retrievalCitation("citation-b"),
          ],
        })}
        onOpenCitation={() => undefined}
      />,
    );

    const items = Array.from(
      container.querySelectorAll(".tapper-markdown ol > li"),
    );
    expect(items).toHaveLength(3);
    expect(
      items[0]!.querySelector("[data-claim-id='claim-login'] button"),
    ).not.toBeNull();
    expect(items[1]!.querySelector("button")).toBeNull();
    expect(items[1]!.textContent).toBe("打开保单贷款页面");
    expect(
      items[2]!.querySelector("[data-claim-id='claim-limit'] button"),
    ).not.toBeNull();
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  answerResponse,
  retrievalCitation,
} from "../testing/fakeKnowledgeClient";
import { GroundedAnswer } from "./GroundedAnswer";

// The app stylesheet sits on Tailwind's preflight, which resets every list to
// `list-style: none` with no padding; load both, in app order, into jsdom.
const preflight = readFileSync(
  resolve("node_modules/tailwindcss/preflight.css"),
  "utf8",
);
const appStyles = readFileSync(resolve("src/app/styles.css"), "utf8").replace(
  /^@import\s+"tailwindcss";/mu,
  "",
);

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

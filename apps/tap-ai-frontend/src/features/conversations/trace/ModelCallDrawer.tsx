import { Button, Drawer } from "antd";
import { useState } from "react";

import { useModelCallDetail } from "../api/queries";
import type { ModelCallDetail } from "../api/client";

type DrawerTab = "request" | "response" | "reasoning";

function tabLabel(tab: DrawerTab, locale: "en" | "zh"): string {
  if (tab === "request") return locale === "zh" ? "请求" : "Request";
  if (tab === "response") return locale === "zh" ? "返回" : "Response";
  return locale === "zh" ? "推理" : "Reasoning";
}

function tabContent(
  detail: ModelCallDetail | undefined,
  tab: DrawerTab,
  locale: "en" | "zh",
): string {
  if (detail === undefined) return "";
  if (tab === "request") return detail.request;
  if (tab === "response")
    return (
      detail.response ??
      (locale === "zh" ? "未记录返回内容。" : "No response recorded.")
    );
  return (
    detail.reasoning ??
    (locale === "zh" ? "未记录推理内容。" : "No reasoning recorded.")
  );
}

export function ModelCallDrawer({
  projectId,
  callId,
  locale,
  onClose,
}: {
  projectId: string | null;
  callId: string | null;
  locale: "en" | "zh";
  onClose: () => void;
}) {
  const [tab, setTab] = useState<DrawerTab>("request");
  const detailQuery = useModelCallDetail(projectId, callId);

  return (
    <Drawer
      open={callId !== null}
      title={locale === "zh" ? "模型调用详情" : "Model call detail"}
      placement="right"
      closable={false}
      destroyOnHidden
      extra={
        <Button
          onClick={onClose}
          aria-label={locale === "zh" ? "关闭" : "Close"}
        >
          {locale === "zh" ? "关闭" : "Close"}
        </Button>
      }
      onClose={onClose}
    >
      <div role="tablist" className="tap-model-call-drawer-tabs">
        {(["request", "response", "reasoning"] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
          >
            {tabLabel(value, locale)}
          </button>
        ))}
      </div>
      {detailQuery.isLoading ? (
        <p role="status">{locale === "zh" ? "加载中…" : "Loading…"}</p>
      ) : null}
      {detailQuery.isError ? (
        <p role="alert">
          {locale === "zh" ? "调用详情不可用。" : "Call detail is unavailable."}
        </p>
      ) : null}
      {detailQuery.data !== undefined ? (
        <>
          <pre>{tabContent(detailQuery.data, tab, locale)}</pre>
          <Button
            onClick={() =>
              navigator.clipboard.writeText(
                tabContent(detailQuery.data, tab, locale),
              )
            }
          >
            {locale === "zh" ? "复制" : "Copy"}
          </Button>
        </>
      ) : null}
    </Drawer>
  );
}

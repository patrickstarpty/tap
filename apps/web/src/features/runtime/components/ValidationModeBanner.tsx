export function ValidationModeBanner({
  state,
}: {
  state: "connecting" | "ready" | "unavailable";
}) {
  return (
    <div
      className="tap-validation-banner"
      role="status"
      aria-label="Validation Mode"
    >
      <strong>{state === "ready" ? "Validation Mode" : "运行环境"}</strong>
      <span>
        {state === "ready"
          ? "操作统一记录到固定 Validation Actor，不代表个人身份"
          : state === "connecting"
            ? "正在连接运行环境 · 服务器操作暂不可用"
            : "运行环境连接失败 · 服务器操作暂不可用"}
      </span>
    </div>
  );
}

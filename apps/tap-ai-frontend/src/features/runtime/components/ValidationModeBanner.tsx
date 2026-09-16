export function ValidationModeBanner({
  state,
}: {
  state: "connecting" | "ready" | "unavailable";
}) {
  if (state === "ready") return null;

  return (
    <div className="tap-validation-banner" role="status" aria-label="运行环境">
      <strong>运行环境</strong>
      <span>
        {state === "connecting"
          ? "正在连接运行环境…"
          : "运行环境连接失败 · 当前无法使用服务器功能，请稍后重试"}
      </span>
    </div>
  );
}

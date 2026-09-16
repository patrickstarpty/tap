import { TapProductPrototype } from "../widgets/tap/TapProductPrototype";

export function TapAiPage({
  conversationSource = "api",
}: {
  conversationSource?: "api" | "fixture";
}) {
  return <TapProductPrototype conversationSource={conversationSource} />;
}

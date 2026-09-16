import { TapAiPage } from "../pages/TapAiPage";
import { AppProviders } from "./providers";

export function App() {
  return (
    <AppProviders>
      <TapAiPage />
    </AppProviders>
  );
}

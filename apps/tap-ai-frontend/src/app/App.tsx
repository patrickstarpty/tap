import { LcaEmbedPage } from "../pages/LcaEmbedPage";
import { TapAiPage } from "../pages/TapAiPage";
import { AppProviders } from "./providers";

export function App() {
  return (
    <AppProviders>
      {window.location.pathname === "/embed/lca" ? (
        <LcaEmbedPage />
      ) : (
        <TapAiPage />
      )}
    </AppProviders>
  );
}

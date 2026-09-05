import { TapperPage } from "../pages/TapperPage";
import { AppProviders } from "./providers";

export function App() {
  return (
    <AppProviders>
      <TapperPage />
    </AppProviders>
  );
}

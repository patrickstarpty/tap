import { AthenaPage } from "../pages/AthenaPage";
import { AppProviders } from "./providers";

export function App() {
  return (
    <AppProviders>
      <AthenaPage />
    </AppProviders>
  );
}

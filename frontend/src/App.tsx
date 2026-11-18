import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { WorkspacePage } from "./pages/WorkspacePage";
import { DataPage } from "./pages/DataPage";
import { RunsPage } from "./pages/RunsPage";
import { PresetsPage } from "./pages/PresetsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<WorkspacePage />} />
        <Route path="data" element={<DataPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="presets" element={<PresetsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

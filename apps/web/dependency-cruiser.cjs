const layerRoot = "(?:src|architecture-fixtures/src)";

module.exports = {
  forbidden: [
    {
      name: "no-circular",
      severity: "error",
      from: {},
      to: { circular: true },
    },
    {
      name: "no-unresolved",
      severity: "error",
      from: {},
      to: { couldNotResolve: true },
    },
    {
      name: "shared-does-not-import-upward",
      severity: "error",
      from: { path: `^${layerRoot}/shared/` },
      to: { path: `^${layerRoot}/(?:features|widgets|pages|app)/` },
    },
    {
      name: "features-do-not-import-upward",
      severity: "error",
      from: { path: `^${layerRoot}/features/` },
      to: { path: `^${layerRoot}/(?:widgets|pages|app)/` },
    },
    {
      name: "widgets-do-not-import-upward",
      severity: "error",
      from: { path: `^${layerRoot}/widgets/` },
      to: { path: `^${layerRoot}/(?:pages|app)/` },
    },
    {
      name: "pages-do-not-import-app",
      severity: "error",
      from: { path: `^${layerRoot}/pages/` },
      to: { path: `^${layerRoot}/app/` },
    },
    {
      name: "no-feature-to-feature",
      severity: "error",
      from: { path: `^${layerRoot}/features/([^/]+)/` },
      to: {
        path: `^${layerRoot}/features/`,
        pathNot: `^${layerRoot}/features/$1/`,
      },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    enhancedResolveOptions: {
      extensions: [".js", ".jsx", ".ts", ".tsx"],
    },
    tsConfig: { fileName: "tsconfig.json" },
  },
};

import type { ThemeConfig } from "antd";

export const tapperTheme: ThemeConfig = {
  token: {
    colorPrimary: "#4f46e5",
    colorInfo: "#4f46e5",
    colorSuccess: "#27805f",
    colorWarning: "#a15c16",
    colorError: "#b64141",
    colorBgBase: "#f7f6f3",
    colorBgContainer: "#fffefa",
    colorBorder: "#dedbd4",
    colorText: "#252525",
    colorTextSecondary: "#6f6c66",
    borderRadius: 8,
    controlHeight: 36,
    fontSize: 14,
    fontFamily:
      'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  components: {
    Button: {
      primaryShadow: "none",
    },
    Table: {
      borderColor: "#e4e1da",
      headerBg: "#f3f1ec",
      headerColor: "#55524d",
      rowHoverBg: "#f7f6ff",
    },
    Tabs: {
      horizontalItemGutter: 30,
      inkBarColor: "#4f46e5",
      itemSelectedColor: "#3730a3",
    },
  },
};

import type { ThemeConfig } from "antd";

// Keep semantic values aligned with :root in styles.css.
export const tapperTheme: ThemeConfig = {
  token: {
    colorPrimary: "#e87722",
    colorPrimaryHover: "#f18b3b",
    colorPrimaryActive: "#e87722",
    colorInfo: "#a9470b",
    colorLink: "#a9470b",
    colorLinkHover: "#893807",
    colorSuccess: "#23745c",
    colorWarning: "#8a5b12",
    colorError: "#b33e38",
    colorBgBase: "#faf9f6",
    colorBgContainer: "#ffffff",
    colorBgElevated: "#ffffff",
    colorBgLayout: "#faf9f6",
    colorFillAlter: "#f3f2ee",
    colorBorder: "#c4cbc5",
    colorBorderSecondary: "#e4e6e1",
    colorText: "#232b2b",
    colorTextSecondary: "#626b68",
    colorTextPlaceholder: "#68716c",
    controlOutline: "rgba(169, 71, 11, 0.18)",
    borderRadius: 8,
    controlHeight: 36,
    fontSize: 14,
    fontFamily:
      'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  components: {
    Button: {
      primaryColor: "#232b2b",
      primaryShadow: "none",
      defaultShadow: "none",
      defaultHoverColor: "#a9470b",
      defaultHoverBorderColor: "#a9470b",
    },
    Checkbox: { colorPrimary: "#a9470b", colorPrimaryHover: "#893807" },
    Radio: { colorPrimary: "#a9470b" },
    Input: { activeBorderColor: "#a9470b", hoverBorderColor: "#a9470b" },
    Select: { optionSelectedBg: "#fff1e5", optionSelectedColor: "#893807" },
    Table: {
      borderColor: "#e4e6e1",
      headerBg: "#f3f2ee",
      headerColor: "#626b68",
      rowHoverBg: "#faf9f6",
      rowSelectedBg: "#fff1e5",
      rowSelectedHoverBg: "#fff1e5",
    },
    Tabs: {
      horizontalItemGutter: 30,
      inkBarColor: "#a9470b",
      itemSelectedColor: "#a9470b",
      itemHoverColor: "#893807",
    },
  },
};

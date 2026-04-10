import { createTheme, defaultCssVariablesResolver, type CSSVariablesResolver } from "@mantine/core";

export const appTheme = createTheme({
  primaryColor: "teal",
  autoContrast: true,
  defaultRadius: "md",
  fontFamily: "var(--vector-store-font-sans)",
  fontFamilyMonospace: "var(--vector-store-font-mono)",
  headings: {
    fontFamily: "var(--vector-store-font-heading)",
  },
  radius: {
    xs: "var(--border-radius-xs, 10px)",
    sm: "var(--border-radius-sm, 14px)",
    md: "var(--border-radius-md, 18px)",
    lg: "var(--border-radius-lg, 24px)",
  },
  shadows: {
    sm: "var(--vector-store-shadow-sm)",
    md: "var(--vector-store-shadow-md)",
  },
  colors: {
    sand: ["#fcfaf2", "#f7f1df", "#ebdfbf", "#dec995", "#d2b572", "#c9a657", "#c39e4a", "#aa8539", "#8c6c2c", "#715622"],
    ink: ["#eef5f6", "#d8e5e8", "#b2ccd2", "#8fb4bd", "#6f9ea9", "#5d909c", "#4c7a85", "#3d626b", "#2e4c54", "#203840"],
  },
  other: {
    pageGradient: "var(--vector-store-page-gradient)",
  },
});

export const appCssVariablesResolver: CSSVariablesResolver = (theme) => {
  const defaultVariables = defaultCssVariablesResolver(theme);

  return {
    variables: {
      ...defaultVariables.variables,
      "--mantine-font-family": "var(--vector-store-font-sans)",
      "--mantine-font-family-monospace": "var(--vector-store-font-mono)",
      "--mantine-font-family-headings": "var(--vector-store-font-heading)",
      "--mantine-shadow-sm": "var(--vector-store-shadow-sm)",
      "--mantine-shadow-md": "var(--vector-store-shadow-md)",
      "--mantine-primary-color-filled": "var(--vector-store-tone-success-solid)",
      "--mantine-primary-color-filled-hover": "var(--vector-store-tone-success-solid-hover)",
      "--mantine-primary-color-light": "var(--vector-store-tone-success-soft)",
      "--mantine-primary-color-light-hover": "var(--vector-store-tone-success-soft)",
      "--mantine-primary-color-light-color": "var(--vector-store-tone-success-text)",
    },
    light: {
      ...defaultVariables.light,
      "--mantine-color-text": "var(--vector-store-color-text)",
      "--mantine-color-body": "var(--vector-store-color-page)",
      "--mantine-color-anchor": "var(--vector-store-tone-success-solid)",
      "--mantine-color-default": "var(--vector-store-card-background-strong)",
      "--mantine-color-default-hover": "var(--vector-store-color-surface-ghost)",
      "--mantine-color-default-color": "var(--vector-store-color-text)",
      "--mantine-color-default-border": "var(--vector-store-color-border)",
      "--mantine-color-dimmed": "var(--vector-store-color-text-tertiary)",
      "--mantine-color-placeholder": "var(--vector-store-color-text-tertiary)",
      "--mantine-color-error": "var(--vector-store-tone-danger-solid)",
    },
    dark: {
      ...defaultVariables.dark,
      "--mantine-color-text": "var(--vector-store-color-text)",
      "--mantine-color-body": "var(--vector-store-color-page)",
      "--mantine-color-anchor": "var(--vector-store-tone-success-solid)",
      "--mantine-color-default": "var(--vector-store-card-background-strong)",
      "--mantine-color-default-hover": "var(--vector-store-color-surface-ghost)",
      "--mantine-color-default-color": "var(--vector-store-color-text)",
      "--mantine-color-default-border": "var(--vector-store-color-border)",
      "--mantine-color-dimmed": "var(--vector-store-color-text-tertiary)",
      "--mantine-color-placeholder": "var(--vector-store-color-text-tertiary)",
      "--mantine-color-error": "var(--vector-store-tone-danger-solid)",
    },
  };
};

// eslint-config-next 16 ships native flat config, so it is imported directly
// rather than bridged through @eslint/eslintrc.
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "*.tsbuildinfo"],
  },
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // Unused arguments are common in event handlers and callbacks; requiring
      // an underscore prefix keeps the signal without the noise.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;

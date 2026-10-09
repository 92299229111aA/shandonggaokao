/** eslint-config-next via FlatCompat; kept minimal on purpose. */
const { dirname } = require("path");
const { fileURLToPath } = require("url");
const { FlatCompat } = require("@eslint/eslintrc");

const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(`file://${__filename.replace(/\\/g, "/")}`)),
});

module.exports = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  {
    rules: {
      // The flow mutates localStorage and needs effects; the exhaustive-deps rule
      // is still on, but we allow the deliberate one-shot hydration effect.
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];

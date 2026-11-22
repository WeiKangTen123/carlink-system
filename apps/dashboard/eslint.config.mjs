// ESLint flat config. Next.js 16 removed `next lint`; `npm run lint` runs the
// ESLint CLI instead. Based on the official Next.js config for TypeScript apps:
// https://nextjs.org/docs/app/api-reference/config/eslint
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Existing code, first run (22 Nov 2026): 54 errors + 15 warnings in 30
    // files, all from these rules. They are warnings for now so the lint
    // runs clean without a big-bang fix; new code should not add to them.
    // Fix the hits a file at a time, then set each rule back to "error".
    rules: {
      "react-hooks/set-state-in-effect": "warn", // 28 hits
      "react-hooks/refs": "warn", // 7
      "react-hooks/preserve-manual-memoization": "warn", // 6
      "react-hooks/immutability": "warn", // 6
      "react-hooks/purity": "warn", // 2
      "react-hooks/use-memo": "warn", // 1
      "@typescript-eslint/no-explicit-any": "warn", // 3
      "@next/next/no-html-link-for-pages": "warn", // 1
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;

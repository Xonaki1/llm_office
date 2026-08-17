import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AuthProvider } from "@/lib/auth";
import { I18nProvider } from "@/lib/i18n";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agents Office",
  description: "Teams of AI agents that work on one task together.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  // `lang` is the default here and is corrected on the client once the stored
  // preference is known, which keeps the server and first client render equal.
  return (
    <html lang="ru">
      <body className="min-h-screen">
        <I18nProvider>
          <AuthProvider>{children}</AuthProvider>
        </I18nProvider>
      </body>
    </html>
  );
}

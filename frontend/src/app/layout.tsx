import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "IssuePilot",
  description: "Multi-agent GitHub Issue automation pipeline",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans antialiased">
        {children}
      </body>
    </html>
  );
}

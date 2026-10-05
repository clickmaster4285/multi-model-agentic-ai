import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MulteAgent — Control Deck",
  description: "Local multi-agent consensus chat and agent manager",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "VoiceGen AI — Talk to my AI assistant",
  description:
    "Voice AI agent for Moazzam Qureshi's freelancing business. " +
    "Click to start a 90-second call. The agent searches Moazzam's portfolio " +
    "for relevant past work, gives an honest fit assessment, and you leave " +
    "with a PDF summary and the call recording.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}

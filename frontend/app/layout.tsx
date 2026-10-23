import { Instrument_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import "./theme.css";

// Self-hosted by next/font: no font requests leave the user's browser.
const body = Instrument_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-body", display: "swap" });
const data = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-data", display: "swap" });

export const metadata = {
  title: "Veyra",
  description: "Investigation and dispatch-decision tool for robots and remote-operated machines.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en" className={`${body.variable} ${data.variable}`}><body>{children}</body></html>;
}

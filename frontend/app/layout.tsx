import "./globals.css";

export const metadata = {
  title: "Veyra V0 | Operational context for Physical AI",
  description: "Veyra reconstructs what machines did, what changed, what people knew, and what happened next.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}

import "./globals.css";

export const metadata = {
  title: "Veyra V0 | Operational intelligence for Physical AI",
  description: "See what changed, decide what to do, learn what happened after and reuse it next time.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}

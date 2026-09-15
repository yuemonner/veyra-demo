import "./globals.css";

export const metadata = {
  title: "Veyra V0 | Operational intelligence for Physical AI",
  description: "Veyra reconstructs what changed, what the team did and what happened next so the next machine decision starts with prior operational context.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}

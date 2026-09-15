import "./globals.css";

export const metadata = {
  title: "Veyra V0 | Decision infrastructure for Physical AI",
  description: "Veyra reconstructs machine evidence into sealed decision records with time-aware evidence, human approval and outcome linkage.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}

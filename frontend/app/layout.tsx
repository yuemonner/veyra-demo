import "./globals.css";

export const metadata = {
  title: "Silken Reason Lab",
  description: "An independent research lab studying intelligence in real-world systems.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}

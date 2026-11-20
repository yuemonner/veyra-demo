import { redirect } from "next/navigation";

// The demo opens on the real field case, investigated at system level.
export default function Home() {
  redirect("/system-investigation");
}

import { redirect } from "next/navigation";

// The tool opens on the field cases.
export default function Home() {
  redirect("/field-case");
}

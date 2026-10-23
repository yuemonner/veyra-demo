import InvestigationClient from "./view";

export default async function InvestigationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <InvestigationClient id={id} />;
}

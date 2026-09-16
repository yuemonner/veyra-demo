import InvestigationClient from "../investigations/[id]/view";

const INVESTIGATION_ID = "inv-120-robots-bad-rollout";

export default function ProductDemoPage() {
  return <InvestigationClient id={INVESTIGATION_ID} />;
}

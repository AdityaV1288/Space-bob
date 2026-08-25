export function AssumptionMark({ reason }: { reason: string }) {
  return <span className="assumption-mark" role="img" aria-label={`Assumed or simulated: ${reason}`} title={`Assumed or simulated — ${reason}`}>*</span>
}

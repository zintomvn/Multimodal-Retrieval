export function requireValidExport(report: {valid: boolean; errors: string[]}, csvUri: string | null): void {
  if (!report.valid) throw new Error(`Invalid submission: ${report.errors.join(", ") || "validation failed"}`);
  if (!csvUri) throw new Error("Export did not produce a CSV file");
}

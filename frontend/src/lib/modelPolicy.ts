interface ModelMetadata {
  modelId: string;
  displayName: string;
  notes: string;
}

export function isKnownAdvisoryModel(model: ModelMetadata): boolean {
  if (model.modelId.trim().toLowerCase() === "deepseek-flash") return false;
  const identity = [model.modelId, model.displayName].join(" ").replace(/[-_/]+/g, " ");
  if (/(?:^|\s)(?:flash|advisory|preview)(?:\s|$)/i.test(identity)) return true;

  return /(?:^|[;,\n])\s*(?:advisory|preview)(?:\s+only)?\s*(?:$|[;,\n])/i.test(model.notes);
}

interface ModelMetadata {
  modelId: string;
  displayName: string;
  notes: string;
}

export function isKnownAdvisoryModel(model: ModelMetadata): boolean {
  const modelId = model.modelId.trim().toLowerCase();
  if (modelId === "deepseek-flash" || /\/deepseek-v4-flash(?:$|[?#])/i.test(modelId)) return false;
  if (modelId === "qwen3.6-35b-a3b" || /\/qwen3\.6-35b-a3b(?:$|[?#])/i.test(modelId)) return true;
  const identity = [model.modelId, model.displayName].join(" ").replace(/[-_/]+/g, " ");
  if (/(?:^|\s)(?:flash|advisory|preview)(?:\s|$)/i.test(identity)) return true;

  return /(?:^|[;,\n])\s*(?:advisory|preview)(?:\s+only)?\s*(?:$|[;,\n])/i.test(model.notes);
}

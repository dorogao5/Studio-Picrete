import type { Assistant, Provider } from "../../lib/types";

interface Props {
  assistant: Assistant;
  providers: Provider[];
  onProfileChanged: () => Promise<void> | void;
}

export default function MaterialsTab(_props: Props) {
  return null;
}

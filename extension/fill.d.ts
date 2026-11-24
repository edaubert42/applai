// The types of fill.js, for the frontend's test of it.
export type FillAction = {
  field: number;
  label: string;
  kind: "fill" | "choose" | "attach";
  value: string | string[];
};
export type FilePayload = { name: string; type: string; base64: string };
export type FieldFilled = {
  field: number;
  value?: string | string[] | null;
  file?: string | null;
  unfilled?: string | null;
};
export function fillForm(
  actions: FillAction[],
  files?: Record<string, FilePayload>,
  root?: Document,
): FieldFilled[];

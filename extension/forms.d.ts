// The types of forms.js, for the frontend's test of it.
export type Field = {
  label: string;
  type: "text" | "email" | "phone" | "file" | "select" | "radio" | "checkbox" | "textarea";
  required: boolean;
  options: string[];
};
export function readForm(root?: Document): Field[];

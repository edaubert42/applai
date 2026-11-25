/** What the server recorded, in the candidate's language: a code, maybe with a detail after ":".
 * Free text (an agent's words or error, a message stored before codes) shows as it is. */
export function serverMessage(text: string, lookup: (key: string) => string | undefined): string {
  const [code, ...detail] = text.split(":");
  return lookup(`message.${code}`)?.replace("{detail}", detail.join(":")) ?? text;
}

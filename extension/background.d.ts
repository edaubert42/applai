// The types of background.js, for the frontend's test of its loop on a fictional form.
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;
type Wait = (ms: number) => Promise<void>;
export const APPLAI: string;
export function readForms(fetch?: Fetch, browser?: object, wait?: Wait): Promise<void>;
export function fillForms(fetch?: Fetch, browser?: object, wait?: Wait): Promise<void>;

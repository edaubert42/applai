import { CATEGORIES, type Service, type ServiceState, type Services } from "./api";

/** The color of a service's dot, by state. */
export const STATE_DOT: Record<ServiceState, string> = {
  red: "bg-minus",
  orange: "bg-unknown",
  green: "bg-plus",
};

/** The worst state of all services, and the services in it: red before orange before green. */
export function worstState(services: Services): { state: ServiceState; at_fault: Service[] } {
  const all = CATEGORIES.flatMap((category) => services[category]);
  for (const state of ["red", "orange"] as const) {
    const at_fault = all.filter((service) => service.state === state);
    if (at_fault.length) return { state, at_fault };
  }
  return { state: "green", at_fault: [] };
}

/** A service's name in the candidate's language; one the translation files do not name (a new source) shows its id. */
export function serviceName(id: string, lookup: (key: string) => string | undefined): string {
  return lookup(`settings.service.${id}`) ?? id;
}

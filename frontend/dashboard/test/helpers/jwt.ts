// An unsigned JWT-shaped string for tests: the page only ever reads its payload's exp to schedule a refresh.
const b64url = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
export const jwt = (payload: Record<string, unknown>) => `${b64url({ alg: "RS256", kid: "k1" })}.${b64url(payload)}.sig`;
export { b64url };

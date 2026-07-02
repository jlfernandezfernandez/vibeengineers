// Client-side "read" tracking, stored under a single localStorage key.
// All functions are pure (operate on the raw string) so they are trivially testable
// without a DOM or localStorage.

export const KEY = 'ctx-reading';

export function parseReading(raw: string | null): { read: string[] } {
	const empty = { read: [] as string[] };
	if (!raw) return empty;
	try {
		const v = JSON.parse(raw);
		const read = Array.isArray(v?.read) ? v.read.filter((s: unknown) => typeof s === 'string') : [];
		return { read };
	} catch {
		// corrupt JSON = empty state, never throw on the client
		return empty;
	}
}

export function markRead(raw: string | null, slug: string): string {
	const reading = parseReading(raw);
	if (!reading.read.includes(slug)) reading.read.push(slug);
	return JSON.stringify(reading);
}
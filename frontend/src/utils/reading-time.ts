export function getReadingTime(content: string, wpm = 200): number {
	const words = content.trim().split(/\s+/).length;
	return Math.ceil(words / wpm);
}

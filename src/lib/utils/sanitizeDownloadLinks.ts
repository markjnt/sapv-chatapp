/**
 * Normalize model-generated download links.
 * Models often prefix valid paths with `sandbox:` (ChatGPT-style) which breaks clicks.
 */
export const sanitizeDownloadLinks = (content: string): string => {
	if (!content || typeof content !== 'string') return content;

	let text = content;

	// Markdown links: [label](sandbox:/api/v1/files/...) or [label](sandbox:/mnt/uploads/...)
	text = text.replace(/\[([^\]]*)\]\(([^)]+)\)/g, (_match, label: string, target: string) => {
		return `[${label}](${normalizeDownloadTarget(target)})`;
	});

	// Bare sandbox-prefixed paths outside markdown links
	text = text.replace(/sandbox:(\/api\/v1\/files\/[^\s)\]]+)/gi, '$1');
	text = text.replace(/sandbox:(\/mnt\/uploads\/[^\s)\]]+)/gi, '$1');

	return text;
};

const normalizeDownloadTarget = (target: string): string => {
	let normalized = target.trim();
	if (normalized.toLowerCase().startsWith('sandbox:')) {
		normalized = normalized.slice('sandbox:'.length);
	}
	return normalized;
};

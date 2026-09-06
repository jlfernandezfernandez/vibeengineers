// @ts-check
import { defineConfig } from 'astro/config';

const site = process.env.SITE_URL || 'http://localhost:4321';

// ponytail: sitemap integration dropped — @astrojs/sitemap 3.7.3 (latest) uses
// zod 3 API but astro 6 requires zod 4; no compatible sitemap release exists.
// Re-add when @astrojs/sitemap ships zod 4 / astro 6 support.
export default defineConfig({
	site,
	base: new URL(site).pathname,
	redirects: {
		'/como-funciona': '/how-it-works',
	},
	markdown: {
		shikiConfig: {
			theme: 'github-dark-default',
		},
	},
});

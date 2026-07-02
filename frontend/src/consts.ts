// Place any global data in this file.
// You can import this data from anywhere in your site by using the `import` keyword.

export const SITE_TITLE = 'Ctx';
export const SITE_DESCRIPTION =
	'Una píldora técnica al día. Para vibe coders que quieren entender qué pasa por debajo.';
export const REPO = 'jlfernandezfernandez/ctx';
export const REPO_URL = `https://github.com/${REPO}`;
export const TOPICS_URL = `${REPO_URL}/issues?q=is%3Aissue+is%3Aopen+label%3Atopic+sort%3Areactions-%2B1-desc`;
export const TOPICS_API_URL = `https://api.github.com/repos/${REPO}/issues?state=open&labels=topic&per_page=100`;
// Base path without trailing slash, ready to prefix internal hrefs.
export const BASE = import.meta.env.BASE_URL.replace(/\/+$/, '');

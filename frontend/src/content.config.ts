import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const quizQuestion = z.object({
	question: z.string(),
	options: z.array(z.string()).length(4),
	correct: z.number().int().min(0).max(3),
	explanation: z.string(),
});

const blog = defineCollection({
	// The generator only ever writes plain Markdown.
	loader: glob({ base: './src/content/blog', pattern: '**/*.md' }),
	// Type-check frontmatter using a schema
	schema: z.object({
		title: z.string(),
		description: z.string(),
		// Transform string to Date object
		date: z.coerce.date(),
		tags: z.array(z.string()).default([]),
		issue: z.number().optional(),
		requestedBy: z.string().optional(),
		writer: z.string().optional(),
		reviewer: z.string().optional(),
		quiz: z.array(quizQuestion).length(3),
	}),
});

export const collections = { blog };

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { markRead, parseReading } from './reading.ts';

test('parseReading: empty localStorage', () => {
	assert.deepEqual(parseReading(null), { read: [] });
});

test('parseReading: corrupt JSON', () => {
	assert.deepEqual(parseReading('{not json'), { read: [] });
});

test('parseReading: missing/invalid read field', () => {
	assert.deepEqual(parseReading('{}'), { read: [] });
	assert.deepEqual(parseReading('{"read":"nope"}'), { read: [] });
});

test('parseReading: filters non-string entries', () => {
	assert.deepEqual(parseReading('{"read":["a",1,null,"b"]}').read, ['a', 'b']);
});

test('markRead: adds slug to empty state', () => {
	assert.deepEqual(parseReading(markRead(null, 'slug-1')).read, ['slug-1']);
});

test('markRead: no duplicates', () => {
	const once = markRead(null, 'slug-1');
	const twice = markRead(once, 'slug-1');
	assert.deepEqual(parseReading(twice).read, ['slug-1']);
});

test('markRead: appends to existing', () => {
	const raw = markRead(markRead(null, 'a'), 'b');
	assert.deepEqual(parseReading(raw).read, ['a', 'b']);
});

test('markRead: recovers from corrupt state', () => {
	assert.deepEqual(parseReading(markRead('garbage', 'a')).read, ['a']);
});
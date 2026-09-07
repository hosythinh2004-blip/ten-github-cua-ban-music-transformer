import test from 'node:test';
import assert from 'node:assert/strict';
import { extractAudioUrl, resolveSunoAudio, safeFilename, validateSunoUrl } from '../lib/suno.js';

test('chỉ chấp nhận URL HTTPS thuộc Suno', () => {
  assert.equal(validateSunoUrl('https://suno.com/song/abc').hostname, 'suno.com');
  assert.throws(() => validateSunoUrl('https://example.com/file.m4a'), /Chỉ hỗ trợ/);
  assert.throws(() => validateSunoUrl('not a url'), /không hợp lệ/);
});

test('trích xuất audio URL an toàn từ HTML', () => {
  const html = String.raw`{"audio_url":"https:\/\/cdn1.suno.ai\/demo.m4a?x=1\u0026y=2"}`;
  assert.equal(extractAudioUrl(html).href, 'https://cdn1.suno.ai/demo.m4a?x=1&y=2');
  assert.equal(extractAudioUrl('<p>none</p>'), null);
});

test('bỏ qua tải trang với liên kết audio trực tiếp', async () => {
  const result = await resolveSunoAudio('https://cdn1.suno.ai/track.m4a', () => { throw new Error('must not fetch'); });
  assert.equal(result.href, 'https://cdn1.suno.ai/track.m4a');
});

test('tạo tên tải xuống sạch', () => {
  assert.equal(safeFilename(new URL('https://cdn1.suno.ai/my%20song.m4a'), 'wav'), 'my-song.wav');
});

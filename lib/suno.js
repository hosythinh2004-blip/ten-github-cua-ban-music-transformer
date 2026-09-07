import { basename } from 'node:path';

const ALLOWED_HOSTS = /(^|\.)(suno\.com|suno\.ai)$/i;
const AUDIO_HOSTS = /(^|\.)(suno\.ai|suno\.com)$/i;
const AUDIO_URL_RE = /https?:\\?\/\\?\/[^"'<>\\\s]+?\.(?:m4a|mp3)(?:\?[^"'<>\\\s]*)?/gi;

export function validateSunoUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('Liên kết không hợp lệ.'); }
  if (url.protocol !== 'https:' || !ALLOWED_HOSTS.test(url.hostname)) {
    throw new Error('Chỉ hỗ trợ liên kết HTTPS từ Suno.');
  }
  return url;
}

export function extractAudioUrl(html) {
  const decoded = html.replaceAll('\\u0026', '&').replaceAll('\\/', '/');
  for (const match of decoded.matchAll(AUDIO_URL_RE)) {
    const candidate = match[0].replaceAll('\\/', '/');
    try {
      const url = new URL(candidate);
      if (AUDIO_HOSTS.test(url.hostname)) return url;
    } catch { /* continue looking */ }
  }
  return null;
}

export async function resolveSunoAudio(input, fetcher = fetch) {
  const pageUrl = validateSunoUrl(input);
  if (/\.(m4a|mp3)$/i.test(pageUrl.pathname)) return pageUrl;

  const response = await fetcher(pageUrl, {
    redirect: 'follow',
    headers: { 'user-agent': 'Mozilla/5.0 SunoAudioConverter/1.0' },
    signal: AbortSignal.timeout(15_000)
  });
  if (!response.ok) throw new Error('Không thể đọc liên kết Suno này.');
  const audioUrl = extractAudioUrl(await response.text());
  if (!audioUrl) throw new Error('Không tìm thấy tệp âm thanh công khai trong liên kết.');
  return audioUrl;
}

export function safeFilename(url, format) {
  const raw = decodeURIComponent(basename(url.pathname)).replace(/\.[^.]+$/, '');
  const clean = raw.replace(/[^a-zA-Z0-9_-]+/g, '-').slice(0, 60) || 'suno-audio';
  return `${clean}.${format}`;
}

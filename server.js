import http from 'node:http';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import { resolveSunoAudio, safeFilename } from './lib/suno.js';

const root = fileURLToPath(new URL('./public/', import.meta.url));
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml' };

function json(res, status, data) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

async function body(req) {
  let value = '';
  for await (const chunk of req) {
    value += chunk;
    if (value.length > 20_000) throw new Error('Yêu cầu quá lớn.');
  }
  return JSON.parse(value || '{}');
}

function runFfmpeg(source, output, format) {
  const args = ['-hide_banner', '-loglevel', 'error', '-y', '-i', source];
  args.push(...(format === 'wav' ? ['-acodec', 'pcm_s16le'] : ['-codec:a', 'libmp3lame', '-q:a', '2']), output);
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.FFMPEG_PATH || 'ffmpeg', args);
    let error = '';
    child.stderr.on('data', chunk => { error += chunk; });
    child.on('error', err => reject(err.code === 'ENOENT' ? new Error('Máy chủ chưa cài đặt FFmpeg.') : err));
    child.on('close', code => code === 0 ? resolve() : reject(new Error(error || 'FFmpeg không thể chuyển đổi tệp.')));
  });
}

async function convert(req, res) {
  let folder;
  try {
    const { url, format = 'mp3' } = await body(req);
    if (!['mp3', 'wav'].includes(format)) return json(res, 400, { error: 'Định dạng không được hỗ trợ.' });
    const audioUrl = await resolveSunoAudio(url);
    folder = await mkdtemp(join(tmpdir(), 'suno-converter-'));
    const input = join(folder, 'source-audio');
    const output = join(folder, `converted.${format}`);
    const download = await fetch(audioUrl, { signal: AbortSignal.timeout(60_000) });
    if (!download.ok || !download.body) throw new Error('Không thể tải tệp âm thanh từ Suno.');
    const length = Number(download.headers.get('content-length') || 0);
    if (length > 100 * 1024 * 1024) throw new Error('Tệp vượt quá giới hạn 100 MB.');
    await pipeline(Readable.fromWeb(download.body), createWriteStream(input));
    await runFfmpeg(input, output, format);
    const info = await stat(output);
    res.writeHead(200, {
      'content-type': format === 'wav' ? 'audio/wav' : 'audio/mpeg',
      'content-length': info.size,
      'content-disposition': `attachment; filename="${safeFilename(audioUrl, format)}"`,
      'cache-control': 'no-store'
    });
    await pipeline(createReadStream(output), res);
  } catch (error) {
    if (!res.headersSent) json(res, 400, { error: error.message || 'Đã xảy ra lỗi.' });
  } finally {
    if (folder) await rm(folder, { recursive: true, force: true });
  }
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'POST' && req.url === '/api/convert') return convert(req, res);
  if (req.method !== 'GET') return json(res, 405, { error: 'Method not allowed' });
  const pathname = req.url === '/' ? '/index.html' : new URL(req.url, 'http://localhost').pathname;
  if (!/^\/[a-zA-Z0-9._/-]+$/.test(pathname) || pathname.includes('..')) return json(res, 404, { error: 'Not found' });
  const file = join(root, pathname);
  const extension = file.slice(file.lastIndexOf('.'));
  try {
    const info = await stat(file);
    res.writeHead(200, { 'content-type': types[extension] || 'application/octet-stream', 'content-length': info.size });
    createReadStream(file).pipe(res);
  } catch { json(res, 404, { error: 'Not found' }); }
});

const port = Number(process.env.PORT || 3000);
server.listen(port, () => console.log(`Suno Converter đang chạy tại http://localhost:${port}`));

import 'dotenv/config';
import express from 'express';
import multer from 'multer';
import { GoogleGenAI } from '@google/genai';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const port = Number(process.env.PORT || 3188);
const outputDir = path.join(__dirname, 'output');
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 10 * 1024 * 1024, files: 3 } });
const jobs = new Map();

await mkdir(outputDir, { recursive: true });
app.use(express.json({ limit: '1mb' }));
app.use(express.static(path.join(__dirname, 'public')));
app.use('/output', express.static(outputDir));

const allowedModels = new Set(['veo-3.1-fast-generate-preview', 'veo-3.1-generate-preview']);
const allowedRatios = new Set(['16:9', '9:16']);
const allowedResolutions = new Set(['720p', '1080p', '4k']);
const allowedDurations = new Set(['4', '6', '8']);

function normalizeError(error) {
  const message = error?.message || String(error || 'Unknown error');
  if (/API.?key/i.test(message)) return 'API key không hợp lệ hoặc chưa được cấp quyền dùng Veo.';
  if (/quota|resource exhausted|429/i.test(message)) return 'Đã chạm quota/rate limit. Hãy thử lại sau hoặc kiểm tra billing/quota.';
  if (/safety|blocked|policy/i.test(message)) return 'Prompt hoặc ảnh tham chiếu bị bộ lọc an toàn từ chối.';
  return message.slice(0, 900);
}

function safeName(index = 1) {
  return `veo_${String(index).padStart(3, '0')}_${Date.now()}.mp4`;
}

function validateSettings(body, hasReferences) {
  const model = allowedModels.has(body.model) ? body.model : 'veo-3.1-fast-generate-preview';
  const aspectRatio = allowedRatios.has(body.aspectRatio) ? body.aspectRatio : '16:9';
  const resolution = allowedResolutions.has(body.resolution) ? body.resolution : '720p';
  let durationSeconds = allowedDurations.has(String(body.durationSeconds)) ? String(body.durationSeconds) : '8';
  if (hasReferences || resolution === '1080p' || resolution === '4k') durationSeconds = '8';
  return { model, aspectRatio, resolution, durationSeconds };
}

async function runJob(job, refs) {
  try {
    const apiKey = process.env.GEMINI_API_KEY?.trim();
    if (!apiKey || apiKey === 'your_key_here') throw new Error('Chưa cấu hình GEMINI_API_KEY trong file .env.');
    job.status = 'GENERATING';
    job.updatedAt = Date.now();

    const ai = new GoogleGenAI({ apiKey });
    const referenceImages = refs.map(file => ({
      image: { imageBytes: file.buffer.toString('base64'), mimeType: file.mimetype || 'image/png' },
      referenceType: 'asset'
    }));
    const config = {
      aspectRatio: job.settings.aspectRatio,
      resolution: job.settings.resolution,
      durationSeconds: job.settings.durationSeconds,
      numberOfVideos: 1
    };
    if (referenceImages.length) config.referenceImages = referenceImages;

    let operation = await ai.models.generateVideos({ model: job.settings.model, prompt: job.prompt, config });
    job.operationName = operation?.name || null;
    while (!operation.done) {
      await new Promise(resolve => setTimeout(resolve, 10_000));
      operation = await ai.operations.getVideosOperation({ operation });
      job.updatedAt = Date.now();
    }

    const generated = operation.response?.generatedVideos?.[0]?.video;
    if (!generated) throw new Error('Veo hoàn tất nhưng không trả về video.');
    const filename = safeName(job.index);
    const destination = path.join(outputDir, filename);
    await ai.files.download({ file: generated, downloadPath: destination });
    job.status = 'COMPLETED';
    job.videoUrl = `/output/${filename}`;
    job.updatedAt = Date.now();
  } catch (error) {
    job.status = 'ERROR';
    job.error = normalizeError(error);
    job.updatedAt = Date.now();
  }
}

app.get('/api/health', (req, res) => {
  const key = process.env.GEMINI_API_KEY?.trim();
  res.json({ ok: true, apiConfigured: Boolean(key && key !== 'your_key_here'), models: [...allowedModels] });
});

app.post('/api/jobs', upload.array('references', 3), (req, res) => {
  const prompt = String(req.body.prompt || '').trim();
  if (!prompt) return res.status(400).json({ error: 'Prompt trống.' });
  if (prompt.length > 8000) return res.status(400).json({ error: 'Prompt quá dài.' });
  const refs = req.files || [];
  if (refs.some(file => !/^image\/(png|jpeg|webp)$/.test(file.mimetype))) return res.status(400).json({ error: 'Ảnh tham chiếu chỉ hỗ trợ PNG, JPG hoặc WEBP.' });

  const settings = validateSettings(req.body, refs.length > 0);
  const job = {
    id: randomUUID(), index: Number(req.body.index || 1), prompt, status: 'WAITING', settings,
    videoUrl: null, error: null, createdAt: Date.now(), updatedAt: Date.now()
  };
  jobs.set(job.id, job);
  res.status(202).json(job);
  queueMicrotask(() => runJob(job, refs));
});

app.get('/api/jobs/:id', (req, res) => {
  const job = jobs.get(req.params.id);
  if (!job) return res.status(404).json({ error: 'Không tìm thấy job.' });
  res.json(job);
});

app.delete('/api/jobs/:id', (req, res) => {
  const job = jobs.get(req.params.id);
  if (!job) return res.status(404).json({ error: 'Không tìm thấy job.' });
  if (job.status === 'GENERATING') return res.status(409).json({ error: 'Job đang render, chưa thể xoá.' });
  jobs.delete(req.params.id);
  res.status(204).end();
});

app.get('*', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));
app.listen(port, () => console.log(`VEO AUTO VIDEO STUDIO: http://localhost:${port}`));

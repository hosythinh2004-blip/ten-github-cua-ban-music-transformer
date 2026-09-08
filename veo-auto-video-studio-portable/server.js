import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = 3188;
const appData = process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
const dataDir = path.join(appData, 'VeoAutoVideoStudio');
const configPath = path.join(dataDir, 'config.json');
const outputDir = path.join(os.homedir(), 'Documents', 'VEO Auto Video Studio');
const publicDir = path.join(__dirname, 'public');
const jobs = new Map();
await fsp.mkdir(dataDir, { recursive: true });
await fsp.mkdir(outputDir, { recursive: true });

const MIME = { '.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.css':'text/css; charset=utf-8', '.svg':'image/svg+xml', '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp', '.mp4':'video/mp4' };
const ALLOWED_MODELS = new Set(['veo-3.1-fast-generate-preview','veo-3.1-generate-preview']);
const ALLOWED_RATIO = new Set(['16:9','9:16']);
const ALLOWED_RES = new Set(['720p','1080p','4k']);
const ALLOWED_DUR = new Set(['4','6','8']);

async function readConfig(){
  try { return JSON.parse(await fsp.readFile(configPath,'utf8')); } catch { return {}; }
}
async function writeConfig(cfg){ await fsp.writeFile(configPath, JSON.stringify(cfg,null,2),'utf8'); }
function maskKey(key=''){ return key ? `${key.slice(0,6)}••••${key.slice(-4)}` : ''; }
function json(res, code, data){ const body=JSON.stringify(data); res.writeHead(code, {'content-type':'application/json; charset=utf-8','content-length':Buffer.byteLength(body)}); res.end(body); }
async function readBody(req, max=45*1024*1024){ return await new Promise((resolve,reject)=>{ let size=0; const chunks=[]; req.on('data',c=>{ size+=c.length; if(size>max){ reject(new Error('Dữ liệu quá lớn.')); req.destroy(); return; } chunks.push(c); }); req.on('end',()=>{ try{ resolve(chunks.length?JSON.parse(Buffer.concat(chunks).toString('utf8')):{}); }catch{ reject(new Error('JSON không hợp lệ.')); }}); req.on('error',reject); }); }
function safeSettings(body, hasRefs){
  const model=ALLOWED_MODELS.has(body.model)?body.model:'veo-3.1-fast-generate-preview';
  const aspectRatio=ALLOWED_RATIO.has(body.aspectRatio)?body.aspectRatio:'16:9';
  const resolution=ALLOWED_RES.has(body.resolution)?body.resolution:'720p';
  let durationSeconds=ALLOWED_DUR.has(String(body.durationSeconds))?String(body.durationSeconds):'8';
  if(hasRefs || resolution==='1080p' || resolution==='4k') durationSeconds='8';
  return {model,aspectRatio,resolution,durationSeconds};
}
function normalizeError(err){
  const m=String(err?.message||err||'Lỗi không xác định');
  if(/401|403|API.?key|permission/i.test(m)) return 'API key không hợp lệ hoặc tài khoản chưa có quyền dùng Veo.';
  if(/429|quota|resource exhausted/i.test(m)) return 'Đã chạm quota/rate limit của Veo. Kiểm tra quota hoặc billing API.';
  if(/safety|blocked|policy/i.test(m)) return 'Veo từ chối prompt/ảnh do bộ lọc an toàn.';
  return m.slice(0,800);
}
function parseDataUrl(v){ const m=/^data:(image\/(?:png|jpeg|webp));base64,(.+)$/s.exec(v||''); return m?{mimeType:m[1],data:m[2]}:null; }
async function googleFetch(url, options={}){
  const cfg=await readConfig();
  if(!cfg.apiKey) throw new Error('Chưa nhập Gemini API key.');
  const headers={...(options.headers||{}),'x-goog-api-key':cfg.apiKey};
  const r=await fetch(url,{...options,headers});
  if(!r.ok){ const txt=await r.text(); throw new Error(`Google API ${r.status}: ${txt.slice(0,700)}`); }
  return r;
}
async function runJob(job){
  try{
    job.status='GENERATING'; job.updatedAt=Date.now();
    const instance={prompt:job.prompt};
    if(job.references.length){
      instance.referenceImages=job.references.map(r=>({image:{inlineData:{mimeType:r.mimeType,data:r.data}},referenceType:'asset'}));
    }
    const parameters={aspectRatio:job.settings.aspectRatio,resolution:job.settings.resolution,durationSeconds:job.settings.durationSeconds,personGeneration:job.references.length?'allow_adult':'allow_all'};
    const base='https://generativelanguage.googleapis.com/v1beta';
    const create=await googleFetch(`${base}/models/${job.settings.model}:predictLongRunning`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({instances:[instance],parameters})});
    let op=await create.json();
    if(!op.name) throw new Error('Google không trả về mã operation.');
    job.operationName=op.name;
    for(;;){
      await new Promise(r=>setTimeout(r,10000));
      const st=await googleFetch(`${base}/${op.name}`); op=await st.json(); job.updatedAt=Date.now();
      if(op.done) break;
    }
    if(op.error) throw new Error(JSON.stringify(op.error));
    const uri=op?.response?.generateVideoResponse?.generatedSamples?.[0]?.video?.uri;
    if(!uri) throw new Error('Veo hoàn tất nhưng không trả về video URI.');
    const vr=await googleFetch(uri,{redirect:'follow'});
    const buf=Buffer.from(await vr.arrayBuffer());
    const filename=`veo_${String(job.index).padStart(3,'0')}_${Date.now()}.mp4`;
    await fsp.writeFile(path.join(outputDir,filename),buf);
    job.filename=filename; job.status='COMPLETED'; job.updatedAt=Date.now();
  }catch(e){ job.status='ERROR'; job.error=normalizeError(e); job.updatedAt=Date.now(); }
}
async function serveFile(req,res,pathname){
  let rel=pathname==='/'?'index.html':pathname.replace(/^\//,'');
  if(rel.startsWith('output/')){
    const file=path.join(outputDir,path.basename(rel));
    try{ const st=await fsp.stat(file); res.writeHead(200,{'content-type':'video/mp4','content-length':st.size,'accept-ranges':'bytes'}); fs.createReadStream(file).pipe(res); }catch{ json(res,404,{error:'Không tìm thấy video.'}); }
    return;
  }
  const file=path.join(publicDir,rel);
  if(!file.startsWith(publicDir)) return json(res,403,{error:'Forbidden'});
  try{ const data=await fsp.readFile(file); res.writeHead(200,{'content-type':MIME[path.extname(file)]||'application/octet-stream','content-length':data.length}); res.end(data); }
  catch{ const data=await fsp.readFile(path.join(publicDir,'index.html')); res.writeHead(200,{'content-type':MIME['.html']}); res.end(data); }
}

const server=http.createServer(async(req,res)=>{
  try{
    const u=new URL(req.url,`http://${req.headers.host||'127.0.0.1'}`); const p=u.pathname;
    if(p==='/api/health' && req.method==='GET'){ const c=await readConfig(); return json(res,200,{ok:true,apiConfigured:!!c.apiKey,keyPreview:maskKey(c.apiKey),outputDir}); }
    if(p==='/api/config' && req.method==='POST'){ const b=await readBody(req,1024*1024); const apiKey=String(b.apiKey||'').trim(); if(!/^AIza[\w-]{20,}$/.test(apiKey)) return json(res,400,{error:'API key không đúng định dạng.'}); await writeConfig({apiKey}); return json(res,200,{ok:true,keyPreview:maskKey(apiKey)}); }
    if(p==='/api/config' && req.method==='DELETE'){ await writeConfig({}); return json(res,200,{ok:true}); }
    if(p==='/api/open-output' && req.method==='POST'){ if(process.platform==='win32') spawn('explorer.exe',[outputDir],{detached:true,stdio:'ignore'}).unref(); return json(res,200,{ok:true,outputDir}); }
    if(p==='/api/jobs' && req.method==='POST'){
      const b=await readBody(req); const prompt=String(b.prompt||'').trim(); if(!prompt) return json(res,400,{error:'Prompt trống.'});
      const refs=(Array.isArray(b.references)?b.references:[]).slice(0,3).map(parseDataUrl).filter(Boolean); const settings=safeSettings(b,refs.length>0);
      const job={id:randomUUID(),index:Number(b.index||1),prompt,status:'WAITING',settings,references:refs,filename:null,error:null,createdAt:Date.now(),updatedAt:Date.now()}; jobs.set(job.id,job);
      json(res,202,{...job,references:undefined}); queueMicrotask(()=>runJob(job)); return;
    }
    const m=/^\/api\/jobs\/([\w-]+)$/.exec(p);
    if(m && req.method==='GET'){ const j=jobs.get(m[1]); if(!j) return json(res,404,{error:'Không tìm thấy job.'}); return json(res,200,{...j,references:undefined,videoUrl:j.filename?`/output/${encodeURIComponent(j.filename)}`:null}); }
    return serveFile(req,res,p);
  }catch(e){ return json(res,500,{error:normalizeError(e)}); }
});
server.listen(PORT,'127.0.0.1',()=>{
  const url=`http://127.0.0.1:${PORT}`;
  console.log(`VEO AUTO VIDEO STUDIO đang chạy: ${url}`);
  if(process.platform==='win32') setTimeout(()=>spawn('cmd.exe',['/c','start','',url],{detached:true,stdio:'ignore'}).unref(),500);
});

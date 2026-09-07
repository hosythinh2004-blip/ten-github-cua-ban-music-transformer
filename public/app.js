const form = document.querySelector('#convert-form');
const status = document.querySelector('#status');
const submit = form.querySelector('.submit');
const buttonText = submit.querySelector('.button-text');
const urlInput = document.querySelector('#url');

document.querySelectorAll('.format').forEach(label => label.addEventListener('click', () => {
  document.querySelectorAll('.format').forEach(item => item.classList.remove('selected'));
  label.classList.add('selected');
}));

document.querySelector('#paste').addEventListener('click', async () => {
  try { urlInput.value = await navigator.clipboard.readText(); urlInput.focus(); }
  catch { status.textContent = 'Trình duyệt không cho phép đọc clipboard. Hãy dùng Ctrl + V.'; }
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  status.className = 'status';
  status.textContent = 'Đang lấy và chuyển đổi audio…';
  submit.disabled = true;
  buttonText.textContent = 'Đang chuyển đổi…';
  try {
    const format = new FormData(form).get('format');
    const response = await fetch('/api/convert', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ url: urlInput.value.trim(), format })
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'Không thể chuyển đổi tệp.');
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `suno-audio.${format}`;
    const objectUrl = URL.createObjectURL(blob);
    const link = Object.assign(document.createElement('a'), { href: objectUrl, download: filename });
    link.click(); URL.revokeObjectURL(objectUrl);
    status.textContent = 'Hoàn tất! Tệp của bạn đang được tải xuống.';
  } catch (error) {
    status.className = 'status error'; status.textContent = error.message;
  } finally {
    submit.disabled = false; buttonText.textContent = 'Chuyển đổi ngay';
  }
});

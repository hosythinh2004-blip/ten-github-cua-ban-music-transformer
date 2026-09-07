import threading
import time
import urllib.request

import uvicorn

import app_v5
import api_server_v5
from app_v5 import find_free_port

app_v5.APP_VERSION = "5.1.0"


class SunoAudioConverterV51(app_v5.SunoAudioConverter):
    """v5.1: keep the direct engine, but make the embedded API robust inside PyInstaller.

    Uvicorn's default logging config can fail in a one-file/windowed executable because
    its formatter import/configuration is not always available at startup.  The embedded
    API does not need console logging, so we disable Uvicorn's log_config completely.
    """

    def _start_embedded_api(self):
        errors = []
        for attempt in range(1, 4):
            port = find_free_port()
            base = f"http://127.0.0.1:{port}"
            try:
                config = uvicorn.Config(
                    api_server_v5.app,
                    host="127.0.0.1",
                    port=port,
                    log_level="critical",
                    access_log=False,
                    log_config=None,
                    use_colors=False,
                )
                server = uvicorn.Server(config)
                self.api_server = server
                thread = threading.Thread(target=server.run, daemon=True)
                thread.start()

                deadline = time.time() + 15
                last_health_error = ""
                while time.time() < deadline:
                    if not thread.is_alive():
                        last_health_error = "server thread đã dừng"
                        break
                    try:
                        with urllib.request.urlopen(f"{base}/health", timeout=1) as response:
                            if response.status == 200:
                                self.api_port = port
                                self.api_base = base
                                self.api_ready = True
                                self.api_error = ""
                                self.after(
                                    0,
                                    lambda b=base: self.api_status.set(
                                        f"API LOCAL: {b} • tự khởi động ✓"
                                    ),
                                )
                                self.after(0, lambda: self.api_docs_btn.config(state="normal"))
                                self.log(f"✓ API local tự khởi động thành công: {base}")
                                return
                    except Exception as exc:
                        last_health_error = str(exc)
                        time.sleep(0.25)

                try:
                    server.should_exit = True
                except Exception:
                    pass
                errors.append(
                    f"lần {attempt}: /health không sẵn sàng ở {base}"
                    + (f" ({last_health_error})" if last_health_error else "")
                )
            except Exception as exc:
                errors.append(f"lần {attempt}: {type(exc).__name__}: {exc}")

        self.api_ready = False
        self.api_error = " | ".join(errors)
        self.after(
            0,
            lambda: self.api_status.set(
                "API LOCAL: không khởi động được • ENGINE vẫn hoạt động bình thường"
            ),
        )
        self.log(f"⚠ API local không khởi động: {self.api_error}")
        self.log("✓ Engine trực tiếp vẫn dùng được; lỗi API không làm dừng chuyển đổi.")


if __name__ == "__main__":
    SunoAudioConverterV51().mainloop()

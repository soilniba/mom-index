"""静态看板服务：每次使用缓存前重验证，避免 HTML/JS/数据版本错配。

沿用 http.server 的文件服务与条件请求，从 frontend/ 工作目录启动。
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer, test


class RevalidatingHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


if __name__ == '__main__':
    test(HandlerClass=RevalidatingHandler, ServerClass=ThreadingHTTPServer,
         port=8765, bind='::')

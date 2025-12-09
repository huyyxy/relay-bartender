#!/usr/bin/env python3
"""
Relay Bartender - OpenAI API 兼容中继服务器
基于 Tornado 实现的 API 中继，支持配置后端地址、API Key 和模型名称覆盖
"""

import json
import logging
import argparse
from typing import Optional
from urllib.parse import urljoin

import yaml
import httpx
import tornado.ioloop
import tornado.web

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Config:
    """配置管理类"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.load()
    
    def load(self):
        """加载配置文件"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"配置文件 {self.config_path} 不存在，使用默认配置")
            config = {}
        
        # 服务器配置
        server_config = config.get('server', {})
        self.port = server_config.get('port', 8080)
        self.host = server_config.get('host', '0.0.0.0')
        
        # 后端配置
        backend_config = config.get('backend', {})
        self.backend_base_url = backend_config.get('base_url', 'https://api.openai.com')
        self.backend_api_key = backend_config.get('api_key', '')
        
        # 模型覆盖配置
        model_config = config.get('model', {})
        self.model_override = model_config.get('override', None)
        self.model_mapping = model_config.get('mapping', {})
        
        # 请求配置
        request_config = config.get('request', {})
        self.request_timeout = request_config.get('timeout', 120)
        self.connect_timeout = request_config.get('connect_timeout', 30)
        
        logger.info(f"配置已加载: 后端={self.backend_base_url}, 端口={self.port}")
        if self.model_override:
            logger.info(f"模型覆盖: {self.model_override}")
        if self.model_mapping:
            logger.info(f"模型映射: {self.model_mapping}")


class RelayHandler(tornado.web.RequestHandler):
    """API 中继处理器"""
    
    SUPPORTED_METHODS = ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS')
    
    def initialize(self, config: Config, http_client: httpx.AsyncClient):
        self.config = config
        self.http_client = http_client
    
    def set_default_headers(self):
        """设置 CORS 头"""
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.set_header("Access-Control-Max-Age", "3600")
    
    async def options(self, *args, **kwargs):
        """处理 CORS 预检请求"""
        self.set_status(204)
        self.finish()
    
    def _get_backend_url(self) -> str:
        """构建后端 URL"""
        path = self.request.path
        query = self.request.query
        
        backend_url = urljoin(self.config.backend_base_url, path)
        if query:
            backend_url = f"{backend_url}?{query}"
        
        return backend_url
    
    def _get_backend_headers(self) -> dict:
        """构建后端请求头"""
        headers = {}
        
        # 复制原始请求头，排除 Host 和可能冲突的头
        skip_headers = {'host', 'content-length', 'transfer-encoding', 'connection'}
        for name, value in self.request.headers.get_all():
            if name.lower() not in skip_headers:
                # 如果配置了后端 API Key，替换 Authorization 头
                if name.lower() == 'authorization' and self.config.backend_api_key:
                    headers[name] = f"Bearer {self.config.backend_api_key}"
                else:
                    headers[name] = value
        
        # 如果原始请求没有 Authorization 但配置了后端 API Key
        if 'authorization' not in [h.lower() for h in self.request.headers.keys()]:
            if self.config.backend_api_key:
                headers['Authorization'] = f"Bearer {self.config.backend_api_key}"
        
        return headers
    
    def _process_request_body(self, body: bytes) -> bytes:
        """处理请求体，进行模型名称覆盖"""
        if not body:
            return body
        
        try:
            data = json.loads(body)
            
            # 检查是否需要覆盖模型
            if 'model' in data:
                original_model = data['model']
                
                # 优先使用强制覆盖
                if self.config.model_override:
                    data['model'] = self.config.model_override
                    logger.info(f"模型覆盖: {original_model} -> {self.config.model_override}")
                # 其次使用模型映射
                elif original_model in self.config.model_mapping:
                    mapped_model = self.config.model_mapping[original_model]
                    data['model'] = mapped_model
                    logger.info(f"模型映射: {original_model} -> {mapped_model}")
            
            return json.dumps(data, ensure_ascii=False).encode('utf-8')
        except (json.JSONDecodeError, UnicodeDecodeError):
            # 如果不是 JSON 或解析失败，返回原始内容
            return body
    
    async def _proxy_request(self, method: str):
        """代理请求到后端"""
        backend_url = self._get_backend_url()
        headers = self._get_backend_headers()
        body = self._process_request_body(self.request.body) if self.request.body else None
        
        logger.info(f"转发请求: {method} {backend_url}")
        
        # 检查是否是流式请求
        is_streaming = False
        if body:
            try:
                data = json.loads(body)
                is_streaming = data.get('stream', False)
            except:
                pass
        
        try:
            if is_streaming:
                # 流式响应处理
                await self._handle_streaming_request(method, backend_url, headers, body)
            else:
                # 普通请求处理
                response = await self.http_client.request(
                    method=method,
                    url=backend_url,
                    headers=headers,
                    content=body,
                )
                
                # 设置响应状态码
                self.set_status(response.status_code)
                
                # 复制响应头
                skip_headers = {'transfer-encoding', 'content-length', 'connection', 'content-encoding'}
                for name, value in response.headers.items():
                    if name.lower() not in skip_headers:
                        self.set_header(name, value)
                
                # 写入响应体
                self.write(response.content)
                self.finish()
                
        except httpx.TimeoutException as e:
            logger.error(f"请求超时: {str(e)}")
            self.set_status(504)
            self.write({
                "error": {
                    "message": f"Backend request timeout: {str(e)}",
                    "type": "relay_error",
                    "code": "timeout"
                }
            })
            self.finish()
        except httpx.RequestError as e:
            logger.error(f"请求失败: {str(e)}")
            self.set_status(502)
            self.write({
                "error": {
                    "message": f"Backend request failed: {str(e)}",
                    "type": "relay_error",
                    "code": "backend_error"
                }
            })
            self.finish()
    
    async def _handle_streaming_request(self, method: str, url: str, headers: dict, body: Optional[bytes]):
        """处理流式请求"""
        try:
            async with self.http_client.stream(
                method=method,
                url=url,
                headers=headers,
                content=body,
            ) as response:
                # 设置响应状态码
                self.set_status(response.status_code)
                
                # 复制响应头
                skip_headers = {'transfer-encoding', 'content-length', 'connection', 'content-encoding'}
                for name, value in response.headers.items():
                    if name.lower() not in skip_headers:
                        self.set_header(name, value)
                
                # 流式写入响应
                async for chunk in response.aiter_bytes():
                    self.write(chunk)
                    await self.flush()
                
        except httpx.TimeoutException as e:
            logger.error(f"流式请求超时: {e}")
            self.set_status(504)
            self.write({
                "error": {
                    "message": f"Backend streaming timeout: {str(e)}",
                    "type": "relay_error",
                    "code": "timeout"
                }
            })
        except httpx.RequestError as e:
            logger.error(f"流式请求失败: {e}")
            self.set_status(502)
            self.write({
                "error": {
                    "message": f"Backend streaming failed: {str(e)}",
                    "type": "relay_error",
                    "code": "backend_error"
                }
            })
        finally:
            self.finish()
    
    async def get(self, *args, **kwargs):
        await self._proxy_request('GET')
    
    async def post(self, *args, **kwargs):
        await self._proxy_request('POST')
    
    async def put(self, *args, **kwargs):
        await self._proxy_request('PUT')
    
    async def delete(self, *args, **kwargs):
        await self._proxy_request('DELETE')
    
    async def patch(self, *args, **kwargs):
        await self._proxy_request('PATCH')


class HealthHandler(tornado.web.RequestHandler):
    """健康检查处理器"""
    
    def get(self):
        self.write({"status": "ok", "service": "relay-bartender"})


def make_app(config: Config, http_client: httpx.AsyncClient) -> tornado.web.Application:
    """创建 Tornado 应用"""
    return tornado.web.Application([
        (r"/health", HealthHandler),
        (r".*", RelayHandler, {"config": config, "http_client": http_client}),
    ])


async def main_async():
    """异步主函数"""
    parser = argparse.ArgumentParser(description='Relay Bartender - OpenAI API 兼容中继服务器')
    parser.add_argument('-c', '--config', default='config.yaml', help='配置文件路径')
    parser.add_argument('-p', '--port', type=int, help='覆盖配置文件中的监听端口')
    parser.add_argument('--host', help='覆盖配置文件中的监听地址')
    args = parser.parse_args()
    
    # 加载配置
    config = Config(args.config)
    
    # 命令行参数覆盖
    if args.port:
        config.port = args.port
    if args.host:
        config.host = args.host
    
    # 创建 httpx 异步客户端
    timeout = httpx.Timeout(
        timeout=config.request_timeout,
        connect=config.connect_timeout
    )
    
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http_client:
        # 创建并启动应用
        app = make_app(config, http_client)
        app.listen(config.port, config.host)
        
        logger.info(f"🍸 Relay Bartender 启动成功!")
        logger.info(f"📡 监听地址: http://{config.host}:{config.port}")
        logger.info(f"🎯 后端地址: {config.backend_base_url}")
        logger.info(f"❤️  健康检查: http://{config.host}:{config.port}/health")
        
        # 保持运行
        shutdown_event = tornado.locks.Event()
        await shutdown_event.wait()


def main():
    """主函数"""
    try:
        tornado.ioloop.IOLoop.current().run_sync(main_async)
    except KeyboardInterrupt:
        logger.info("服务器已停止")


if __name__ == "__main__":
    main()

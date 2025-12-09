import tornado.web
import httpx
import time
import uuid
from typing import Optional
from urllib.parse import urljoin
from config import Config
from utils import format_headers_for_log, format_json_body, format_streaming_body
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RelayHandler(tornado.web.RequestHandler):
    """API 中继处理器"""
    
    SUPPORTED_METHODS = ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS')
    
    def initialize(self, config: Config, http_client: httpx.AsyncClient):
        self.config = config
        self.http_client = http_client
        self.request_id = str(uuid.uuid4())[:8]
        self.start_time = time.time()
    
    def _get_client_ip(self) -> str:
        """获取客户端 IP 地址"""
        # 尝试从 X-Forwarded-For 或 X-Real-IP 获取真实 IP
        x_forwarded_for = self.request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        x_real_ip = self.request.headers.get("X-Real-IP")
        if x_real_ip:
            return x_real_ip
        return self.request.remote_ip or "unknown"
    
    def _log_request(self, method: str, backend_url: str, headers: dict, body: Optional[bytes]):
        """记录请求日志"""
        if not self.config.log_enabled:
            return
        
        client_ip = self._get_client_ip()
        log_parts = [
            f"\n{'='*60}",
            f"📥 请求 [{self.request_id}]",
            f"{'='*60}",
            f"客户端: {client_ip}",
            f"方法: {method}",
            f"原始路径: {self.request.path}",
            f"后端URL: {backend_url}",
        ]
        
        if self.config.log_request_headers:
            formatted_headers = format_headers_for_log(headers, self.config.log_mask_sensitive)
            log_parts.append(f"请求头: {json.dumps(formatted_headers, ensure_ascii=False, indent=2)}")
        
        if self.config.log_request_body and body:
            formatted_body = format_json_body(body, self.config.log_max_body_length)
            log_parts.append(f"请求体:\n{formatted_body}")
        
        logger.info("\n".join(log_parts))
    
    def _log_response(self, status_code: int, headers: dict, body: Optional[bytes] = None, 
                      is_streaming: bool = False, stream_bytes: int = 0):
        """记录响应日志"""
        if not self.config.log_enabled:
            return
        
        elapsed_time = (time.time() - self.start_time) * 1000  # 转换为毫秒
        
        log_parts = [
            f"\n{'='*60}",
            f"📤 响应 [{self.request_id}]",
            f"{'='*60}",
            f"状态码: {status_code}",
            f"耗时: {elapsed_time:.2f}ms",
        ]
        
        if is_streaming:
            log_parts.append(f"类型: 流式响应")
            log_parts.append(f"传输字节: {stream_bytes}")
        else:
            log_parts.append(f"类型: 普通响应")
        
        if self.config.log_response_headers:
            formatted_headers = format_headers_for_log(dict(headers), self.config.log_mask_sensitive)
            log_parts.append(f"响应头: {json.dumps(formatted_headers, ensure_ascii=False, indent=2)}")
        
        if self.config.log_response_body and body:
            if is_streaming:
                formatted_body = format_streaming_body(body, self.config.log_max_body_length)
            else:
                formatted_body = format_json_body(body, self.config.log_max_body_length)
            log_parts.append(f"响应体:\n{formatted_body}")
        
        log_parts.append(f"{'='*60}")
        
        logger.info("\n".join(log_parts))
    
    def _log_error(self, error_type: str, error_message: str):
        """记录错误日志"""
        elapsed_time = (time.time() - self.start_time) * 1000
        logger.error(
            f"\n❌ 错误 [{self.request_id}] - {error_type}\n"
            f"   耗时: {elapsed_time:.2f}ms\n"
            f"   详情: {error_message}"
        )
    
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
        
        # 检测 path 中是否包含 v1，如果有则提取 v1 之后的部分
        if '/v1' in path:
            # 找到 v1 的位置，提取 v1 之后的路径部分
            v1_index = path.find('/v1')
            path_after_v1 = path[v1_index + 3:]  # +3 跳过 '/v1'
            # 确保 backend_base_url 以 / 结尾时不会产生双斜杠
            base_url = self.config.backend_base_url.rstrip('/')
            backend_url = base_url + path_after_v1
        else:
            backend_url = urljoin(self.config.backend_base_url, path)
        
        if query:
            backend_url = f"{backend_url}?{query}"
        
        return backend_url
    
    def _get_backend_headers(self) -> dict:
        """构建后端请求头"""
        headers = {}
        
        # 复制原始请求头，排除 Host 和可能冲突的头
        skip_headers = {'host', 'content-length', 'transfer-encoding', 'connection', 'accept-encoding'}
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
                # 其次使用模型映射（确保 model_mapping 是字典且不为空）
                elif (self.config.model_mapping and 
                      isinstance(self.config.model_mapping, dict) and 
                      original_model in self.config.model_mapping):
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
        
        # 检查是否是流式请求
        is_streaming = False
        if body:
            try:
                data = json.loads(body)
                is_streaming = data.get('stream', False)
            except:
                pass
        
        # 记录请求日志
        self._log_request(method, backend_url, headers, body)
        
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
                response_headers = {}
                for name, value in response.headers.items():
                    if name.lower() not in skip_headers:
                        self.set_header(name, value)
                        response_headers[name] = value
                
                # 记录响应日志
                self._log_response(
                    status_code=response.status_code,
                    headers=response_headers,
                    body=response.content,
                    is_streaming=False
                )
                
                # 写入响应体
                self.write(response.content)
                self.finish()
                
        except httpx.TimeoutException as e:
            self._log_error("请求超时", str(e))
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
            self._log_error("请求失败", str(e))
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
        total_bytes = 0
        response_headers = {}
        status_code = 0
        collected_chunks = []  # 收集所有 chunk 用于日志记录
        
        try:
            async with self.http_client.stream(
                method=method,
                url=url,
                headers=headers,
                content=body,
            ) as response:
                # 设置响应状态码
                status_code = response.status_code
                self.set_status(response.status_code)
                
                # 复制响应头
                skip_headers = {'transfer-encoding', 'content-length', 'connection', 'content-encoding'}
                for name, value in response.headers.items():
                    if name.lower() not in skip_headers:
                        self.set_header(name, value)
                        response_headers[name] = value
                
                # 流式写入响应，同时收集用于日志
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    collected_chunks.append(chunk)
                    self.write(chunk)
                    await self.flush()
                
                # 合并所有 chunk 用于日志记录
                full_response = b''.join(collected_chunks) if self.config.log_response_body else None
                
                # 记录流式响应日志
                self._log_response(
                    status_code=status_code,
                    headers=response_headers,
                    body=full_response,
                    is_streaming=True,
                    stream_bytes=total_bytes
                )
                
        except httpx.TimeoutException as e:
            self._log_error("流式请求超时", str(e))
            self.set_status(504)
            self.write({
                "error": {
                    "message": f"Backend streaming timeout: {str(e)}",
                    "type": "relay_error",
                    "code": "timeout"
                }
            })
        except httpx.RequestError as e:
            self._log_error("流式请求失败", str(e))
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

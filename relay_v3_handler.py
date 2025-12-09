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


class RelayV3Handler(tornado.web.RequestHandler):
    """
    自适应 API 中继处理器
    
    自动检测请求格式：
    - 如果是 Chat Completion API 格式 (有 messages 字段)，直接透传
    - 如果是 Response API 格式 (有 input 字段)，转换为 Chat Completion 格式后发送，响应也相应转换
    """
    
    SUPPORTED_METHODS = ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS')
    
    def initialize(self, config: Config, http_client: httpx.AsyncClient):
        self.config = config
        self.http_client = http_client
        self.request_id = str(uuid.uuid4())[:8]
        self.start_time = time.time()
        self._is_response_api_format = False  # 标记原始请求是否为 Response API 格式
    
    def _get_client_ip(self) -> str:
        """获取客户端 IP 地址"""
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
        format_type = "Response API -> Chat Completion" if self._is_response_api_format else "Chat Completion (透传)"
        log_parts = [
            f"\n{'='*60}",
            f"📥 V3请求 [{self.request_id}] ({format_type})",
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
        
        elapsed_time = (time.time() - self.start_time) * 1000
        
        log_parts = [
            f"\n{'='*60}",
            f"📤 V3响应 [{self.request_id}]",
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
            f"\n❌ V3错误 [{self.request_id}] - {error_type}\n"
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
        skip_headers = {'host', 'content-length', 'transfer-encoding', 'connection', 'accept-encoding'}
        
        for name, value in self.request.headers.get_all():
            if name.lower() not in skip_headers:
                if name.lower() == 'authorization' and self.config.backend_api_key:
                    headers[name] = f"Bearer {self.config.backend_api_key}"
                else:
                    headers[name] = value
        
        if 'authorization' not in [h.lower() for h in self.request.headers.keys()]:
            if self.config.backend_api_key:
                headers['Authorization'] = f"Bearer {self.config.backend_api_key}"
        
        return headers
    
    def _detect_request_format(self, data: dict) -> bool:
        """
        检测请求格式是否为 Response API 格式
        
        Response API 特征:
        - 有 'input' 字段 (而非 'messages')
        - 可能有 'instructions' 字段 (而非 system message)
        - 使用 'max_output_tokens' (而非 'max_tokens')
        
        Chat Completion API 特征:
        - 有 'messages' 字段
        - 使用 'max_tokens'
        
        Returns:
            True 如果是 Response API 格式，False 如果是 Chat Completion 格式
        """
        # 明确的 Response API 标志
        if 'input' in data:
            return True
        
        # 明确的 Chat Completion API 标志
        if 'messages' in data:
            return False
        
        # 其他 Response API 特有字段
        if 'instructions' in data or 'max_output_tokens' in data:
            return True
        
        # 默认按 Chat Completion 格式处理
        return False
    
    def _apply_model_override(self, model: str) -> str:
        """应用模型覆盖/映射"""
        original_model = model
        
        if self.config.model_override:
            model = self.config.model_override
            logger.info(f"V3模型覆盖: {original_model} -> {model}")
        elif (self.config.model_mapping and 
              isinstance(self.config.model_mapping, dict) and 
              original_model in self.config.model_mapping):
            model = self.config.model_mapping[original_model]
            logger.info(f"V3模型映射: {original_model} -> {model}")
        
        return model
    
    def _convert_response_api_to_chat_completion(self, request_data: dict) -> dict:
        """
        将 Response API 请求格式转换为 Chat Completion API 格式
        """
        chat_request = {}
        
        # 复制模型名称
        if 'model' in request_data:
            chat_request['model'] = self._apply_model_override(request_data['model'])
        
        # 构建 messages 数组
        messages = []
        
        # 处理 instructions (转换为 system message)
        if 'instructions' in request_data:
            messages.append({
                "role": "system",
                "content": request_data['instructions']
            })
        
        # 处理 input
        input_data = request_data.get('input', [])
        if isinstance(input_data, str):
            messages.append({
                "role": "user",
                "content": input_data
            })
        elif isinstance(input_data, list):
            for item in input_data:
                if isinstance(item, dict):
                    msg = self._convert_input_message(item)
                    if msg:
                        messages.append(msg)
                elif isinstance(item, str):
                    messages.append({
                        "role": "user",
                        "content": item
                    })
        
        chat_request['messages'] = messages
        
        # 转换 tools 格式 (Response API -> Chat Completion API)
        if 'tools' in request_data:
            chat_request['tools'] = self._convert_tools_to_chat_completion(request_data['tools'])
        
        # 直接复制的参数（不需要格式转换的）
        copy_params = ['tool_choice', 'temperature', 'top_p', 'stream', 
                       'stop', 'presence_penalty', 'frequency_penalty', 'logit_bias',
                       'user', 'seed', 'response_format', 'parallel_tool_calls']
        for param in copy_params:
            if param in request_data:
                chat_request[param] = request_data[param]
        
        # 参数名称转换
        if 'max_output_tokens' in request_data:
            chat_request['max_tokens'] = request_data['max_output_tokens']
        
        return chat_request
    
    def _convert_tools_to_chat_completion(self, tools: list) -> list:
        """
        将 Response API 的 tools 格式转换为 Chat Completion API 格式
        
        Response API 格式:
        {
            "type": "function",
            "name": "get_weather",
            "description": "获取天气",
            "parameters": {...}
        }
        
        Chat Completion API 格式:
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取天气",
                "parameters": {...}
            }
        }
        """
        if not tools:
            return tools
        
        converted_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            
            tool_type = tool.get('type', 'function')
            
            # 检查是否已经是 Chat Completion 格式（有 function 字段）
            if 'function' in tool:
                # 已经是 Chat Completion 格式，直接复制
                converted_tools.append(tool)
            elif tool_type == 'function':
                # Response API 格式，需要转换
                converted_tool = {
                    "type": "function",
                    "function": {
                        "name": tool.get('name', ''),
                        "description": tool.get('description', ''),
                    }
                }
                # parameters 可能为空，只有存在时才添加
                if 'parameters' in tool:
                    converted_tool['function']['parameters'] = tool['parameters']
                
                converted_tools.append(converted_tool)
            else:
                # 其他类型的 tool，保持原样
                converted_tools.append(tool)
        
        return converted_tools
    
    def _convert_input_message(self, item: dict) -> Optional[dict]:
        """转换 Response API 的输入消息为 Chat Completion 消息格式"""
        item_type = item.get('type')
        
        if item_type == 'message':
            role = item.get('role', 'user')
            content = item.get('content', [])
            
            if isinstance(content, str):
                return {"role": role, "content": content}
            elif isinstance(content, list):
                converted_content = self._convert_content_array(content)
                if converted_content:
                    return {"role": role, "content": converted_content}
        
        elif item_type == 'function_call_output':
            return {
                "role": "tool",
                "tool_call_id": item.get('call_id', ''),
                "content": item.get('output', '')
            }
        
        elif item_type is None and 'role' in item:
            role = item.get('role', 'user')
            content = item.get('content', '')
            
            if isinstance(content, str):
                return {"role": role, "content": content}
            elif isinstance(content, list):
                converted_content = self._convert_content_array(content)
                if converted_content:
                    return {"role": role, "content": converted_content}
        
        return None
    
    def _convert_content_array(self, content_array: list):
        """转换 Response API 的 content 数组"""
        if not content_array:
            return ""
        
        all_text = all(
            (isinstance(item, dict) and item.get('type') in ['input_text', 'text']) or 
            isinstance(item, str)
            for item in content_array
        )
        
        if all_text:
            texts = []
            for item in content_array:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict):
                    texts.append(item.get('text', ''))
            return ''.join(texts)
        
        converted = []
        for item in content_array:
            if isinstance(item, str):
                converted.append({"type": "text", "text": item})
            elif isinstance(item, dict):
                item_type = item.get('type', '')
                
                if item_type in ['input_text', 'text']:
                    converted.append({"type": "text", "text": item.get('text', '')})
                
                elif item_type in ['input_image', 'image_url']:
                    if 'image_url' in item:
                        image_data = item['image_url']
                        if isinstance(image_data, str):
                            converted.append({
                                "type": "image_url",
                                "image_url": {"url": image_data}
                            })
                        elif isinstance(image_data, dict):
                            converted.append({
                                "type": "image_url", 
                                "image_url": image_data
                            })
                    elif 'url' in item:
                        converted.append({
                            "type": "image_url",
                            "image_url": {"url": item['url'], "detail": item.get('detail', 'auto')}
                        })
                
                elif item_type == 'output_text':
                    converted.append({"type": "text", "text": item.get('text', '')})
        
        return converted if converted else ""
    
    def _process_chat_completion_body(self, body: bytes) -> bytes:
        """处理 Chat Completion 请求体，进行模型名称覆盖"""
        if not body:
            return body
        
        try:
            data = json.loads(body)
            
            if 'model' in data:
                data['model'] = self._apply_model_override(data['model'])
            
            return json.dumps(data, ensure_ascii=False).encode('utf-8')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body
    
    def _convert_chat_completion_to_response_api(self, chat_response: dict, original_request: dict) -> dict:
        """将 Chat Completion API 响应转换为 Response API 格式"""
        response_id = f"resp_{chat_response.get('id', str(uuid.uuid4())[:24])}"
        
        output = []
        choices = chat_response.get('choices', [])
        
        for i, choice in enumerate(choices):
            message = choice.get('message', {})
            finish_reason = choice.get('finish_reason', 'stop')
            
            if finish_reason == 'stop':
                status = 'completed'
            elif finish_reason == 'tool_calls':
                status = 'completed'
            elif finish_reason == 'length':
                status = 'incomplete'
            elif finish_reason == 'content_filter':
                status = 'failed'
            else:
                status = 'completed'
            
            content = []
            if message.get('content'):
                content.append({
                    "type": "output_text",
                    "text": message['content']
                })
            
            msg_output = {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "status": status,
                "role": "assistant",
                "content": content
            }
            output.append(msg_output)
            
            tool_calls = message.get('tool_calls', [])
            for tool_call in tool_calls:
                if tool_call.get('type') == 'function':
                    function_call_output = {
                        "type": "function_call",
                        "id": f"fc_{uuid.uuid4().hex[:24]}",
                        "call_id": tool_call.get('id', ''),
                        "name": tool_call.get('function', {}).get('name', ''),
                        "arguments": tool_call.get('function', {}).get('arguments', '{}'),
                        "status": "completed"
                    }
                    output.append(function_call_output)
        
        usage = {}
        if 'usage' in chat_response:
            chat_usage = chat_response['usage']
            usage = {
                "input_tokens": chat_usage.get('prompt_tokens', 0),
                "output_tokens": chat_usage.get('completion_tokens', 0),
                "total_tokens": chat_usage.get('total_tokens', 0)
            }
        
        response = {
            "id": response_id,
            "object": "response",
            "created_at": chat_response.get('created', int(time.time())),
            "status": "completed",
            "model": chat_response.get('model', original_request.get('model', '')),
            "output": output,
            "usage": usage
        }
        
        return response
    
    def _convert_chat_completion_chunk_to_response_events(self, chunk_data: dict) -> list:
        """将 Chat Completion 流式 chunk 转换为 Response API 的 SSE 事件"""
        events = []
        
        choices = chunk_data.get('choices', [])
        if not choices:
            return events
        
        choice = choices[0]
        delta = choice.get('delta', {})
        finish_reason = choice.get('finish_reason')
        
        if 'content' in delta and delta['content']:
            events.append({
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": delta['content']
            })
        
        if 'tool_calls' in delta:
            for tool_call in delta['tool_calls']:
                index = tool_call.get('index', 0)
                if 'function' in tool_call:
                    func = tool_call['function']
                    if 'arguments' in func and func['arguments']:
                        events.append({
                            "type": "response.function_call_arguments.delta",
                            "output_index": index + 1,
                            "delta": func['arguments']
                        })
        
        if finish_reason:
            if finish_reason == 'stop':
                events.append({
                    "type": "response.output_text.done",
                    "output_index": 0,
                    "content_index": 0
                })
            elif finish_reason == 'tool_calls':
                events.append({
                    "type": "response.function_call_arguments.done",
                    "output_index": 0
                })
        
        return events
    
    async def _proxy_request(self, method: str):
        """代理请求到后端"""
        body = self.request.body
        original_request = {}
        
        # 解析请求体并检测格式
        if body and method.upper() == 'POST':
            try:
                original_request = json.loads(body)
                self._is_response_api_format = self._detect_request_format(original_request)
                
                if self._is_response_api_format:
                    # Response API 格式，转换为 Chat Completion 格式
                    logger.info(f"[{self.request_id}] 检测到 Response API 格式，执行转换")
                    chat_request = self._convert_response_api_to_chat_completion(original_request)
                    body = json.dumps(chat_request, ensure_ascii=False).encode('utf-8')
                else:
                    # Chat Completion 格式，只做模型覆盖
                    logger.info(f"[{self.request_id}] 检测到 Chat Completion 格式，透传")
                    body = self._process_chat_completion_body(body)
            except json.JSONDecodeError:
                pass
        
        backend_url = self._get_backend_url()
        headers = self._get_backend_headers()
        
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
                if self._is_response_api_format:
                    await self._handle_streaming_response_api(method, backend_url, headers, body, original_request)
                else:
                    await self._handle_streaming_passthrough(method, backend_url, headers, body)
            else:
                if self._is_response_api_format:
                    await self._handle_normal_response_api(method, backend_url, headers, body, original_request)
                else:
                    await self._handle_normal_passthrough(method, backend_url, headers, body)
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
    
    async def _handle_normal_passthrough(self, method: str, url: str, headers: dict, body: Optional[bytes]):
        """处理普通（非流式）透传请求"""
        response = await self.http_client.request(
            method=method,
            url=url,
            headers=headers,
            content=body,
        )
        
        self.set_status(response.status_code)
        
        skip_headers = {'transfer-encoding', 'content-length', 'connection', 'content-encoding'}
        response_headers = {}
        for name, value in response.headers.items():
            if name.lower() not in skip_headers:
                self.set_header(name, value)
                response_headers[name] = value
        
        self._log_response(
            status_code=response.status_code,
            headers=response_headers,
            body=response.content,
            is_streaming=False
        )
        
        self.write(response.content)
        self.finish()
    
    async def _handle_streaming_passthrough(self, method: str, url: str, headers: dict, body: Optional[bytes]):
        """处理流式透传请求"""
        total_bytes = 0
        response_headers = {}
        collected_chunks = []
        
        try:
            async with self.http_client.stream(
                method=method,
                url=url,
                headers=headers,
                content=body,
            ) as response:
                self.set_status(response.status_code)
                
                skip_headers = {'transfer-encoding', 'content-length', 'connection', 'content-encoding'}
                for name, value in response.headers.items():
                    if name.lower() not in skip_headers:
                        self.set_header(name, value)
                        response_headers[name] = value
                
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    collected_chunks.append(chunk)
                    self.write(chunk)
                    await self.flush()
                
                full_response = b''.join(collected_chunks) if self.config.log_response_body else None
                self._log_response(
                    status_code=response.status_code,
                    headers=response_headers,
                    body=full_response,
                    is_streaming=True,
                    stream_bytes=total_bytes
                )
        finally:
            self.finish()
    
    async def _handle_normal_response_api(self, method: str, url: str, headers: dict, body: bytes, original_request: dict):
        """处理普通（非流式）Response API 请求"""
        response = await self.http_client.request(
            method=method,
            url=url,
            headers=headers,
            content=body,
        )
        
        self.set_status(response.status_code)
        
        skip_headers = {'transfer-encoding', 'content-length', 'connection', 'content-encoding'}
        response_headers = {}
        for name, value in response.headers.items():
            if name.lower() not in skip_headers:
                self.set_header(name, value)
                response_headers[name] = value
        
        if response.status_code == 200:
            try:
                chat_response = response.json()
                
                # 打印后端输出（Chat Completion 格式）
                logger.info(
                    f"\n{'='*60}\n"
                    f"🔄 [{self.request_id}] 转换前 - 后端输出 (Chat Completion)\n"
                    f"{'='*60}\n"
                    f"{json.dumps(chat_response, ensure_ascii=False, indent=2)}"
                )
                
                response_api_response = self._convert_chat_completion_to_response_api(
                    chat_response, original_request
                )
                
                # 打印前端输出（Response API 格式）
                logger.info(
                    f"\n{'='*60}\n"
                    f"🔄 [{self.request_id}] 转换后 - 前端输出 (Response API)\n"
                    f"{'='*60}\n"
                    f"{json.dumps(response_api_response, ensure_ascii=False, indent=2)}"
                )
                
                response_body = json.dumps(response_api_response, ensure_ascii=False).encode('utf-8')
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"响应转换失败，返回原始响应: {e}")
                response_body = response.content
        else:
            response_body = response.content
        
        self._log_response(
            status_code=response.status_code,
            headers=response_headers,
            body=response_body,
            is_streaming=False
        )
        
        self.write(response_body)
        self.finish()
    
    async def _handle_streaming_response_api(self, method: str, url: str, headers: dict, body: bytes, original_request: dict):
        """处理流式 Response API 请求"""
        total_bytes = 0
        response_headers = {}
        collected_chunks = []
        
        accumulated_content = ""
        accumulated_tool_calls = []
        response_id = None
        model_name = None
        created_time = None
        
        try:
            async with self.http_client.stream(
                method=method,
                url=url,
                headers=headers,
                content=body,
            ) as response:
                self.set_status(response.status_code)
                
                self.set_header('Content-Type', 'text/event-stream')
                self.set_header('Cache-Control', 'no-cache')
                self.set_header('Connection', 'keep-alive')
                
                skip_headers = {'transfer-encoding', 'content-length', 'connection', 'content-encoding', 'content-type'}
                for name, value in response.headers.items():
                    if name.lower() not in skip_headers:
                        self.set_header(name, value)
                        response_headers[name] = value
                
                is_first_chunk = True
                buffer = ""
                
                async for chunk in response.aiter_text():
                    buffer += chunk
                    
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        
                        if not line:
                            continue
                        
                        if line.startswith('data: '):
                            data_content = line[6:]
                            
                            if data_content == '[DONE]':
                                done_event = {
                                    "type": "response.completed",
                                    "response": {
                                        "id": response_id or f"resp_{uuid.uuid4().hex[:24]}",
                                        "object": "response",
                                        "created_at": created_time or int(time.time()),
                                        "status": "completed",
                                        "model": model_name or original_request.get('model', ''),
                                        "output": [{
                                            "type": "message",
                                            "id": f"msg_{uuid.uuid4().hex[:24]}",
                                            "status": "completed",
                                            "role": "assistant",
                                            "content": [{"type": "output_text", "text": accumulated_content}] if accumulated_content else []
                                        }]
                                    }
                                }
                                
                                # 流式完成时打印汇总日志
                                logger.info(
                                    f"\n{'='*60}\n"
                                    f"🔄 [{self.request_id}] 流式转换完成前 - 后端累积内容 (Chat Completion)\n"
                                    f"{'='*60}\n"
                                    f"累积文本: {accumulated_content}\n"
                                    f"累积tool_calls: {json.dumps(accumulated_tool_calls, ensure_ascii=False) if accumulated_tool_calls else '无'}"
                                )
                                logger.info(
                                    f"\n{'='*60}\n"
                                    f"🔄 [{self.request_id}] 流式转换完成后 - 前端输出 (Response API)\n"
                                    f"{'='*60}\n"
                                    f"{json.dumps(done_event, ensure_ascii=False, indent=2)}"
                                )
                                
                                sse_data = f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"
                                self.write(sse_data)
                                await self.flush()
                                total_bytes += len(sse_data.encode('utf-8'))
                                collected_chunks.append(sse_data.encode('utf-8'))
                                continue
                            
                            try:
                                chunk_data = json.loads(data_content)
                                
                                if not response_id:
                                    response_id = f"resp_{chunk_data.get('id', uuid.uuid4().hex[:24])}"
                                if not model_name:
                                    model_name = chunk_data.get('model')
                                if not created_time:
                                    created_time = chunk_data.get('created')
                                
                                choices = chunk_data.get('choices', [])
                                if choices:
                                    delta = choices[0].get('delta', {})
                                    if 'content' in delta and delta['content']:
                                        accumulated_content += delta['content']
                                    if 'tool_calls' in delta:
                                        accumulated_tool_calls.extend(delta['tool_calls'])
                                
                                if is_first_chunk:
                                    created_event = {
                                        "type": "response.created",
                                        "response": {
                                            "id": response_id,
                                            "object": "response",
                                            "created_at": created_time or int(time.time()),
                                            "status": "in_progress",
                                            "model": model_name or original_request.get('model', ''),
                                            "output": []
                                        }
                                    }
                                    sse_data = f"data: {json.dumps(created_event, ensure_ascii=False)}\n\n"
                                    self.write(sse_data)
                                    await self.flush()
                                    total_bytes += len(sse_data.encode('utf-8'))
                                    collected_chunks.append(sse_data.encode('utf-8'))
                                    
                                    item_added_event = {
                                        "type": "response.output_item.added",
                                        "output_index": 0,
                                        "item": {
                                            "type": "message",
                                            "id": f"msg_{uuid.uuid4().hex[:24]}",
                                            "status": "in_progress",
                                            "role": "assistant",
                                            "content": []
                                        }
                                    }
                                    sse_data = f"data: {json.dumps(item_added_event, ensure_ascii=False)}\n\n"
                                    self.write(sse_data)
                                    await self.flush()
                                    total_bytes += len(sse_data.encode('utf-8'))
                                    collected_chunks.append(sse_data.encode('utf-8'))
                                    
                                    content_added_event = {
                                        "type": "response.content_part.added",
                                        "output_index": 0,
                                        "content_index": 0,
                                        "part": {
                                            "type": "output_text",
                                            "text": ""
                                        }
                                    }
                                    sse_data = f"data: {json.dumps(content_added_event, ensure_ascii=False)}\n\n"
                                    self.write(sse_data)
                                    await self.flush()
                                    total_bytes += len(sse_data.encode('utf-8'))
                                    collected_chunks.append(sse_data.encode('utf-8'))
                                    
                                    is_first_chunk = False
                                
                                events = self._convert_chat_completion_chunk_to_response_events(chunk_data)
                                
                                # 打印后端 chunk 和转换后的事件
                                if events:
                                    logger.debug(
                                        f"🔄 [{self.request_id}] 流式转换 - 后端chunk: {json.dumps(chunk_data, ensure_ascii=False)}"
                                    )
                                    logger.debug(
                                        f"🔄 [{self.request_id}] 流式转换 - 前端事件: {json.dumps(events, ensure_ascii=False)}"
                                    )
                                
                                for event in events:
                                    sse_data = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                                    self.write(sse_data)
                                    await self.flush()
                                    total_bytes += len(sse_data.encode('utf-8'))
                                    collected_chunks.append(sse_data.encode('utf-8'))
                                    
                            except json.JSONDecodeError:
                                continue
                
                if buffer.strip():
                    if buffer.strip().startswith('data: '):
                        data_content = buffer.strip()[6:]
                        if data_content == '[DONE]':
                            done_event = {"type": "response.completed"}
                            sse_data = f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"
                            self.write(sse_data)
                            await self.flush()
                            total_bytes += len(sse_data.encode('utf-8'))
                            collected_chunks.append(sse_data.encode('utf-8'))
                
                full_response = b''.join(collected_chunks) if self.config.log_response_body else None
                self._log_response(
                    status_code=response.status_code,
                    headers=response_headers,
                    body=full_response,
                    is_streaming=True,
                    stream_bytes=total_bytes
                )
                
        except Exception as e:
            self._log_error("流式处理错误", str(e))
            raise
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

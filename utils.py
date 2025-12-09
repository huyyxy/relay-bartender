import json


def mask_sensitive_value(value: str, visible_chars: int = 4) -> str:
    """对敏感值进行脱敏处理"""
    if not value or len(value) <= visible_chars * 2:
        return "***"
    return f"{value[:visible_chars]}...{value[-visible_chars:]}"


def mask_authorization_header(auth_value: str) -> str:
    """对 Authorization 头进行脱敏"""
    if not auth_value:
        return "***"
    # 处理 Bearer token 格式
    if auth_value.lower().startswith("bearer "):
        token = auth_value[7:]
        return f"Bearer {mask_sensitive_value(token)}"
    return mask_sensitive_value(auth_value)


def truncate_content(content: str, max_length: int = 2000) -> str:
    """截断过长的内容"""
    if not content or len(content) <= max_length:
        return content
    return f"{content[:max_length]}... [truncated, total {len(content)} chars]"


def format_headers_for_log(headers: dict, mask_auth: bool = True) -> dict:
    """格式化请求头用于日志输出，对敏感信息脱敏"""
    result = {}
    sensitive_headers = {'authorization', 'x-api-key', 'api-key'}
    for name, value in headers.items():
        if mask_auth and name.lower() in sensitive_headers:
            if name.lower() == 'authorization':
                result[name] = mask_authorization_header(value)
            else:
                result[name] = mask_sensitive_value(value)
        else:
            result[name] = value
    return result


def format_json_body(body: bytes, max_length: int = 2000) -> str:
    """格式化 JSON body 用于日志输出"""
    if not body:
        return "<empty>"
    try:
        data = json.loads(body)
        formatted = json.dumps(data, ensure_ascii=False, indent=2)
        return truncate_content(formatted, max_length)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 不是标准 JSON，尝试解码为文本（可能是 SSE 流式格式）
        try:
            text = body.decode('utf-8', errors='replace')
            return truncate_content(text, max_length)
        except:
            return f"<binary data, {len(body)} bytes>"


def format_streaming_body(body: bytes, max_length: int = 2000) -> str:
    """格式化流式响应 body（SSE 格式）用于日志输出"""
    if not body:
        return "<empty>"
    try:
        text = body.decode('utf-8', errors='replace')
        # SSE 格式通常是多行 "data: {...}" 格式
        # 尝试提取并格式化每行的 JSON
        lines = text.strip().split('\n')
        formatted_lines = []
        for line in lines:
            line = line.strip()
            if line.startswith('data: '):
                data_content = line[6:]  # 去掉 "data: " 前缀
                if data_content == '[DONE]':
                    formatted_lines.append('data: [DONE]')
                else:
                    try:
                        # 尝试格式化 JSON
                        parsed = json.loads(data_content)
                        formatted_lines.append(f"data: {json.dumps(parsed, ensure_ascii=False)}")
                    except json.JSONDecodeError:
                        formatted_lines.append(line)
            elif line:
                formatted_lines.append(line)
        
        result = '\n'.join(formatted_lines)
        return truncate_content(result, max_length)
    except:
        return f"<binary data, {len(body)} bytes>"

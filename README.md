# Relay Bartender 🍸

一个基于 Tornado 实现的 OpenAI API 兼容中继服务器。支持配置后端地址、API Key 和模型名称覆盖。

## ✨ 功能特性

- 🔄 **完全兼容** - 支持 OpenAI API 的所有端点
- 🌊 **流式响应** - 完美支持 SSE 流式输出
- 🔑 **API Key 管理** - 可配置后端 API Key，隐藏真实密钥
- 🎭 **模型覆盖** - 支持强制覆盖或映射模型名称
- 🌐 **CORS 支持** - 开箱即用的跨域支持
- ⚡ **高性能** - 基于 Tornado 异步框架

## 📦 安装

### 1. 克隆项目

```bash
git clone https://github.com/your-repo/relay-bartender.git
cd relay-bartender
```

### 2. 创建虚拟环境 (推荐)

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# 或
.\venv\Scripts\activate  # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

## ⚙️ 配置

编辑 `config.yaml` 文件：

```yaml
# 服务器配置
server:
  port: 8080
  host: "0.0.0.0"

# 后端 API 配置
backend:
  base_url: "https://api.openai.com"
  api_key: "sk-your-api-key"  # 可选，留空则透传客户端的 Key

# 模型配置
model:
  override: ""  # 强制覆盖所有请求的模型名称
  mapping:      # 模型映射
    gpt-4: "gpt-4-turbo"
    gpt-3.5-turbo: "gpt-4"

# 请求配置
request:
  timeout: 120
  connect_timeout: 30
```

### 配置说明

| 配置项 | 说明 |
|--------|------|
| `server.port` | 服务监听端口 |
| `server.host` | 服务监听地址 |
| `backend.base_url` | 后端 API 基础 URL |
| `backend.api_key` | 后端 API Key（留空则使用客户端传入的） |
| `model.override` | 强制覆盖所有请求的模型名称 |
| `model.mapping` | 模型名称映射表 |
| `request.timeout` | 请求超时时间（秒） |
| `request.connect_timeout` | 连接超时时间（秒） |

## 🚀 启动

### 基本启动

```bash
python server.py
```

### 指定配置文件

```bash
python server.py -c /path/to/config.yaml
```

### 命令行覆盖端口

```bash
python server.py -p 9000
```

### 完整参数

```bash
python server.py --help
```

## 📡 使用

启动后，将你的 OpenAI 客户端的 `base_url` 指向中继服务器即可：

### Python OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",  # 如果配置了后端 API Key，这里可以随意填写
    base_url="http://localhost:8080/v1"
)

response = client.chat.completions.create(
    model="gpt-4",  # 会根据配置进行覆盖或映射
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### cURL

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 健康检查

```bash
curl http://localhost:8080/health
```

## 🎯 典型应用场景

### 1. API Key 隐藏

将真实的 API Key 配置在服务端，客户端使用任意 Key 或空 Key 访问：

```yaml
backend:
  api_key: "sk-real-api-key"
```

### 2. 模型强制覆盖

强制所有请求使用特定模型：

```yaml
model:
  override: "gpt-4-turbo"
```

### 3. 模型映射

将某些模型请求映射到其他模型：

```yaml
model:
  mapping:
    gpt-4: "gpt-4-turbo"
    gpt-3.5-turbo: "gpt-4"
```

### 4. 使用第三方 API 服务

转发请求到第三方 OpenAI 兼容服务：

```yaml
backend:
  base_url: "https://api.third-party.com"
  api_key: "your-third-party-key"
```

## 📋 支持的端点

中继服务器支持所有 OpenAI API 端点，包括但不限于：

- `/v1/chat/completions` - 聊天补全
- `/v1/completions` - 文本补全
- `/v1/embeddings` - 文本嵌入
- `/v1/models` - 模型列表
- `/v1/images/generations` - 图像生成
- `/v1/audio/transcriptions` - 语音转文字
- `/v1/audio/speech` - 文字转语音

## 📄 许可证

MIT License

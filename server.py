#!/usr/bin/env python3
"""
Relay Bartender - OpenAI API 兼容中继服务器
基于 Tornado 实现的 API 中继，支持配置后端地址、API Key 和模型名称覆盖
"""

import logging
import argparse
from config import Config
import httpx
import tornado.ioloop
import tornado.web

from health_handler import HealthHandler
from relay_v3_handler import RelayV4Handler as RelayHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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

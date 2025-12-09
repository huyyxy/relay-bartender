import tornado.web
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HealthHandler(tornado.web.RequestHandler):
    """健康检查处理器"""
    
    def get(self):
        self.write({"status": "ok", "service": "relay-bartender"})

import yaml
import logging

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
        # 确保 model_mapping 始终是字典类型，即使配置文件中是 None
        model_mapping = model_config.get('mapping', {})
        self.model_mapping = model_mapping if isinstance(model_mapping, dict) else {}
        
        # 请求配置
        request_config = config.get('request', {})
        self.request_timeout = request_config.get('timeout', 120)
        self.connect_timeout = request_config.get('connect_timeout', 30)
        
        # 日志配置
        logging_config = config.get('logging', {})
        self.log_enabled = logging_config.get('enabled', True)
        self.log_request_headers = logging_config.get('request_headers', True)
        self.log_request_body = logging_config.get('request_body', True)
        self.log_response_headers = logging_config.get('response_headers', True)
        self.log_response_body = logging_config.get('response_body', True)
        self.log_max_body_length = logging_config.get('max_body_length', 2000)
        self.log_mask_sensitive = logging_config.get('mask_sensitive', True)
        
        logger.info(f"配置已加载: 后端={self.backend_base_url}, 端口={self.port}")
        if self.model_override:
            logger.info(f"模型覆盖: {self.model_override}")
        if self.model_mapping:
            logger.info(f"模型映射: {self.model_mapping}")
        if self.log_enabled:
            logger.info(f"详细日志: 已启用 (请求头={self.log_request_headers}, 请求体={self.log_request_body}, 响应头={self.log_response_headers}, 响应体={self.log_response_body})")

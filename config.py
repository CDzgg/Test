import os

# === 1. Deepseek API Keys ===
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
# TG_CHAT_IDS 可以是逗号分隔的字符串，转换为列表
_tg_chat_ids = os.getenv('TG_CHAT_IDS', '')
TG_CHAT_IDS = [cid.strip() for cid in _tg_chat_ids.split(',') if cid.strip()] if _tg_chat_ids else []

# === 2. 网络设置 ===
# 如果你在 Mac 本地直连没问题，设为 None；如果需要梯子，填入代理地址
PROXIES = None 
# PROXIES = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

# === 3. 新增：老虎证券配置 ===
# 你的 Tiger ID (在开发者中心查看，通常是一串数字)
TIGER_ID = "20157087" 
# 你的账户 ID (在 APP 里能看到，用于交易，查行情其实不是必须的，但填上保险)
TIGER_ACCOUNT = "8847635" 
# 私钥文件的路径 (确保文件在项目目录下)
TIGER_PRIVATE_KEY = os.getenv('TIGER_PRIVATE_KEY', "private_key.pem")
# 运行环境: True=实盘, False=模拟盘 (行情数据通常是一样的)
IS_SANDBOX = False

# === 4. 策略参数 ===
DEFAULT_INTERVAL = "15m"  # K线周期
SCAN_INTERVAL = 300       # 自动轮询间隔 (秒)
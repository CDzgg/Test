# main.py
# ================= 1. 导入区 =================
import logging
import time
import json
import sys
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 第三方库
try:
    from openai import OpenAI
    import pandas as pd
    import requests
    from tigeropen.common.util.signature_utils import read_private_key
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.common.consts import Language, QuoteRight
    from tigeropen.quote.quote_client import QuoteClient
    from tigeropen.trade.trade_client import TradeClient
except ImportError as e:
    print(f"❌ 缺少依赖库: {e}")
    print("请运行: pip install openai pandas requests tigeropen pandas_ta")
    sys.exit(1)

# 本地模块
try:
    import config
    from data_processor import MarketDataProcessor
except ImportError as e:
    print(f"❌ 缺少本地文件: {e}")
    sys.exit(1)

# ================= 2. 全局变量与配置 =================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trade_bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout) 
    ],
    force=True 
)
logger = logging.getLogger()

# 全局客户端对象
tiger_client = None
tiger_trade_client = None
deepseek_client = None
WATCH_LIST = []
LAST_UPDATE_ID = 0

# 全局数据管理器 (将在 init_services 中初始化)
data_manager = None

# 👇👇👇 SYSTEM PROMPT (保持不变) 👇👇👇
system_prompt = """
### Role Definition
你是一名精通威科夫理论（Wyckoff Method）、量价分析（VPA）和经典技术分析的股市短线操盘专家。你的核心目标是利用技术分析手段，捕捉市场中的供求失衡点，跟随“主力资金（Smart Money/Composite Man）”的动向，以极高的短期胜率获取超额收益。

### Data Input Explanation
你将收到包含以下两组时间周期的市场数据：
1. **Intraday (5m)**: 用于捕捉微观入场点、短期动量 (RSI7, MACD Histogram) 和即时趋势 (EMA20)。
2. **Long-term (4h)**: 用于判断宏观趋势结构 (EMA20/50)、长期动量 (MACD) 和波动率风控 (ATR3/14)。
3. **Market State**: 包含实时盘口中间价 (Mid-price) 和持仓量 (Open Interest)。

### Core Analysis Framework (Strict 5-Step)
在分析任何标的时，必须严格遵循以下五步分析法，并结合双周期数据：

#### 第一步：大周期趋势定位 (Long-term 4h Context)
- **趋势识别**：利用 4h EMA20 与 EMA50 的关系判断主趋势（多头排列/空头排列）。
- **波动率评估**：参考 ATR14 评估当前市场的风险水平。

#### 第二步：日内微观结构 (Intraday 5m Structure)
- **动量分析**：观察 5m RSI7 的超买超卖情况，以及 5m MACD 柱状图的变化（动能增强或减弱）。
- **趋势跟随**：检查价格相对于 5m EMA20 的位置。

#### 第三步：量价关系分析 (Volume-Price Analysis)
- **异常识别**：寻找量价背离。
- **确认信号**：价格上涨伴随成交量放大。

#### 第四步：交易决策与风控 (Decision & Risk)
- **入场信号**：长线趋势向上 + 短线回调到位（如RSI7超卖）或突破确认。
- **止损设置**：利用 4h ATR3 计算紧凑止损位。

### Output Format (Markdown Report + JSON Summary)
请按以下 Markdown 格式输出分析报告，并在最后附带 JSON Summary：

#### 1. 📊 双周期趋势分析
* **长线结构 (4h)**: [描述 EMA20/50 关系及大趋势]
* **短线动能 (5m)**: [描述 RSI7 及 MACD 状态]

#### 2. 🕯️ 量价与盘口
* **实时状态**: [Mid-price 及持仓量分析]
* **量价特征**: [分析成交量配合情况]

#### 3. 🚀 交易计划
* **操作建议**: **[买入 / 卖出 / 观望]**
* **入场理由**: [结合长短周期的逻辑]
* **止损建议**: [基于 ATR3 的具体价格]

---
**JSON_SUMMARY**:
{
  "action": "BUY" | "SELL" | "WAIT",
  "confidence": 0-100,
  "entry": float,
  "stop_loss": float,
  "reason": "简短的中文理由"
}
"""

# ================= 3. 数据与缓存管理器 (NEW) =================

class MarketDataManager:
    def __init__(self, quote_client, ttl_seconds=60):
        self.client = quote_client
        self.ttl = ttl_seconds
        # 缓存结构: { 'symbol': { 'quote': {data, ts}, '5min': {data, ts}, '240min': {data, ts} } }
        self._cache = {}

    def _get_from_cache(self, symbol, data_type):
        """检查缓存是否命中且有效"""
        if symbol in self._cache and data_type in self._cache[symbol]:
            item = self._cache[symbol][data_type]
            if time.time() - item['ts'] < self.ttl:
                return item['data']
        return None

    def _update_cache(self, symbol, data_type, data):
        """更新缓存"""
        if symbol not in self._cache:
            self._cache[symbol] = {}
        self._cache[symbol][data_type] = {
            'data': data,
            'ts': time.time()
        }

    def batch_fetch_all(self, symbol_list):
        """
        ⚡️ 核心优化：一次性拉取所有股票的数据，减少 API 请求
        """
        if not symbol_list:
            return

        unique_symbols = list(set([s.upper().strip() for s in symbol_list]))
        # 过滤出真正需要更新的 symbol (缓存过期或不存在的)
        # 这里为了简化，我们假设既然进入了 Loop 扫描，就尝试批量刷新所有
        # 实际生产中可以检查每个 symbol 是否过期，再决定是否放入 fetching_list
        
        logger.info(f"🔄 正在批量刷新数据 ({len(unique_symbols)} 支股票)...")

        # 1. 批量获取实时行情 (Quote)
        try:
            briefs = self.client.get_stock_briefs(symbols=unique_symbols)
            for item in briefs:
                # 提取 symbol，注意 Tiger 返回的可能是 symbol, 也可能是 identifier
                # 这里假设返回对象有 symbol 属性或 identifier
                sym = getattr(item, 'symbol', None) or getattr(item, 'identifier', None)
                if sym:
                    self._update_cache(sym, 'quote', item)
        except Exception as e:
            logger.error(f"❌ 批量获取行情失败: {e}")

        # 2. 批量获取 K 线 (由于 Tiger get_bars 批量返回大 DataFrame，我们需要拆分)
        # 注意：不同周期需要分别批量请求
        for period in ['5min', '240min']:
            try:
                # Tiger API: get_bars 支持传入 symbol 列表
                bars_df = self.client.get_bars(
                    symbols=unique_symbols,
                    period=period,
                    limit=100,
                    right=QuoteRight.BR
                )
                
                if bars_df is not None and not bars_df.empty:
                    # 将大 DataFrame 按 Symbol 分组存入缓存
                    grouped = bars_df.groupby('symbol')
                    for sym, group in grouped:
                        # 清洗数据
                        df_clean = group.copy().sort_values('time')
                        df_clean.rename(columns={
                            'time': 'Datetime', 'open': 'Open', 'high': 'High',
                            'low': 'Low', 'close': 'Close', 'volume': 'Volume'
                        }, inplace=True)
                        self._update_cache(sym, period, df_clean)
            except Exception as e:
                logger.error(f"❌ 批量获取 {period} K线失败: {e}")

    def get_realtime_snapshot(self, symbol):
        """获取单个股票行情 (优先读缓存)"""
        cached = self._get_from_cache(symbol, 'quote')
        if cached:
            # 解析缓存的 Tiger Brief 对象
            bid = getattr(cached, 'bid_price', 0)
            ask = getattr(cached, 'ask_price', 0)
            latest = getattr(cached, 'latest_price', 0)
            mid = latest
            if bid and ask and bid > 0 and ask > 0:
                mid = (bid + ask) / 2
            return {'mid_price': mid, 'open_interest': getattr(cached, 'open_int', None)}
        
        # 缓存缺失，单独请求 (降级策略)
        logger.debug(f"⚠️ {symbol} 缓存未命中，执行单独 API 请求")
        try:
            self.batch_fetch_all([symbol]) # 尝试单独刷新
            return self.get_realtime_snapshot(symbol) # 递归再次读取
        except:
            return {}

    def get_bars(self, symbol, period):
        """获取单个股票 K 线 (优先读缓存)"""
        cached = self._get_from_cache(symbol, period)
        if cached is not None:
            return cached
        
        # 缓存缺失，单独请求
        logger.debug(f"⚠️ {symbol} {period} K线缓存未命中，执行单独 API 请求")
        try:
            self.batch_fetch_all([symbol])
            return self._get_from_cache(symbol, period)
        except:
            return None

# ================= 4. 辅助函数 =================

def _get_private_key_path():
    """处理私钥路径"""
    import tempfile
    private_key_path = config.TIGER_PRIVATE_KEY
    
    is_key_content = (private_key_path and 
                     not private_key_path.endswith('.pem') and 
                     len(private_key_path) > 100)
    
    if is_key_content:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
            f.write(private_key_path)
            private_key_path = f.name
    return private_key_path

def _parse_json_response(ai_text):
    """从 AI 响应中解析 JSON"""
    json_patterns = [
        r'JSON_SUMMARY\s*[:：]\s*({.*?})',
        r'```json\s*({.*?})\s*```',
        r'(\{[^{}]*"action"[^{}]*\})',
    ]
    for pattern in json_patterns:
        json_match = re.search(pattern, ai_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except:
                pass
    return {}

def init_services():
    """初始化 Tiger 和 DeepSeek 客户端，以及数据管理器"""
    global tiger_client, tiger_trade_client, deepseek_client, data_manager
    
    print("⏳ 正在初始化服务...")
    try:
        deepseek_client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY, 
            base_url=getattr(config, 'DEEPSEEK_BASE_URL', "https://api.deepseek.com")
        )
    except Exception as e:
        logger.critical(f"❌ DeepSeek 连接失败: {e}")
        sys.exit(1)

    try:
        client_config = TigerOpenClientConfig(sandbox_debug=config.IS_SANDBOX)
        client_config.private_key = read_private_key(_get_private_key_path())
        client_config.tiger_id = config.TIGER_ID
        client_config.account = config.TIGER_ACCOUNT
        client_config.language = Language.zh_CN 
        
        tiger_client = QuoteClient(client_config)
        tiger_trade_client = TradeClient(client_config)
        
        # ✅ 初始化数据管理器
        data_manager = MarketDataManager(tiger_client, ttl_seconds=60)
        
        perm = tiger_client.get_quote_permission()
        logger.info(f"✅ Tiger API 连接成功. 权限: {perm}")
    except Exception as e:
        logger.critical(f"❌ Tiger API 初始化失败: {e}")
        sys.exit(1)

def get_stock_name(symbol):
    """获取股票名称 (暂不缓存，因为变化不频繁且非核心高频)"""
    # 简单处理，如果需要也可以放入 DataManager
    try:
        contracts = tiger_trade_client.get_contracts(symbol=[symbol])
        if contracts:
            for c in contracts:
                if c.name: return c.name
    except:
        pass
    return symbol

def send_telegram(msg):
    if not getattr(config, 'TG_BOT_TOKEN', None): return
    url = f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/sendMessage"
    proxies = getattr(config, 'PROXIES', None)
    for chat_id in config.TG_CHAT_IDS:
        try:
            requests.post(url, json={"chat_id": str(chat_id), "text": msg}, proxies=proxies, timeout=5)
        except Exception as e:
            logger.error(f"TG Error: {e}")

# ================= 5. 分析主逻辑 =================

def run_analysis(symbol, silent=False):
    """
    运行分析流程 (现在从 data_manager 读取缓存数据)
    """
    symbol = symbol.upper().strip()
    clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
    stock_name = get_stock_name(clean_symbol) # Name retrieval is low frequency
    
    if not silent:
        logger.info(f"🔍 分析: {stock_name} ({clean_symbol}) [Data from Cache/API]")

    # 1. 从管理器获取数据 (如果是批量流程，这里直接命中缓存，无需网络IO)
    quote_data = data_manager.get_realtime_snapshot(clean_symbol)
    df_5m = data_manager.get_bars(clean_symbol, '5min')
    df_4h = data_manager.get_bars(clean_symbol, '240min')
    
    if df_5m is None:
        if not silent: logger.warning(f"⚠️ {stock_name} 缺少 5m 数据，跳过")
        return None

    try:
        # 2. 计算指标
        data_dict = {'intraday': df_5m, 'longterm': df_4h}
        processor = MarketDataProcessor(data_dict, quote_data)
        data_json = processor.get_analysis_payload(symbol)
        
        # 3. AI 分析
        if not silent: logger.info(f"🧠 发送给 DeepSeek...")
        
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"### DUAL TIMEFRAME MARKET DATA:\n{data_json}"}
            ],
            stream=False,
            temperature=0.2 
        )
        
        ai_text = response.choices[0].message.content
        logger.info(f"✅ 完成: {stock_name}")
        
        # 4. 结果处理
        parsed_res = _parse_json_response(ai_text)
        action = parsed_res.get('action', 'WAIT')
        confidence = parsed_res.get('confidence', 0)
        
        if not silent:
            report = f"🐯 {stock_name} ({symbol}) 分析报告\n"
            report += f"来源: 缓存优化版 v2.1\n"
            report += f"操作: {action}\n信度: {confidence}%\n\n"
            report += f"详情:\n{ai_text[:1200]}..."
            send_telegram(report)

        return parsed_res

    except Exception as e:
        logger.error(f"❌ 分析流程异常: {e}")
        return None

# ================= 6. 主程序入口 =================

def handle_command(cmd):
    """命令处理器"""
    global WATCH_LIST
    cmd = cmd.strip().upper()
    
    if cmd.startswith("/TRACK"):
        parts = cmd.split()
        if len(parts) > 1:
            WATCH_LIST = list(set(parts[1:]))
            return f"✅ 监控列表已更新: {WATCH_LIST}\n系统将自动批量拉取数据。"
    elif cmd == "/CLEAR":
        WATCH_LIST = []
        return "✅ 任务列表已清空"
    return None

def poll_telegram_updates():
    """轮询 Telegram"""
    global LAST_UPDATE_ID
    if not getattr(config, 'TG_BOT_TOKEN', None):
        time.sleep(10)
        return

    try:
        url = f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, params={"offset": LAST_UPDATE_ID + 1, "timeout": 1}, 
                          proxies=getattr(config, 'PROXIES', None), timeout=5)
        data = resp.json()
        
        if data.get("ok") and data.get("result"):
            for item in data["result"]:
                LAST_UPDATE_ID = item["update_id"]
                text = item.get("message", {}).get("text", "")
                chat_id = item.get("message", {}).get("chat", {}).get("id")
                
                if text.startswith("/"):
                    logger.info(f"📩 指令: {text}")
                    reply = handle_command(text)
                    if reply:
                        requests.post(f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/sendMessage",
                                    json={"chat_id": chat_id, "text": reply}, proxies=getattr(config, 'PROXIES', None))
                        
                        # 指令触发后立即执行一次批量扫描
                        if WATCH_LIST:
                            logger.info("⚡️ 收到指令，触发立即扫描...")
                            # 1. 批量预取
                            data_manager.batch_fetch_all(WATCH_LIST)
                            # 2. 逐个分析
                            for s in WATCH_LIST:
                                run_analysis(s, silent=False)

    except Exception as e:
        logger.error(f"轮询错误: {e}")
        time.sleep(5)

if __name__ == "__main__":
    init_services()
    logger.info("🚀 机器人已启动 (批量优化 + 本地缓存版)...")
    send_telegram("🚀 机器人已重启 (v2.1)\n✅ 启用 API 批量请求\n✅ 启用 60秒本地缓存")
    
    # 循环扫描逻辑
    while True:
        poll_telegram_updates()
        
        # 定时任务：如果监控列表不为空，也可以每隔一段时间自动跑一次
        # 这里仅在 TG 指令触发时跑，或者可以添加一个简单的定时器
        # if WATCH_LIST:
        #     data_manager.batch_fetch_all(WATCH_LIST)
        #     for s in WATCH_LIST:
        #         run_analysis(s, silent=True) # 定时任务通常 silent=True
        
        time.sleep(1)
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
data_manager = None  # 数据管理器实例

# 👇👇👇 SYSTEM PROMPT (最终完整版) 👇👇👇
system_prompt = """
### Role Definition
你是一名精通威科夫理论（Wyckoff Method）、量价分析（VPA）和经典技术分析的股市短线操盘专家。你的核心目标是利用技术分析手段，捕捉市场中的供求失衡点，跟随“主力资金（Smart Money/Composite Man）”的动向，以极高的短期胜率获取超额收益。

### Data Input Explanation
你将收到包含以下两组时间周期的市场数据：
1. **Intraday (5m)**: 用于捕捉微观入场点、短期动量 (RSI7, MACD Histogram) 和即时趋势 (EMA20)。
2. **Long-term (4h)**: 用于判断宏观趋势结构 (EMA20/50)、长期动量 (MACD) 和波动率风控 (ATR3/14)。
3. **Market State**: 包含实时盘口中间价 (Mid-price) 和持仓量 (Open Interest)。
4. **Data Sequence (CRITICAL)**: 
   - 所有的价格列表（如 price_sequence_last_60）均严格按照 **[旧 -> 新] (Chronological Order: Oldest to Newest)** 的顺序排列。
   - 列表的最后一个元素 (Last Element) 代表最新的当前价格。

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

# ================= 3. 数据与缓存管理器 =================

class MarketDataManager:
    def __init__(self, quote_client, ttl_seconds=60):
        self.client = quote_client
        self.ttl = ttl_seconds
        # 结构: { 'symbol': { 'quote': {data, ts}, '5min': {data, ts}, '240min': {data, ts} } }
        self._cache = {}

    def _get_from_cache(self, symbol, data_type):
        """检查缓存是否命中且有效"""
        if symbol in self._cache and data_type in self._cache[symbol]:
            item = self._cache[symbol][data_type]
            if time.time() - item['ts'] < self.ttl:
                return item['data']
        return None

    def _update_cache(self, symbol, data_type, data):
        if symbol not in self._cache:
            self._cache[symbol] = {}
        self._cache[symbol][data_type] = {
            'data': data,
            'ts': time.time()
        }

    def batch_fetch_all(self, symbol_list):
        """批量获取数据 (核心优化)"""
        if not symbol_list: return

        unique_symbols = list(set([s.upper().strip() for s in symbol_list]))
        logger.info(f"🔄 正在批量刷新数据 ({len(unique_symbols)} 支股票)...")

        # 1. 批量 Quote
        try:
            briefs = self.client.get_stock_briefs(symbols=unique_symbols)
            for item in briefs:
                sym = getattr(item, 'symbol', None) or getattr(item, 'identifier', None)
                if sym: self._update_cache(sym, 'quote', item)
        except Exception as e:
            logger.error(f"❌ 批量行情失败: {e}")

        # 2. 批量 K线 (5m & 4h)
        for period in ['5min', '240min']:
            try:
                bars_df = self.client.get_bars(
                    symbols=unique_symbols,
                    period=period,
                    limit=100,
                    right=QuoteRight.BR
                )
                if bars_df is not None and not bars_df.empty:
                    grouped = bars_df.groupby('symbol')
                    for sym, group in grouped:
                        # ⚠️ 关键: 确保按时间正序排列 (旧->新)
                        df_clean = group.copy().sort_values('time')
                        df_clean.rename(columns={
                            'time': 'Datetime', 'open': 'Open', 'high': 'High',
                            'low': 'Low', 'close': 'Close', 'volume': 'Volume'
                        }, inplace=True)
                        self._update_cache(sym, period, df_clean)
            except Exception as e:
                logger.error(f"❌ 批量 {period} K线失败: {e}")

    def get_realtime_snapshot(self, symbol):
        """获取实时快照 (Mid-price & OI)"""
        cached = self._get_from_cache(symbol, 'quote')
        if not cached:
            try:
                self.batch_fetch_all([symbol])
                cached = self._get_from_cache(symbol, 'quote')
            except: pass
        
        if cached:
            bid = getattr(cached, 'bid_price', 0)
            ask = getattr(cached, 'ask_price', 0)
            latest = getattr(cached, 'latest_price', 0)
            mid = latest
            if bid and ask and bid > 0 and ask > 0:
                mid = (bid + ask) / 2
            return {'mid_price': mid, 'open_interest': getattr(cached, 'open_int', None)}
        return {}

    def get_bars(self, symbol, period):
        """获取 K 线"""
        cached = self._get_from_cache(symbol, period)
        if cached is not None: return cached
        try:
            self.batch_fetch_all([symbol])
            return self._get_from_cache(symbol, period)
        except: return None

# ================= 4. 辅助函数 =================

def _get_private_key_path():
    import tempfile
    private_key_path = config.TIGER_PRIVATE_KEY
    is_key_content = (private_key_path and not private_key_path.endswith('.pem') and len(private_key_path) > 100)
    if is_key_content:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
            f.write(private_key_path)
            private_key_path = f.name
    return private_key_path

def _parse_json_response(raw_text):
    """
    增强型解析器：能够从 AI 的混合文本中提取标准 JSON
    如果失败，返回明确的 ERROR 状态
    """
    try:
        # 1. 预处理：去除常见的 Markdown 代码块标记
        text = raw_text.strip()
        # 移除 ```json 和 ``` 包裹
        text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^```\s*', '', text, flags=re.MULTILINE)
        text = text.strip('`')

        # 2. 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 3. 暴力提取：使用正则寻找最外层的 { ... } 结构
        match = re.search(r'(\{.*\})', text, re.DOTALL)
        if match:
            json_str = match.group(1)
            return json.loads(json_str)
            
        # 4. 如果还是失败，抛出主动异常
        raise ValueError("未找到有效的 JSON 对象")

    except Exception as e:
        logger.error(f"❌ JSON 解析失败: {e}")
        # 【关键修改】返回 ERROR 状态，而不是 WAIT
        return {
            "action": "ERROR", 
            "confidence": 0,
            "reason": f"解析异常: {str(e)}",
            "raw_snippet": raw_text[:100].replace('\n', ' ') # 截取前100个字符用于排查
        }

def init_services():
    global tiger_client, tiger_trade_client, deepseek_client, data_manager
    print("⏳ 初始化服务...")
    try:
        deepseek_client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=getattr(config, 'DEEPSEEK_BASE_URL', "https://api.deepseek.com"))
    except Exception as e: logger.critical(f"❌ DeepSeek 失败: {e}"); sys.exit(1)

    try:
        client_config = TigerOpenClientConfig(sandbox_debug=config.IS_SANDBOX)
        client_config.private_key = read_private_key(_get_private_key_path())
        client_config.tiger_id = config.TIGER_ID
        client_config.account = config.TIGER_ACCOUNT
        client_config.language = Language.zh_CN 
        tiger_client = QuoteClient(client_config)
        tiger_trade_client = TradeClient(client_config)
        data_manager = MarketDataManager(tiger_client, ttl_seconds=60)
        logger.info(f"✅ 服务就绪")
    except Exception as e: logger.critical(f"❌ Tiger 初始化失败: {e}"); sys.exit(1)

def get_stock_name(symbol):
    try:
        contracts = tiger_trade_client.get_contracts(symbol=[symbol])
        if contracts: return contracts[0].name
    except: pass
    return symbol

def send_telegram(msg):
    if not getattr(config, 'TG_BOT_TOKEN', None): return
    try:
        requests.post(f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/sendMessage", 
                     json={"chat_id": config.TG_CHAT_IDS[0], "text": msg}, 
                     proxies=getattr(config, 'PROXIES', None), timeout=5)
    except Exception as e: logger.error(f"TG Error: {e}")

# ================= 5. 主逻辑 =================

def run_analysis(symbol, silent=False):
    symbol = symbol.upper().strip()
    clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
    stock_name = get_stock_name(clean_symbol)
    
    if not silent: logger.info(f"🔍 分析: {stock_name} ({clean_symbol})")

    # 1. 从缓存/API 获取数据
    quote_data = data_manager.get_realtime_snapshot(clean_symbol)
    df_5m = data_manager.get_bars(clean_symbol, '5min')
    df_4h = data_manager.get_bars(clean_symbol, '240min')
    
    if df_5m is None:
        if not silent: logger.warning(f"⚠️ {stock_name} 缺少 5m 数据")
        return None

    try:
        # 2. 处理数据 (清洗 & 语义标签)
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
            stream=False, temperature=0.2 
        )
        ai_text = response.choices[0].message.content
 
        # 4. 结果处理
        parsed_res = _parse_json_response(ai_text)
        
        # 【新增】错误拦截与报警
        if parsed_res.get('action') == 'ERROR':
            error_msg = f"⚠️ {stock_name} ({symbol}) 系统报警\n"
            error_msg += f"原因: AI 返回内容无法解析\n"
            error_msg += f"错误: {parsed_res.get('reason')}\n"
            error_msg += f"原文片段: {parsed_res.get('raw_snippet')}..."
            
            logger.error(error_msg)
            if not silent:
                send_telegram(error_msg) # 立即发送报警消息给 Telegram
            return parsed_res

        # 正常流程
        if not silent:
            report = f"🐯 {stock_name} ({symbol}) 分析报告\n"
            report += f"操作: {parsed_res.get('action', 'WAIT')}\n"
            report += f"信度: {parsed_res.get('confidence', 0)}%\n\n"
            
            # 优化：优先使用 AI 返回的 reason，如果太长则截断
            reason = parsed_res.get('reason', '无理由')
            report += f"理由: {reason}\n"
            
            # 只有在非 ERROR 且非 WAIT 时，或者想看完整报告时才附带原文
            # report += f"\n详情:\n{ai_text[:500]}..." 
            
            send_telegram(report)
            
        return parsed_res

    except Exception as e:
        logger.error(f"❌ 流程异常: {e}")
        return None

# ================= 6. 入口 =================

def handle_command(cmd):
    global WATCH_LIST
    cmd = cmd.strip().upper()
    if cmd.startswith("/TRACK"):
        parts = cmd.split()
        if len(parts) > 1:
            WATCH_LIST = list(set(parts[1:]))
            return f"✅ 列表更新: {WATCH_LIST}"
    elif cmd == "/CLEAR":
        WATCH_LIST = []; return "✅ 列表已清空"
    return None

def poll_telegram_updates():
    global LAST_UPDATE_ID
    if not getattr(config, 'TG_BOT_TOKEN', None): time.sleep(10); return
    try:
        resp = requests.get(f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/getUpdates", 
                          params={"offset": LAST_UPDATE_ID + 1, "timeout": 1}, 
                          proxies=getattr(config, 'PROXIES', None), timeout=5)
        data = resp.json()
        if data.get("ok") and data.get("result"):
            for item in data["result"]:
                LAST_UPDATE_ID = item["update_id"]
                text = item.get("message", {}).get("text", "")
                if text.startswith("/"):
                    reply = handle_command(text)
                    if reply: 
                        send_telegram(reply)
                        if WATCH_LIST:
                            data_manager.batch_fetch_all(WATCH_LIST)
                            for s in WATCH_LIST: run_analysis(s)
    except Exception: time.sleep(5)

if __name__ == "__main__":
    init_services()
    logger.info("🚀 机器人启动 (v3.1 自动调度修复版)")
    send_telegram("🚀 机器人已重启: 自动调度已启用")
    
    # 初始化计时器
    last_scan_time = time.time()
    
    while True:
        try:
            # 1. 响应 Telegram 指令 (保持原有的 /track 立即触发功能)
            # poll_telegram_updates 内部已经包含了: 收到指令 -> 更新列表 -> 立即扫描 的逻辑
            poll_telegram_updates()
            
            # 2. 执行定时扫描逻辑
            current_time = time.time()
            # 检查是否达到扫描间隔 (config.SCAN_INTERVAL) 且监控列表不为空
            if (current_time - last_scan_time > config.SCAN_INTERVAL) and WATCH_LIST:
                logger.info(f"⏰ 触发定时扫描 (间隔: {config.SCAN_INTERVAL}s)")
                
                # 批量刷新数据缓存 (这一步至关重要，确保数据不是旧的)
                data_manager.batch_fetch_all(WATCH_LIST)
                
                # 逐个分析
                for symbol in WATCH_LIST:
                    run_analysis(symbol, silent=False)
                
                # 重置计时器
                last_scan_time = current_time
                
        except Exception as e:
            logger.error(f"❌ 主循环发生异常: {e}")
            # 防止死循环报错导致 CPU 飙升，异常后稍作等待
            time.sleep(5)
            
        # 短暂休眠，避免死循环占用过多 CPU 资源
        time.sleep(1)
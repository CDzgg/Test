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

# ================= 2. 辅助类型定义 =================

class ActionType:
    """订单操作类型"""
    BUY = "BUY"
    SELL = "SELL"

class OrderType:
    """订单类型"""
    MKT = "MKT"  # 市价单
    LMT = "LMT"  # 限价单

class Order:
    """订单对象"""
    def __init__(self, account, contract, action, order_type, quantity):
        self.account = account
        self.contract = contract
        self.action = action
        self.order_type = order_type
        self.quantity = quantity

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
- **资金管理 (Money Management)**: 
   - 你将收到当前的账户资金 (Cash) 和持仓 (Position)。
   - **加仓逻辑**: 如果已有持仓且趋势确认加强 (Confirmation)，可以继续买入，但单支股票持仓不能超过50%，账户总持仓股票数量不超过10个。
   - **金额决定**: 请根据你的【置信度 (Confidence)】和【账户余额】决定本次交易的金额。
   - 建议：高信度 (>80%) 可投入较大仓位，低信度轻仓试错。切勿建议超过可用现金的金额。

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
  "target_cash": float,  // 【新增】本次计划投入的现金金额 (单位: 账户本位币，如 HKD)。如果是 SELL，填 0 表示全卖，或填具体金额减仓。
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

def get_account_status():
    """
    获取账户资金状态
    返回: (可用现金, 货币代码)
    说明: 如果无法获取，返回 (-1, "UNKNOWN") 作为特殊标记
    """
    try:
        if tiger_trade_client is None:
            logger.warning("⚠️ Trade Client 未初始化")
            return (-1, "UNKNOWN")
        
        # 尝试使用 get_asset 或类似方法获取资产信息
        # Tiger API 通常通过账户查询获取资金信息
        try:
            # 方法1: 尝试 get_asset (常见的资产查询方法)
            asset = tiger_trade_client.get_asset()
            if asset:
                cash_available = getattr(asset, 'cash', 0)
                currency = getattr(asset, 'currency', 'HKD')
                logger.info(f"💰 账户资金: {cash_available} {currency}")
                return (float(cash_available), currency)
        except AttributeError:
            pass
        
        try:
            # 方法2: 尝试 get_position 中提取现金信息
            positions = tiger_trade_client.get_positions()
            if positions:
                # 某些 API 版本在 positions 中包含现金信息
                for pos in positions:
                    if getattr(pos, 'symbol', '') == 'CASH':
                        cash = getattr(pos, 'quantity', 0)
                        logger.info(f"💰 账户资金: {cash} HKD")
                        return (float(cash), "HKD")
        except Exception:
            pass
        
        # 如果以上都失败，返回特殊标记 (-1, "UNKNOWN")
        logger.warning("⚠️ 无法获取账户资金信息 (API 权限或版本问题)")
        return (-1, "UNKNOWN")
        
    except Exception as e:
        logger.error(f"❌ 获取账户失败: {e}")
        return (-1, "UNKNOWN")

def get_position(symbol):
    """
    获取某支股票的持仓数量
    返回: 持仓股数 (int)，如果无持仓返回 0
    """
    try:
        if tiger_trade_client is None:
            return 0
        
        symbol = symbol.upper().strip()
        clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
        
        # 获取所有持仓
        try:
            positions = tiger_trade_client.get_positions()
        except Exception as e:
            logger.error(f"❌ 查询持仓异常: {e}")
            return 0
        
        if not positions:
            return 0
        
        # 查找该股票的持仓
        for pos in positions:
            pos_symbol = getattr(pos, 'symbol', '')
            
            # 比对逻辑：处理多种格式 (00700, 00700.HK, TCEHY 等)
            pos_clean = pos_symbol.upper().split('.')[0] if pos_symbol else ''
            
            if pos_clean == clean_symbol or pos_symbol.upper() == symbol:
                qty = getattr(pos, 'quantity', 0)
                if qty > 0:  # 只记录正持仓
                    logger.debug(f"📊 {symbol} 持仓: {qty}股")
                    return int(qty)
        
        return 0
        
    except Exception as e:
        logger.error(f"❌ 获取持仓失败 ({symbol}): {e}")
        return 0

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

    # ================= 【新增】股票信息打印 =================
    if not silent:
        logger.info(f"📊 股票基本信息:")
        logger.info(f"   名称: {stock_name}")
        logger.info(f"   代码: {clean_symbol}")
        logger.info(f"   实时价格: {quote_data.get('mid_price', 'N/A')}")
        logger.info(f"   持仓量: {quote_data.get('open_interest', 'N/A')}")
        
        if df_5m is not None and not df_5m.empty:
            logger.info(f"   5m K线: {len(df_5m)} 根 (最新收盘: {df_5m.iloc[-1]['Close']:.4f})")
        if df_4h is not None and not df_4h.empty:
            logger.info(f"   4h K线: {len(df_4h)} 根 (最新收盘: {df_4h.iloc[-1]['Close']:.4f})")
    # =======================================================

    try:
        # 2. 处理数据 (清洗 & 语义标签)
        data_dict = {'intraday': df_5m, 'longterm': df_4h}
        processor = MarketDataProcessor(data_dict, quote_data)
        data_json = processor.get_analysis_payload(symbol)
        
        # ================= 【新增】指标信息打印 =================
        if not silent:
            logger.info(f"📈 技术指标已计算:")
            indicators = json.loads(data_json).get('indicators', {})
            ind_5m = indicators.get('intraday_5m', {})
            ind_4h = indicators.get('longterm_4h', {})
            
            if isinstance(ind_5m, dict):
                logger.info(f"   5m: RSI7={ind_5m.get('rsi7')}, MACD_H={ind_5m.get('macd_hist')}, EMA20={ind_5m.get('ema20')}")
            if isinstance(ind_4h, dict):
                logger.info(f"   4h: 趋势={ind_4h.get('trend_tag')}, EMA20={ind_4h.get('ema20')}, EMA50={ind_4h.get('ema50')}, ATR14={ind_4h.get('atr14')}")
        # =======================================================
        
        # ================= 插入账户上下文 (改进) =================
        curr_cash, curr_currency = get_account_status()
        curr_pos = get_position(clean_symbol)
        
        # ================= 【新增】账户信息打印（改进版本） =================
        if not silent:
            logger.info(f"💼 账户状态:")
            if curr_cash == -1:
                logger.info(f"   可用资金: 无法获取 (API 权限问题)")
            else:
                logger.info(f"   可用资金: {curr_cash} {curr_currency}")
            logger.info(f"   当前持仓: {curr_pos} 股")
        # =======================================================
        
        # 如果无法获取账户信息，给出友好提示
        if curr_cash == -1:
            account_context = f"""
### 当前账户状态 (Fund Management Context):
- 可用资金: 无法获取 (API 权限或版本问题)
- 当前持仓 ({symbol}): {curr_pos} 股
- 说明：由于无法获取账户余额，建议在高信度时谨慎入场。
"""
        else:
            account_context = f"""
### 当前账户状态 (Fund Management Context):
- 可用资金: {curr_cash} {curr_currency}
- 当前持仓 ({symbol}): {curr_pos} 股
- 说明：请根据当前流动性确定"target_cash"。请勿超过可用现金。
"""
        # =======================================================

        # 3. AI 分析
        if not silent: logger.info(f"🧠 发送给 DeepSeek (含资金信息)...")
        final_user_content = f"### DUAL TIMEFRAME MARKET DATA:\n{data_json}\n{account_context}"
        
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": final_user_content}
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
                send_telegram(error_msg)
            return parsed_res

        # ================= 交易执行 =================
        trade_feedback = ""
        action = parsed_res.get('action', 'WAIT')
        confidence = parsed_res.get('confidence', 0)
        target_cash = parsed_res.get('target_cash', 0.0)
        
        # 只有在信号明确且置信度高时才交易
        if action in ["BUY", "SELL"] and confidence >= 70:
            logger.info(f"⚡ 触发交易: {action} (AI建议金额: {target_cash})")
            trade_feedback = execute_order(clean_symbol, action, confidence, target_cash)
        # =======================================================

        # --- C. 发送报告 ---
        if not silent:
            report = f"🐯 {stock_name} ({symbol}) 分析报告\n"
            report += f"决策: {action} (信度: {confidence}%)\n"
            report += f"建议金额: {target_cash}\n"
            report += f"理由: {parsed_res.get('reason', 'N/A')}\n"
            
            if trade_feedback:
                report += f"----------------\n⚙️ 执行: {trade_feedback}\n"
            
            send_telegram(report)
            
        return parsed_res

    except Exception as e:
        logger.error(f"❌ 流程异常: {e}")
        return None

# ================= execute_order 下单函数 ================= 

def execute_order(symbol, action_str, confidence, target_cash):
    """
    执行下单 - DeepSeek 托管模式
    target_cash: AI 建议的交易金额 (由 JSON 返回)
    """
    if not getattr(config, 'ENABLE_TRADING', False):
        logger.info(f"ℹ️ 模拟交易模式: {action_str} {target_cash} (开关关闭)")
        return f"模拟交易: 开关关闭 (AI建议: {action_str} {target_cash})"

    try:
        # 1. 基础信息
        symbol = symbol.upper().strip()
        curr_pos = get_position(symbol)
        
        # 获取实时价格
        quote = data_manager.get_realtime_snapshot(symbol)
        price = quote.get('mid_price', 0)
        if price <= 0:
            logger.warning(f"⚠️ {symbol} 价格获取失败")
            return "❌ 价格获取失败，取消"

        quantity = 0
        
        # ================= BUY 逻辑 =================
        if action_str == "BUY":
            avail_cash, _ = get_account_status()
            
            # 使用 AI 建议的金额，但不能超过实际可用资金
            # 如果无法获取账户信息，使用保守策略
            if avail_cash == -1:
                logger.warning(f"⚠️ 无法获取账户余额，采用保守策略")
                avail_cash = 0
            
            safe_cash = min(float(target_cash), float(avail_cash))
            
            if safe_cash < price:
                msg = f"❌ 资金不足或AI建议金额过小 (建议: {target_cash}, 股价: {price}, 可用: {avail_cash})"
                logger.warning(msg)
                return msg

            quantity = int(safe_cash / price)
            if quantity == 0:
                logger.warning(f"❌ {symbol} 计算股数为 0")
                return "❌ 计算股数为 0"

        # ================= SELL 逻辑 =================
        elif action_str == "SELL":
            if curr_pos <= 0:
                logger.warning(f"⚠️ {symbol} 无持仓，无法卖出")
                return "⚠️ 无持仓，无法卖出"
            
            if target_cash <= 0 or target_cash >= (curr_pos * price):
                quantity = curr_pos
                note = "清仓"
            else:
                quantity = int(target_cash / price)
                if quantity > curr_pos: quantity = curr_pos
                note = f"减仓 (保留 {curr_pos - quantity}股)"

        else:
            return "❌ 未知的操作类型"

        # ================= 下单执行 (演示模式) =================
        logger.info(f"📋 下单准备: {action_str} {quantity}股 @ {price}")
        
        try:
            contract = tiger_trade_client.get_contracts(symbol=[symbol])[0]
            action = ActionType.BUY if action_str == "BUY" else ActionType.SELL
            
            order = Order(
                account=config.TIGER_ACCOUNT,
                contract=contract,
                action=action,
                order_type=OrderType.MKT,
                quantity=quantity
            )
            
            oid = tiger_trade_client.place_order(order)
            msg = f"✅ 下单成功 (ID: {oid}): {action_str} {quantity}股"
            logger.info(msg)
            return msg
            
        except Exception as e:
            logger.error(f"❌ Tiger 下单异常: {e}")
            # 降级到演示模式
            return f"⚠️ 演示模式: {action_str} {quantity}股 (实际下单失败: {str(e)[:50]})"

    except Exception as e:
        logger.error(f"❌ 下单流程异常: {e}")
        return f"执行失败: {str(e)}"

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
    logger.info("🚀 机器人启动 (v3.2 账户管理增强版)")
    send_telegram("🚀 机器人已重启: 账户管理已启用")
    
    last_scan_time = time.time()
    
    while True:
        try:
            poll_telegram_updates()
            
            current_time = time.time()
            if (current_time - last_scan_time > config.SCAN_INTERVAL) and WATCH_LIST:
                logger.info(f"⏰ 触发定时扫描 (间隔: {config.SCAN_INTERVAL}s)")
                
                data_manager.batch_fetch_all(WATCH_LIST)
                
                for symbol in WATCH_LIST:
                    run_analysis(symbol, silent=False)
                
                last_scan_time = current_time
                
        except Exception as e:
            logger.error(f"❌ 主循环发生异常: {e}")
            time.sleep(5)
            
        time.sleep(1)
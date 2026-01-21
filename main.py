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
    print("请运行: pip install openai pandas requests tigeropen")
    sys.exit(1)

# 本地模块
try:
    import config
    from data_processor import MarketDataProcessor
except ImportError as e:
    print(f"❌ 缺少本地文件: {e}")
    sys.exit(1)

# ================= 2. 全局变量与配置 =================

# 🔧 强制配置日志 (修复 Log 不显示的问题)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trade_bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout) # 强制输出到控制台
    ],
    force=True # ⚠️ 关键：覆盖第三方库的默认配置
)
logger = logging.getLogger()

# 全局客户端对象
tiger_client = None
tiger_trade_client = None
deepseek_client = None
WATCH_LIST = []
LAST_UPDATE_ID = 0

# 👇👇👇 SYSTEM PROMPT (威科夫操盘专家) 👇👇👇
system_prompt = """
### Role Definition
你是一名精通威科夫理论（Wyckoff Method）、量价分析（VPA）和经典技术分析的股市短线操盘专家。你的核心目标是利用技术分析手段，捕捉市场中的供求失衡点，跟随“主力资金（Smart Money/Composite Man）”的动向，以极高的短期胜率获取超额收益。你的交易哲学融合了本间宗久“风林火山”的战术纪律和查理·芒格“等待好球（Fat Pitch）”的耐心。

### Core Analysis Framework (Strict 5-Step)
在分析任何标的时，必须严格遵循以下五步分析法：

#### 第一步：市场背景与趋势定位 (Context & Trend)
- **趋势识别**：使用道氏理论定义趋势（高点更高为多头，低点更低为空头）。结合均线系统（如MA10, MA20, MA60）判断短期与中期趋势方向。
- **威科夫阶段**：判断当前处于威科夫周期的哪个阶段：吸筹（Accumulation）、拉升（Markup）、派发（Distribution）还是下跌（Markdown）。
- **位置判定**：识别关键的水平支撑位（冰线 Ice Line）和阻力位（小溪 Creek）。在趋势回调中，关注50%回撤位的支撑或压力表现。

#### 第二步：量价关系分析 (Volume-Price Analysis - VPA)
- **核心定律**：应用威科夫三大定律（供求定律、因果定律、投入产出定律）。
- **异常识别**：寻找量价背离。
- **确认信号**：价格上涨伴随成交量放大（投入大，产出大）= 趋势健康。
- **警示信号**：高成交量伴随窄幅K线实体（努力没结果）= 停止行为（Stopping Volume），暗示反转。
- **空头陷阱**：低量测试支撑（Test）或缩量回调，表明供应枯竭。

#### 第三步：K线形态与盘口解读 (Candlestick Patterns)
- **关键K线**：识别反转和持续信号，如射击十字星、吊人线、锤头线、高开阴线/阳线等。
- **盘口定式**：分析开盘价与收盘价的意图。例如，高开低走放量可能为主力出货；平开高走放量可能为拉升初期。
- **影线含义**：长上影线代表供应（卖压），长下影线代表需求（买盘支撑）。

#### 第四步：技术指标辅助 (Indicator Confirmation)
- **MACD**：利用MACD判断动能。关注“将死未死”的空中加油形态或底背离/顶背离信号。
- **RSI**：识别超买（Overbought）与超卖（Oversold）区域，但需注意强趋势中的指标钝化。
- **均线**：利用均线作为动态支撑/阻力，观察价格是否站稳关键均线之上。

#### 第五步：交易决策与风控 (Decision & Risk)
- **入场信号（多头）**：Spring（弹簧效应）、JOC（跳跃小溪，缩量回踩不破）、底分型放量止跌。
- **离场信号（风控）**：UT（上冲回落，伴随巨量）、SOW（弱势信号，放量跌破支撑）。
- **心态控制**：遵循“不动如山”，若无明确的高胜率信号（Fat Pitch），则保持空仓观望。

### Output Format (Markdown Report + JSON Summary)
请按以下 Markdown 格式输出分析报告，并在最后附带 JSON Summary：

#### 1. 📊 趋势与结构 (Trend & Structure)
* **当前趋势**: [上涨/下跌/震荡]
* **威科夫阶段**: [吸筹/拉升/派发/下跌]
* **关键位置**: 支撑位 [价格], 阻力位 [价格]

#### 2. 🕯️ 量价与K线解读 (VPA & Patterns)
* **量价状态**: [量价配合/量价背离/努力无结果]
* **分析**: [详细描述近期关键K线的成交量与实体关系]
* **主力痕迹**: [是否存在主力吸筹、洗盘或出货的迹象]

#### 3. 📈 指标共振 (Indicators)
* **MACD**: [动能状态]
* **均线系统**: [价格与MA的关系]

#### 4. 🚀 交易计划 (Trading Plan)
* **操作建议**: **[买入 / 卖出 / 观望]**
* **胜率逻辑**: [简述为何这是一笔高胜率交易]
* **入场点 (Entry)**: [具体价格区间]
* **止损点 (Stop Loss)**: [严格的风控位置]
* **目标位 (Target)**: [预期价格]

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

# ================= 3. 核心功能函数 =================

def _get_private_key_path():
    """处理私钥路径：支持环境变量中的私钥内容或文件路径"""
    import tempfile
    private_key_path = config.TIGER_PRIVATE_KEY
    
    # 判断是否为私钥内容（而不是文件路径）
    is_key_content = (private_key_path and 
                     not private_key_path.endswith('.pem') and 
                     len(private_key_path) > 100)
    
    if is_key_content:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
            f.write(private_key_path)
            private_key_path = f.name
        logger.info(f"📝 私钥从环境变量加载，临时文件: {private_key_path}")
    else:
        logger.info(f"📝 使用本地私钥文件: {private_key_path}")
    
    return private_key_path

def _parse_json_response(ai_text):
    """从 AI 响应中解析 JSON，支持多种格式"""
    json_patterns = [
        r'JSON_SUMMARY\s*[:：]\s*({.*?})',  # 标准格式
        r'```json\s*({.*?})\s*```',         # 代码块格式
        r'(\{[^{}]*"action"[^{}]*\})',      # 任意包含 action 字段的 JSON
    ]
    
    for pattern in json_patterns:
        json_match = re.search(pattern, ai_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except (json.JSONDecodeError, IndexError) as e:
                logger.debug(f"JSON 解析失败 (模式: {pattern}): {e}")
    
    logger.warning("⚠️ 未找到有效的 JSON_SUMMARY，使用默认值")
    return {}

def init_services():
    """初始化 Tiger 和 DeepSeek 客户端"""
    global tiger_client, tiger_trade_client, deepseek_client
    
    print("⏳ 正在初始化服务...")
    logger.info("🔌 正在连接 DeepSeek API...")
    try:
        deepseek_client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY, 
            base_url=getattr(config, 'DEEPSEEK_BASE_URL', "https://api.deepseek.com")
        )
        logger.info("✅ DeepSeek 连接配置完成")
    except Exception as e:
        logger.critical(f"❌ DeepSeek 连接失败: {e}")
        sys.exit(1)

    logger.info("🐯 正在连接 Tiger API...")
    try:
        client_config = TigerOpenClientConfig(sandbox_debug=config.IS_SANDBOX)
        client_config.private_key = read_private_key(_get_private_key_path())
        client_config.tiger_id = config.TIGER_ID
        client_config.account = config.TIGER_ACCOUNT
        client_config.language = Language.zh_CN 
        
        tiger_client = QuoteClient(client_config)
        tiger_trade_client = TradeClient(client_config)
        
        perm = tiger_client.get_quote_permission()
        logger.info(f"✅ Tiger API 连接成功. 权限: {perm}")
    except Exception as e:
        logger.critical(f"❌ Tiger API 初始化失败: {e}")
        sys.exit(1)

def get_stock_name(symbol):
    """获取股票名称"""
    symbol = symbol.upper().strip()
    query_list = [symbol]
    
    # 自动补全后缀猜测
    if symbol.isdigit():
        if len(symbol) == 5: query_list.append(f"{symbol}.HK")
        elif len(symbol) == 6:
            if symbol.startswith('6'): query_list.append(f"{symbol}.SH")
            else: query_list.append(f"{symbol}.SZ")

    try:
        contracts = tiger_trade_client.get_contracts(symbol=query_list)
        if contracts:
            for c in contracts:
                if c.name and c.name.strip() and c.name.upper() != symbol:
                    return c.name
    except Exception as e:
        logger.warning(f"获取名称失败 ({symbol}): {e}")

    return symbol

def get_market_data(symbol):
    """获取 K 线数据"""
    symbol = symbol.upper().strip()
    clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
    
    logger.info(f"🔍 [Tiger] 获取行情: {clean_symbol}")
    try:
        bars = tiger_client.get_bars(
            symbols=[clean_symbol],
            period='60min',  # ⚠️ 修复：直接使用字符串 '60min'
            limit=100,
            right=QuoteRight.BR
        )
        if bars is None or bars.empty:
            logger.warning(f"⚠️ {clean_symbol} 数据为空")
            return None
            
        logger.info(f"📊 {clean_symbol} 成功获取 K线: {len(bars)} 根")
        
        # 数据清洗
        df = bars.copy()
        df.rename(columns={
            'time': 'Datetime', 'open': 'Open', 'high': 'High',
            'low': 'Low', 'close': 'Close', 'volume': 'Volume'
        }, inplace=True)
        return df
    except Exception as e:
        logger.error(f"❌ Tiger 行情接口报错: {e}")
        return None

def send_telegram(msg):
    """发送 Telegram 消息"""
    if not getattr(config, 'TG_BOT_TOKEN', None) or not getattr(config, 'TG_CHAT_IDS', []):
        logger.warning("⚠️ Telegram 未配置，跳过发送")
        return

    url = f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/sendMessage"
    proxies = getattr(config, 'PROXIES', None)
    
    for chat_id in config.TG_CHAT_IDS:
        try:
            resp = requests.post(url, json={"chat_id": str(chat_id), "text": msg}, 
                               proxies=proxies, timeout=10)
            if resp.status_code == 200:
                logger.info(f"📤 消息已推送到 TG: {chat_id}")
            else:
                logger.error(f"❌ TG 推送失败 (HTTP {resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ TG 推送异常: {e}")

# ================= 4. 分析主逻辑 =================

def run_analysis(symbol, silent=False):
    """运行分析流程: 获取数据 -> 处理指标 -> AI分析 -> 结果解析"""
    # 1. 获取数据
    df = get_market_data(symbol)
    if df is None or len(df) < 50: 
        return None
        
    stock_name = get_stock_name(symbol)
    current_price = df.iloc[-1]['Close']

    try:
        # 2. 计算指标
        logger.info(f"🧮 正在计算威科夫指标: {stock_name}...")
        processor = MarketDataProcessor(df)
        data_json = processor.get_analysis_payload(symbol, timeframe="60m")
        if data_json is None: 
            return None

        # 3. 调用 AI
        if not silent:
            logger.info(f"🧠 DeepSeek 正在思考: {stock_name}...")
        
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"### MARKET DATA INPUT (JSON):\n{data_json}"}
            ],
            stream=False,
            temperature=0.2 
        )
        
        ai_text = response.choices[0].message.content
        logger.info(f"✅ DeepSeek 分析完成: {stock_name} (长度: {len(ai_text)})")
        
        # 4. 解析 JSON 结果
        parsed_res = _parse_json_response(ai_text)
        action = parsed_res.get('action', 'WAIT')
        confidence = parsed_res.get('confidence', 0)
        
        # 5. 发送报告
        if not silent:
            report = f"🐯 {stock_name} ({symbol}) 威科夫分析\n\n"
            report += f"操作: {action}\n信度: {confidence}%\n\n"
            report += f"详细分析:\n{ai_text[:1000]}..."
            send_telegram(report)

        return {
            "symbol": symbol,
            "name": stock_name,
            "price": current_price,
            "action": action,
            "confidence": confidence,
            "reason": parsed_res.get('reason', '请查看详情')
        }

    except Exception as e:
        logger.error(f"❌ 分析流程异常: {e}")
        return None

# ================= 5. 主程序入口 =================

def handle_command(cmd):
    """命令处理器"""
    global WATCH_LIST
    cmd = cmd.strip().upper()
    
    if cmd.startswith("/TRACK"):
        parts = cmd.split()
        if len(parts) > 1:
            WATCH_LIST = list(set(parts[1:]))
            return f"✅ 监控列表已更新: {WATCH_LIST}"
    elif cmd == "/CLEAR":
        WATCH_LIST = []
        return "✅ 任务列表已清空，程序恢复待命状态"
    
    return None

def poll_telegram_updates():
    """轮询 Telegram 消息"""
    global LAST_UPDATE_ID
    
    if not getattr(config, 'TG_BOT_TOKEN', None):
        time.sleep(10)
        return

    try:
        url = f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/getUpdates"
        params = {"offset": LAST_UPDATE_ID + 1, "timeout": 1}
        proxies = getattr(config, 'PROXIES', None)
        
        resp = requests.get(url, params=params, proxies=proxies, timeout=5)
        data = resp.json()
        
        if data.get("ok") and data.get("result"):
            for item in data["result"]:
                LAST_UPDATE_ID = item["update_id"]
                text = item.get("message", {}).get("text", "")
                chat_id = item.get("message", {}).get("chat", {}).get("id")
                
                if text.startswith("/"):
                    logger.info(f"📩 收到指令: {text}")
                    reply = handle_command(text)
                    
                    if reply:
                        # 回复用户
                        requests.post(
                            f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/sendMessage",
                            json={"chat_id": chat_id, "text": reply},
                            proxies=proxies
                        )
                        
                        # 触发扫描
                        if WATCH_LIST:
                            logger.info(f"⚡️ 触发扫描: {WATCH_LIST}")
                            for s in WATCH_LIST:
                                run_analysis(s, silent=False)
    except Exception as e:
        logger.error(f"轮询错误: {e}")
        time.sleep(5)

if __name__ == "__main__":
    init_services()
    logger.info("🚀 机器人已启动，正在监听指令...")
    
    # 发送启动确认消息
    startup_msg = """🚀 交易机器人已启动，等待指令...

📋 支持的指令：

1️⃣ /track SYMBOL
   添加股票到监控列表并执行分析
   示例：/track 00700
   示例：/track 00700 AAPL 000001
   (支持多个股票代码，空格分隔)

2️⃣ /clear
   清空任务列表，恢复待命状态
   示例：/clear

3️⃣ /help
   显示帮助信息（即将支持）

4️⃣ /list
   显示当前监控列表（即将支持）

⏰ 使用说明：
- 发送 /track 指令后，机器人会立即分析该股票
- 使用威科夫理论进行深度技术分析
- 返回操作建议 (BUY/SELL/WAIT) 和信心度
- 使用 /clear 可停止当前分析"""
    
    send_telegram(startup_msg)
    
    # 手动测试模式
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        test_symbol = sys.argv[2] if len(sys.argv) > 2 else "00700"
        logger.info(f"🧪 执行测试扫描: {test_symbol}")
        run_analysis(test_symbol, silent=False)
        sys.exit(0)
    
    # 主循环
    while True:
        poll_telegram_updates()
        time.sleep(1)
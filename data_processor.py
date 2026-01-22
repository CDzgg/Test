import pandas as pd
import pandas_ta as ta
import json
import numpy as np

class MarketDataProcessor:
    def __init__(self, data_dict, quote_data=None):
        """
        初始化数据处理器
        :param data_dict: 包含不同周期 K 线数据的字典
               Example: {'intraday': df_5m, 'longterm': df_4h}
        :param quote_data: 实时盘口快照
               Example: {'mid_price': 100.5, 'open_interest': 5000}
        """
        self.data = {}
        self.quote_data = quote_data or {}
        
        # 预处理 K 线数据：复制并计算指标
        if 'intraday' in data_dict and data_dict['intraday'] is not None:
            self.data['intraday'] = data_dict['intraday'].copy()
            self._process_intraday_indicators(self.data['intraday'])
            
        if 'longterm' in data_dict and data_dict['longterm'] is not None:
            self.data['longterm'] = data_dict['longterm'].copy()
            self._process_longterm_indicators(self.data['longterm'])

    def _process_intraday_indicators(self, df):
        """
        计算日内短线指标 (5m)
        目标: 捕捉短期入场点和动量
        """
        # 数据长度检查
        if len(df) < 20: return

        # 1. EMA20 (趋势判断)
        df['EMA20'] = ta.ema(df['Close'], length=20)
        
        # 2. MACD (动量与趋势)
        macd = ta.macd(df['Close'])
        if macd is not None:
            df['MACD'] = macd['MACD_12_26_9']
            df['MACD_Signal'] = macd['MACDs_12_26_9']
            df['MACD_Hist'] = macd['MACDh_12_26_9']
        
        # 3. RSI7 (快速超买超卖)
        df['RSI7'] = ta.rsi(df['Close'], length=7)
        
        # 4. RSI14 (标准超买超卖)
        df['RSI14'] = ta.rsi(df['Close'], length=14)

    def _process_longterm_indicators(self, df):
        """
        计算长线趋势指标 (e.g. 4h)
        目标: 判断大趋势和波动率风险
        """
        if len(df) < 50: return

        # 1. EMA20 & EMA50 (中期趋势支撑/阻力)
        df['EMA20'] = ta.ema(df['Close'], length=20)
        df['EMA50'] = ta.ema(df['Close'], length=50)
        
        # 2. ATR (波动率与风控)
        # ATR3: 极短期波动率，用于紧凑止损
        df['ATR3'] = ta.atr(df['High'], df['Low'], df['Close'], length=3)
        # ATR14: 标准波动率，用于计算风险敞口
        df['ATR14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
        
        # 3. MACD (长线动量)
        macd = ta.macd(df['Close'])
        if macd is not None:
            df['MACD'] = macd['MACD_12_26_9']
            df['MACD_Signal'] = macd['MACDs_12_26_9']
            df['MACD_Hist'] = macd['MACDh_12_26_9']
            
        # 4. RSI14 (长线强弱)
        df['RSI14'] = ta.rsi(df['Close'], length=14)

    def get_analysis_payload(self, symbol):
        """
        生成发送给 AI 的完整数据包，包含实时状态和双周期指标
        """
        
        # --- 1. 基础市场状态 (实时 & 队列) ---
        # 优先使用实时快照的 Mid-price，如果没有则用 K线最新价兜底
        current_price = self.quote_data.get('mid_price')
        if current_price is None and 'intraday' in self.data:
            if not self.data['intraday'].empty:
                current_price = self.data['intraday'].iloc[-1]['Close']

        # 获取最近 60 个价格点 (队列)，用于短期趋势参考
        recent_history = []
        if 'intraday' in self.data and not self.data['intraday'].empty:
            # 取最后 60 条 Close 价格，转换为列表
            recent_history = self.data['intraday']['Close'].tail(60).round(2).tolist()

        market_state = {
            "current_mid_price": round(current_price, 3) if current_price else None,
            "open_interest": self.quote_data.get('open_interest', "N/A"),
            "recent_price_history_60_points": recent_history
        }

        payload = {
            "symbol": symbol,
            "market_state": market_state,
            "intraday_5m": "Data Insufficient",
            "longterm_4h": "Data Insufficient"
        }

        # --- 2. 组装 5m 数据 (微观) ---
        if 'intraday' in self.data and len(self.data['intraday']) > 20:
            df = self.data['intraday']
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            payload['intraday_5m'] = {
                "close": round(curr['Close'], 2),
                "volume": int(curr['Volume']),
                "trend_ema20": round(curr['EMA20'], 3) if pd.notnull(curr.get('EMA20')) else None,
                "momentum": {
                    "macd_hist": round(curr['MACD_Hist'], 4) if pd.notnull(curr.get('MACD_Hist')) else 0,
                    "macd_prev_hist": round(prev['MACD_Hist'], 4) if pd.notnull(prev.get('MACD_Hist')) else 0,
                    "rsi7": round(curr['RSI7'], 1) if pd.notnull(curr.get('RSI7')) else None,
                    "rsi14": round(curr['RSI14'], 1) if pd.notnull(curr.get('RSI14')) else None
                }
            }

        # --- 3. 组装 4h 数据 (宏观) ---
        if 'longterm' in self.data and len(self.data['longterm']) > 50:
            df = self.data['longterm']
            curr = df.iloc[-1]
            
            # 简单的趋势结构判断
            trend = "Neutral"
            if pd.notnull(curr.get('EMA20')) and pd.notnull(curr.get('EMA50')):
                if curr['Close'] > curr['EMA20'] > curr['EMA50']: trend = "Bullish"
                elif curr['Close'] < curr['EMA20'] < curr['EMA50']: trend = "Bearish"

            payload['longterm_4h'] = {
                "structure": trend,
                "ema20": round(curr['EMA20'], 3) if pd.notnull(curr.get('EMA20')) else None,
                "ema50": round(curr['EMA50'], 3) if pd.notnull(curr.get('EMA50')) else None,
                "volatility_risk": {
                    "atr3_tight_stop": round(curr['ATR3'], 3) if pd.notnull(curr.get('ATR3')) else None,
                    "atr14_risk_exposure": round(curr['ATR14'], 3) if pd.notnull(curr.get('ATR14')) else None
                },
                "indicators": {
                    "macd_long": round(curr['MACD'], 4) if pd.notnull(curr.get('MACD')) else None,
                    "rsi14_long": round(curr['RSI14'], 1) if pd.notnull(curr.get('RSI14')) else None
                }
            }

        return json.dumps(payload, ensure_ascii=False, indent=2)
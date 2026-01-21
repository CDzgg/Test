import pandas as pd
import pandas_ta as ta
import json
import numpy as np

class MarketDataProcessor:
    def __init__(self, df):
        """
        df: 包含 Open, High, Low, Close, Volume 的 DataFrame
        """
        self.df = df.copy()
        self._process_indicators()

    def _process_indicators(self):
        # 1. 基础均线 (趋势判断)
        self.df['MA10'] = ta.ema(self.df['Close'], length=10)
        self.df['MA20'] = ta.ema(self.df['Close'], length=20)
        self.df['MA60'] = ta.ema(self.df['Close'], length=60)
        
        # 2. 动能指标
        self.df['RSI'] = ta.rsi(self.df['Close'], length=14)
        
        # MACD
        macd = ta.macd(self.df['Close'])
        self.df['MACD'] = macd['MACD_12_26_9']
        self.df['MACD_Signal'] = macd['MACDs_12_26_9']
        self.df['MACD_Hist'] = macd['MACDh_12_26_9']
        
        # 3. 成交量分析 (VPA核心)
        # 计算20周期平均成交量，用于判断是否"放量"
        self.df['Vol_MA20'] = ta.sma(self.df['Volume'], length=20)
        self.df['Vol_Ratio'] = self.df['Volume'] / self.df['Vol_MA20'] # >1.5 为放量
        
        # 4. ATR (用于止损计算)
        self.df['ATR'] = ta.atr(self.df['High'], self.df['Low'], self.df['Close'], length=14)

    def get_analysis_payload(self, symbol, timeframe="Unknown"):
        """
        生成符合 System Prompt 要求的 JSON 数据包
        """
        if len(self.df) < 60:
            return None
            
        curr = self.df.iloc[-1]
        prev = self.df.iloc[-2]
        
        # 提取最近 5 根 K 线用于盘口解读
        recent_candles = []
        for i in range(5):
            idx = -(5-i)
            row = self.df.iloc[idx]
            
            # 简单的K线形态描述 (辅助AI)
            body_size = abs(row['Close'] - row['Open'])
            upper_shadow = row['High'] - max(row['Close'], row['Open'])
            lower_shadow = min(row['Close'], row['Open']) - row['Low']
            desc = []
            if row['Vol_Ratio'] > 1.5: desc.append("放量")
            if row['Vol_Ratio'] < 0.6: desc.append("缩量")
            if upper_shadow > body_size * 2: desc.append("长上影(抛压)")
            if lower_shadow > body_size * 2: desc.append("长下影(支撑)")
            
            candle = {
                "offset": str(idx), # T-0 是当前, T-1 是上一根
                "open": round(row['Open'], 2),
                "high": round(row['High'], 2),
                "low": round(row['Low'], 2),
                "close": round(row['Close'], 2),
                "volume": int(row['Volume']),
                "rel_volume": round(row['Vol_Ratio'], 2), # 相对成交量
                "features": ", ".join(desc)
            }
            recent_candles.append(candle)

        # 趋势状态判断
        trend_status = "Neutral"
        if curr['Close'] > curr['MA20'] and curr['MA20'] > curr['MA60']:
            trend_status = "Bullish (Strong)"
        elif curr['Close'] < curr['MA20'] and curr['MA20'] < curr['MA60']:
            trend_status = "Bearish (Strong)"
            
        macd_status = "Positive" if curr['MACD_Hist'] > 0 else "Negative"
        if curr['MACD_Hist'] > prev['MACD_Hist'] and curr['MACD_Hist'] > 0:
            macd_status += " (Expanding)"
        elif curr['MACD_Hist'] < prev['MACD_Hist'] and curr['MACD_Hist'] > 0:
            macd_status += " (Weakening)"

        # 关键支撑压力 (简单的近期高低点)
        recent_high = self.df['High'].tail(30).max()
        recent_low = self.df['Low'].tail(30).min()

        # 组装最终 JSON
        payload = {
            "target_info": {
                "symbol": symbol,
                "analysis_timeframe": timeframe,
                "note": "Based on provided document methodology"
            },
            "current_market_data": {
                "price": round(curr['Close'], 2),
                "volume": int(curr['Volume']),
                "avg_volume_20": int(curr['Vol_MA20']),
                "vol_ratio": round(curr['Vol_Ratio'], 2),
                "atr": round(curr['ATR'], 2),
                "indicators": {
                    "ma20": round(curr['MA20'], 2),
                    "ma60": round(curr['MA60'], 2),
                    "rsi": round(curr['RSI'], 1),
                    "macd_hist": macd_status,
                    "trend_structure": trend_status
                }
            },
            "recent_price_action": recent_candles,
            "key_levels_context": {
                "recent_30_period_high": round(recent_high, 2),
                "recent_30_period_low": round(recent_low, 2)
            }
        }
        
        return json.dumps(payload, ensure_ascii=False, indent=2)
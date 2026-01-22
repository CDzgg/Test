import pandas as pd
import pandas_ta as ta
import json
import numpy as np

class MarketDataProcessor:
    def __init__(self, data_dict, quote_data=None):
        """
        初始化处理器，引入严格的数据标准化层
        """
        self.data = {}
        self.quote_data = quote_data or {}
        
        # 数据加载与预处理
        if 'intraday' in data_dict and data_dict['intraday'] is not None:
            self.data['intraday'] = data_dict['intraday'].copy()
            self._process_intraday_indicators(self.data['intraday'])
            
        if 'longterm' in data_dict and data_dict['longterm'] is not None:
            self.data['longterm'] = data_dict['longterm'].copy()
            self._process_longterm_indicators(self.data['longterm'])

    # ================= 1. 数据标准化核心 (Standardization Layer) =================
    
    def _extract_val(self, value, default=None):
        """
        A. 单点数值提取
        - 功能: 提取标量或 Series 的最后一个值
        - 精度: 强制 round(val, 4)
        - 异常: 返回 None
        """
        try:
            if value is None: return default
            
            # 如果是 Series/Array，取最后一个值
            if isinstance(value, (pd.Series, np.ndarray)):
                if len(value) == 0: return default
                value = value.iloc[-1] if isinstance(value, pd.Series) else value[-1]
            
            # 转换与清洗
            val_float = float(value)
            if np.isnan(val_float) or np.isinf(val_float):
                return default
                
            return round(val_float, 4)
        except Exception:
            return default

    def _extract_seq(self, series, length=60):
        """
        B. 序列数据提取
        - 功能: 提取 Series 的最后 N 个值
        - 精度: 列表内每个元素强制 round(val, 4)
        - 格式: [1.2345, 2.3456, ...]
        - 异常: 返回空列表 []
        """
        try:
            if series is None or len(series) == 0:
                return []
            
            # 截取最后 N 个数据
            target = series.tail(length)
            
            # 列表推导式：过滤无效值并统一精度
            return [
                round(float(x), 4) 
                for x in target 
                if pd.notnull(x) and not np.isinf(float(x))
            ]
        except Exception:
            return []

    def _get_trend_tag(self, price, ma_short, ma_long):
        """生成趋势语义标签 (辅助 AI 判断)"""
        try:
            p = float(price)
            ms = float(ma_short)
            ml = float(ma_long)
            if p > ms > ml: return "Bullish_Strong"
            if p < ms < ml: return "Bearish_Strong"
            if p > ml and p < ms: return "Correction_Bullish"
            if p < ml and p > ms: return "Rebound_Bearish"
            return "Consolidation"
        except:
            return "Unknown"

    # ================= 2. 指标计算逻辑 (Indicators) =================

    def _process_intraday_indicators(self, df):
        if len(df) < 20: return
        # 趋势
        df['EMA20'] = ta.ema(df['Close'], length=20)
        
        # 动能 (MACD)
        macd = ta.macd(df['Close'])
        if macd is not None:
            df['MACD_Hist'] = macd['MACDh_12_26_9']
            df['MACD_Line'] = macd['MACD_12_26_9']
        
        # 震荡 (RSI)
        df['RSI7'] = ta.rsi(df['Close'], length=7)
        df['RSI14'] = ta.rsi(df['Close'], length=14)

    def _process_longterm_indicators(self, df):
        if len(df) < 50: return
        # 趋势
        df['EMA20'] = ta.ema(df['Close'], length=20)
        df['EMA50'] = ta.ema(df['Close'], length=50)
        
        # 波动率
        df['ATR3'] = ta.atr(df['High'], df['Low'], df['Close'], length=3)
        df['ATR14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
        
        # 长线动能
        macd = ta.macd(df['Close'])
        if macd is not None:
            df['MACD'] = macd['MACD_12_26_9']
        df['RSI14'] = ta.rsi(df['Close'], length=14)

    # ================= 3. Payload 组装 (Payload Assembly) =================

    def get_analysis_payload(self, symbol):
        """生成标准化、扁平化、高精度的 AI 数据包"""
        
        # --- A. 市场状态 (Market State) ---
        current_price = self.quote_data.get('mid_price')
        # 回退逻辑：如果实时 Quote 缺失，使用 5m Close (最新已完成的价格)
        if self._extract_val(current_price) is None and 'intraday' in self.data:
             if not self.data['intraday'].empty:
                # 【修复】使用倒数第二根（已完成）的收盘价作为回退，避免未完成数据的干扰
                current_price = self.data['intraday'].iloc[-2]['Close']

        # 提取最近 60 个价格点 (Trend Context)
        price_seq = []
        if 'intraday' in self.data:
            # 【修复】关键修改：剔除最后一行（未完成的 K 线），只取已确认的历史数据
            # 这样 AI 看到的形态是完全确定的，不会重绘
            completed_data = self.data['intraday'].iloc[:-1]
            price_seq = self._extract_seq(completed_data['Close'], length=60)

        market_state = {
            "price_current": self._extract_val(current_price),
            "open_interest": self.quote_data.get('open_interest', "N/A"),
            "price_sequence_60": price_seq
        }

        # --- B. 5m 数据 (Micro Structure) ---
        indicators_5m = "Data Insufficient"
        if 'intraday' in self.data and len(self.data['intraday']) > 20:
            df = self.data['intraday']
            # 【修复】关键修改：指针前移
            # curr = iloc[-2] (上一根，已完成)
            # prev = iloc[-3] (上上根，已完成)
            curr = df.iloc[-2]
            prev = df.iloc[-3]
            
            # 为了计算序列特征，我们也只取已完成的部分
            df_completed = df.iloc[:-1]
            
            indicators_5m = {
                "close": self._extract_val(curr['Close']),
                "volume": int(self._extract_val(curr['Volume'], 0)), 
                "ema20": self._extract_val(curr.get('EMA20')),
                # 动能指标
                "macd_hist": self._extract_val(curr.get('MACD_Hist')),
                "macd_hist_prev": self._extract_val(prev.get('MACD_Hist')), 
                "macd_hist_seq_10": self._extract_seq(df_completed['MACD_Hist'], length=10),
                # 震荡指标
                "rsi7": self._extract_val(curr.get('RSI7')),
                "rsi14": self._extract_val(curr.get('RSI14')),
                "rsi7_seq_10": self._extract_seq(df_completed['RSI7'], length=10) 
            }

        # --- C. 4h 数据 (Macro Context) ---
        indicators_4h = "Data Insufficient"
        if 'longterm' in self.data and len(self.data['longterm']) > 50:
            df = self.data['longterm']
            # 【修复】同样使用已完成的 K 线
            curr = df.iloc[-2]
            
            p = self._extract_val(curr['Close'])
            e20 = self._extract_val(curr.get('EMA20'))
            e50 = self._extract_val(curr.get('EMA50'))

            indicators_4h = {
                "trend_tag": self._get_trend_tag(p, e20, e50),
                "ema20": e20,
                "ema50": e50,
                "atr3": self._extract_val(curr.get('ATR3')),
                "atr14": self._extract_val(curr.get('ATR14')),
                "macd": self._extract_val(curr.get('MACD')),
                "rsi14": self._extract_val(curr.get('RSI14'))
            }

        # --- D. 最终封装 ---
        payload = {
            "symbol": symbol,
            "timestamp": pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            "note": "Analysis based on COMPLETED candles only (Lagged by 1 period).",
            "market_state": market_state,
            "indicators": {
                "intraday_5m": indicators_5m,
                "longterm_4h": indicators_4h
            }
        }

        return json.dumps(payload, ensure_ascii=False, indent=2)
APP_NAME = 'AI Market Decision V3'

US_STOCKS = [
    'NVDA','AMD','AVGO','MSFT','AMZN','META','GOOGL','AAPL','TSLA','NFLX',
    'JPM','BAC','XOM','CVX','LLY','UNH','COST','WMT','ORCL','CRM',
    'INTC','QCOM','MU','PLTR','SMCI','ARM','GE','CAT','BA','UBER'
]
EU_STOCKS = [
    'SAP.DE','ASML.AS','MC.PA','TTE.PA','SIE.DE','AIR.PA','SU.PA','AI.PA',
    'SAN.MC','ENEL.MI','ISP.MI','ENI.MI','UCG.MI'
]
ETFS = ['QQQ','SPY','IWM','XLK','XLF','XLE','XLV','SMH','TLT','GLD','SLV']

BENCHMARKS = {
    'SPY': 'SPY',
    'QQQ': 'QQQ',
    'VIX': '^VIX',
    'TNX': '^TNX',
    'OIL': 'CL=F',
    'GOLD': 'GC=F',
    'BTC': 'BTC-USD',
}
ALL_UNIVERSE = list(dict.fromkeys(US_STOCKS + EU_STOCKS + ETFS))

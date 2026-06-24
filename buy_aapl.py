from dotenv import load_dotenv
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()

client = TradingClient(
    os.getenv("ALPACA_KEY"),
    os.getenv("ALPACA_SECRET"),
    paper=True,
)

order = client.submit_order(
    MarketOrderRequest(
        symbol="AAPL",
        qty=1,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
)

print(f"Order submitted!")
print(f"  ID:     {order.id}")
print(f"  Symbol: {order.symbol}")
print(f"  Qty:    {order.qty}")
print(f"  Side:   {order.side}")
print(f"  Status: {order.status}")
print(f"  Type:   {order.order_type}")

from decimal import Decimal

# Decimal, not float: this is the opening cash balance of a money ledger and
# it is written straight into Numeric(12,2). Starting it as a float would put
# a binary-float value at the root of every balance derived from it — see the
# money-is-Decimal section of services/paper_trading.py.
DEFAULT_VIRTUAL_CAPITAL = Decimal("1000000.00")

from typing import *
from pprint import pprint
from dataclasses import dataclass
from collections import defaultdict

import datetime
import argparse
import time
import sys

import stellar_sdk as sdk
import matplotlib.pyplot as plt
import numpy as np



@dataclass
class NativeBalanceLine:
    asset_type: str
    balance: str
    buying_liabilities: str
    selling_liabilities: str

    def __repr__(self):
        return f"XLM: {float(self.balance):.3f}"

    @property
    def asset_code(self):
        return "XLM"

    @property
    def asset_issuer(self):
        return None


@dataclass
class BalanceLine:
    asset_code: str         # example: 'yXLM',
    asset_issuer: str       # example: 'GARDNV3Q7YGT4AKSDF25LT32YSCCW4EV22Y2TV3I2PU2MMXJTEDL5T55',
    asset_type: str         # example: 'credit_alphanum4',
    balance: str            # example: '0.0000000',
    buying_liabilities: str # example: '0.0000000',
    is_authorized: bool     # example: True,
    is_authorized_to_maintain_liabilities: bool # example: True,
    last_modified_ledger: int   # example: 40905685,
    limit: str                  # example: '922337203685.4775807',
    selling_liabilities: str    # example: '0.0000000'

    is_clawback_enabled: Optional[bool] = False

    def __repr__(self):
        return f"{self.asset_code}:{self.asset_issuer[:7]}: {float(self.balance):.3f}"


@dataclass
class Candle:
    line: Union[NativeBalanceLine, BalanceLine]
    asset: sdk.Asset

    avg: str
    base_volume: str
    close: str
    counter_volume: str
    high: str
    low: str
    open: str
    timestamp: str
    trade_count: str

    # we don't care about these
    close_r: Any  # format: {'d': '486566', 'n': '532140'}
    high_r: Any
    low_r: Any
    open_r: Any

    @property
    def date(self):
        return datetime.date.fromtimestamp(float(self.timestamp) / 1000)


parser = argparse.ArgumentParser(
    description="This utility answers a simple question: "
    "What would my portfolio have looked like over the past "
    "year with my current holdings? It *does not* take into "
    "account any trades or transfers. It's designed simply as "
    "a way to view the historical value of your current portfolio "
    "and **if you had no transfers** can be used as a proxy for "
    "historical performance.")
parser.add_argument("account", help="the account whose value you want to calculate (GABC...)")

args = parser.parse_args()
if not sdk.strkey.StrKey.is_valid_ed25519_public_key(args.account):
    parser.error("'account' must be a valid Stellar address (in the form 'G...')")

HORIZON_URL = "https://horizon.stellar.org"
USDC = sdk.Asset("USDC", "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN")
ACCOUNT = args.account
server = sdk.Server(HORIZON_URL)

account = server.load_account(ACCOUNT)
balances = account.raw_data["balances"]

today = datetime.datetime.now()
last_year = today - datetime.timedelta(days=364)

# For each bucket of time, stores the mapping of assets to their respective
# values at that time.
values: Dict[int, Dict[str, float]] = defaultdict(dict)

for i, row in enumerate(balances):
    if row["asset_type"] != "native":
        line = BalanceLine(**row)
    else:
        line = NativeBalanceLine(**row)

    base = sdk.Asset(line.asset_code, line.asset_issuer)
    balances[i]["asset"] = base

    if float(line.balance) < 0.001:
        continue

    print(f"Aggregating stats for {base.code} ({line.balance} held):")

    rows = []
    try:
        start = last_year
        # Daily candle for a whole year => ~365 records so we need to paginate.
        while today - start > datetime.timedelta(hours=1):
            try:
                agg = server.trade_aggregations(
                    base=base,
                    counter=USDC,
                    resolution=86400000,
                    start_time=int(1000 * start.timestamp()),
                    end_time=int(1000 * today.timestamp()),
                ).limit(100).call()
            except sdk.exceptions.BadRequestError as e:
                if str(e).find("429") != -1: # lazy af
                    print("Rate limits exceeded, waiting a minute...")
                    time.sleep(60)
                    continue
                else:
                    raise e

            new_rows = agg["_embedded"]["records"]
            if not rows and not new_rows:
                raise ValueError("no candles found")

            elif not new_rows:
                break

            rows.extend(new_rows)
            print(f"  {len(rows)} records...")

            start = datetime.datetime.fromtimestamp(
                float(new_rows[-1]["timestamp"]) / 1000
            ) + datetime.timedelta(hours=1)

    except (sdk.exceptions.NotFoundError, ValueError):
        print(f"Skipping {line}: no significant value found, or no USDC trades occurred.")
        continue

    for candle in (
        Candle(line=line, asset=base, **row) for row in rows
    ):
        c: Candle = candle      # helps with editor hinting
        value = float(c.line.balance) * float(c.close)
        values[c.timestamp][str(c.asset)] = value
        # print(f"[DEBUG] {c.line.balance} {c.asset.code} was worth ${value} on {c.date}")

print("Accumulating and plotting portfolio value for", ACCOUNT)

# Does the account have a USDC baseline of value to add? There won't be a
# USDC:USDC price point, obviously, so we need to just pad every asset
# accumulation with that holding.
baseline = [ row for row in balances if row["asset"] == USDC ] or 0
if baseline:
    baseline = float(baseline[0]["balance"] )
    # print(f"[DEBUG] Using ${baseline} USDC as portfolio baseline.")

# ensure that we plot in ascending timestamp order, since certain assets may or
# may not have existed at all points in time
values = { ts: values[ts] for ts in sorted(values) }

xaxis = [datetime.date.fromtimestamp(float(k) / 1000) for k in values.keys()]
yaxis = [baseline + sum(group.values()) for group in values.values()]

plt.xlabel("Time")
plt.xticks(rotation=45)
plt.ylabel("Value in USDC")
plt.title(f"Portfolio value from ({last_year.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')})")
plt.plot(xaxis, yaxis, label="Portfolio")

plt.legend()
plt.show()

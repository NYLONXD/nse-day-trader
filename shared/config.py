"""Your settings: trader/config.json. Standard library only.

Every setting has a default here; trader/config.json only needs what you change.
Market rules (session hours, ticks, holidays) are NOT settings; they live in market.py.

  python shared/config.py init    # writes trader/config.json with every default, if missing
  python shared/config.py show    # prints the settings in force and any problems
  python shared/config.py calendar [YYYY-MM-DD]   # is that day (default: the next 7 days)
                                  # a normal NSE session by your holiday list?
  python shared/config.py lists   # copies the empty holiday and tick-size lists into trader/,
                                  # where they survive plugin updates (fill them from NSE)

The trader/ folder is in the current directory (where you run Claude), or $NSE_TRADER_DIR.
"""

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import costs   # noqa: E402
import market  # noqa: E402

DEFAULTS = {
    "paper": True,                         # v1 is paper only; nothing here places orders
    "capital_inr": 10000,                 # the money this is for: only money you can afford to lose
    "cash_buffer_pct": 10,                 # always left unused
    "max_position_pct": 50,                # one position at most this share of capital
    "max_order_inr": 50000,                # one order at most this much
    "min_order_inr": 1000,                 # smaller orders are mostly costs
    "blocked": [],                         # symbols you never want to trade, e.g. ["YESBANK"]
    "flat_by": "15:10",                    # every position closed by this bar end (IST)
    "no_entries_after": "14:30",           # no new entries from this time (IST)
    "max_loss_per_trade_inr": 500,         # stop distance x quantity, plus costs, at most this
    "daily_loss_limit_inr": 1500,          # stop for the day once realised loss reaches this
    "max_trades_per_day": 3,
    "min_reward_risk": 1.5,                # target distance / stop distance, at least this
    "min_price_inr": 50,                   # scanner: skip stocks below this price
    "min_traded_value_inr": 100000000,     # scanner: median daily traded value, Rs. 10 cr
    "costs": costs.DEFAULT_COSTS,          # PLACEHOLDER rates: put your broker's real ones here
}

POSITIVE_NUMBERS = ("max_loss_per_trade_inr", "daily_loss_limit_inr", "max_trades_per_day",
                    "min_traded_value_inr", "min_reward_risk", "capital_inr", "max_order_inr",
                    "max_position_pct")


class ConfigError(ValueError):
    pass


def trader_dir():
    return market.trader_dir()


def config_path():
    return os.path.join(trader_dir(), "config.json")


def check(cfg):
    """A list of plain problems with a merged config (empty = fine)."""
    probs = list(market.validate_session_times(cfg["no_entries_after"], cfg["flat_by"]))
    for k in POSITIVE_NUMBERS:
        v = cfg[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            probs.append("%s must be a number above 0 (got %r)" % (k, v))
    v = cfg["min_price_inr"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        probs.append("min_price_inr must be a number, 0 or more (got %r)" % (v,))
    if not isinstance(cfg["max_trades_per_day"], int) or isinstance(cfg["max_trades_per_day"], bool):
        probs.append("max_trades_per_day must be a whole number")
    for k in ("cash_buffer_pct", "min_order_inr"):
        v = cfg[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            probs.append("%s must be a number, 0 or more (got %r)" % (k, v))
    for k in ("cash_buffer_pct", "max_position_pct"):
        v = cfg[k]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 100:
            probs.append("%s is a percent: 100 at most (got %r)" % (k, v))
    if not isinstance(cfg["blocked"], list):
        probs.append("blocked must be a list of symbols")
    else:
        for s in cfg["blocked"]:
            try:
                market.normalize_symbol(s)
            except market.MarketError:
                probs.append("blocked: %r is not an NSE symbol" % (s,))
    if not isinstance(cfg["costs"], dict):
        probs.append("costs must be an object of rates (see shared/costs.py)")
    else:
        probs.extend(costs.check(cfg["costs"]))
    if cfg["paper"] is not True:
        probs.append("paper must be true: v1 is paper/simulation only")
    if (isinstance(cfg["daily_loss_limit_inr"], (int, float)) and isinstance(cfg["max_loss_per_trade_inr"], (int, float))
            and cfg["daily_loss_limit_inr"] < cfg["max_loss_per_trade_inr"]):
        probs.append("daily_loss_limit_inr is below max_loss_per_trade_inr: one stopped trade would break it")
    return probs


def load(path=None):
    """(config dict, warnings). Raises ConfigError (one line per problem) when a value is wrong.
    A missing file means all defaults."""
    path = path or config_path()
    cfg = copy.deepcopy(DEFAULTS)
    warnings = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
        except (OSError, ValueError) as e:
            raise ConfigError("cannot read %s: %s" % (path, e))
        if not isinstance(user, dict):
            raise ConfigError("%s must be a JSON object" % path)
        for k, v in user.items():
            if k.startswith("_"):
                continue
            if k not in DEFAULTS:
                warnings.append("unknown setting %r in %s (a typo?) is ignored" % (k, path))
                continue
            if k == "costs" and isinstance(v, dict):
                cfg[k] = dict(cfg[k], **v)     # change one rate, keep the others
                continue
            cfg[k] = v
    probs = check(cfg)
    if probs:
        raise ConfigError("; ".join(probs))
    return cfg, warnings


def init(path=None):
    path = path or config_path()
    if os.path.exists(path):
        return False, path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULTS, f, indent=2)
        f.write("\n")
    return True, path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "show"
    if cmd == "init":
        made, path = init()
        print(("Wrote %s with the defaults." if made else "%s already exists; left as is.") % path)
        return 0
    if cmd == "lists":
        import shutil
        for name, src in (("nse_holidays.json", market.HOLIDAYS_FILE), ("nse_tick_sizes.json", market.TICKS_FILE)):
            dst = os.path.join(trader_dir(), name)
            if os.path.exists(dst):
                print("%s already exists; left as is." % dst)
                continue
            os.makedirs(trader_dir(), exist_ok=True)
            shutil.copyfile(src, dst)
            print("Wrote %s: fill it from NSE (the steps are inside). It wins over %s." % (dst, src))
        return 0
    if cmd == "calendar":
        import datetime as _dt
        try:
            cal = market.load_calendar()
        except market.MarketError as e:
            print("Holiday list problem: %s" % e)
            return 1
        print("Holiday list: %s" % cal.path)
        if len(argv) > 1:
            days = [market.parse_date(argv[1])]
        else:
            today = market.now_ist().date()
            days = [today + _dt.timedelta(days=i) for i in range(8)]
        ok = True
        for d in days:
            st = cal.status(d)
            ok = ok and st.tradable if len(days) == 1 else ok
            print("  %s  %-6s %s" % (d, "OPEN" if st.tradable else "NO", st.reason))
        return 0 if ok else 1
    if cmd == "show":
        try:
            cfg, warnings = load()
        except ConfigError as e:
            print("Config problem: %s" % e)
            return 1
        for w in warnings:
            print("Warning: " + w)
        print(json.dumps(cfg, indent=2))
        return 0
    print("usage: config.py init | show | lists | calendar [YYYY-MM-DD]")
    return 2


if __name__ == "__main__":
    sys.exit(main())

from datetime import datetime, timezone
from math import isfinite, log, sqrt, exp, erf, pi
import requests
import json
from pathlib import Path

def number(value):
    v = float(value)
    if not isfinite(v):
        raise ValueError('Non-finite market data')
    return v

def deribit(method, **params):
    r = requests.get("https://www.deribit.com/api/v2/public/"+method,params=params,timeout=25)
    r.raise_for_status()
    payload=r.json()
    if "error" in payload:
        raise ValueError("Deribit returned an API error")
    return payload["result"]

def option_snapshot():
    """BTC inverse options only, forward Black-76 approximation.
    OI in underlying coin units; call-positive/put-negative gamma convention.
    Not observed dealer positioning. No manufactured history."""
    now=datetime.now(timezone.utc)
    spot=number(deribit("get_index_price",index_name="btc_usd")["index_price"])
    rows=deribit("get_book_summary_by_currency",currency="BTC",kind="option")
    dex=gex=total=used=0.0
    count=0
    strikes={}
    expiries={}
    for row in rows:
        try:
            name=row["instrument_name"].split("-")
            if len(name)!=4 or name[0]!="BTC" or name[-1] not in ("C","P"):
                continue
            oi=number(row["open_interest"])
            if oi<=0:
                continue
            total+=oi
            expiry=datetime.strptime(name[1],"%d%b%y").replace(hour=8,tzinfo=timezone.utc)
            t=(expiry-now).total_seconds()/(365.25*86400)
            k=number(name[2])
            vol=number(row["mark_iv"])/100
            f=number(row["underlying_price"])
            if min(t,k,vol,f,spot)<=0:
                continue
            d1=(log(f/k)+0.5*vol*vol*t)/(vol*sqrt(t))
            delta=0.5*(1+erf(d1/sqrt(2)))
            if name[-1]=="P":
                delta-=1
            gamma=exp(-0.5*d1*d1)/(sqrt(2*pi)*f*vol*sqrt(t))
            dex+=delta*oi*spot
            contribution=(1 if name[-1]=="C" else -1)*gamma*oi*spot*spot*.01
            gex+=contribution
            strikes[k]=strikes.get(k,0)+contribution
            expiries.setdefault(expiry,[]).append((k,name[-1],oi))
            used+=oi
            count+=1
        except (ValueError,TypeError,KeyError,OverflowError,ZeroDivisionError):
            continue
    if not total or not used or used/total<.95:
        raise ValueError("Insufficient option OI coverage")
    # Max pain: nearest future expiry, minimum theoretical USD intrinsic payout.
    nearest=min(expiries)
    contracts=expiries[nearest]
    candidates=sorted({k for k,_,_ in contracts})
    def payout(price):
        return sum(oi*max(0,price-k if side=="C" else k-price) for k,side,oi in contracts)
    pain=min(candidates,key=lambda k:(payout(k),abs(k-spot),k))
    return dict(day=now.date().isoformat(),timestamp=now.isoformat(),dex=dex,gex=gex,
                coverage=used/total,contracts=count,spot=spot,
                gex_strikes=[dict(strike=k,gex=v) for k,v in sorted(strikes.items())],
                max_pain=dict(strike=pain,expiry=nearest.isoformat(),method="minimum USD intrinsic payout"))


def main():
    path=Path("data/btc_options_daily.json")
    rows=json.loads(path.read_text()) if path.exists() else []
    day=datetime.now(timezone.utc).date().isoformat()
    if any(row["day"]==day and row.get("gex_strikes") and row.get("max_pain") for row in rows):
        return
    snap=option_snapshot()
    # Upgrade today's snapshot atomically; previous dates are untouched.
    rows=[row for row in rows if row["day"]!=snap["day"]]
    rows.append(snap)
    rows.sort(key=lambda row:row["day"])
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows,indent=2,allow_nan=False)+"\n")
    tmp.replace(path)

if __name__=="__main__":
    main()

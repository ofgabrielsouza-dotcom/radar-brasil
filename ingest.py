#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Radar Brasil - camada de ingestao
Coleta indicadores de fontes gratuitas, calcula tendencias e z-scores,
e grava data/radar.json que o front-end (index.html) consome.

Roda no GitHub Actions (rede aberta). Tudo degrada com try/except:
uma fonte fora do ar nao derruba o arquivo inteiro.

Fontes:
  - BCB SGS  (SELIC, IPCA 12m, dolar, credito, divida)  -> gratis, sem chave
  - BCB Olinda / Focus (expectativas)                    -> gratis, sem chave
  - Yahoo Finance chart API (Treasury 10Y, DXY, Brent, Ibov) -> gratis, sem chave
  - brapi.dev (Ibovespa, IFIX, acoes B3)                 -> token gratis (env BRAPI_TOKEN)
  - FRED (Treasury, Fed funds) opcional                  -> chave gratis (env FRED_API_KEY)

Lacunas SEM fonte gratuita (continuam manuais no radar): DI futuro, CDS/EMBI,
fluxo estrangeiro, breadth setorial. Marcadas como live=false no JSON.
"""
import os, json, statistics, datetime as dt
import urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (RadarBrasil ingest)"}
TIMEOUT = 25

def _get(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")

def get_json(url, headers=None):
    return json.loads(_get(url, headers))

# ----------------------------------------------------------------------
# BCB SGS  -  ultimos N valores de uma serie
# ----------------------------------------------------------------------
def sgs(codigo, n=1):
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}?formato=json"
    return get_json(url)  # [{"data":"dd/MM/yyyy","valor":"14.50"}, ...]

def sgs_valores(codigo, n):
    return [float(x["valor"]) for x in sgs(codigo, n)]

# BCB Olinda - mediana anual do Focus para o indicador/ano
def focus_anual(indicador, ano, top=2):
    base = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais"
    flt = f"Indicador eq '{indicador}' and DataReferencia eq '{ano}'"
    qs = {"$top": top, "$orderby": "Data desc", "$format": "json",
          "$select": "Indicador,Data,Mediana", "$filter": flt}
    url = base + "?" + urllib.parse.urlencode(qs, quote_via=urllib.parse.quote)
    return get_json(url).get("value", [])

# ----------------------------------------------------------------------
# Yahoo Finance - serie diaria de fechamento (sem chave)
# ----------------------------------------------------------------------
def yahoo_closes(symbol, rng="6mo"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range={rng}&interval=1d")
    j = get_json(url)
    res = j["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    return [c for c in closes if c is not None]

# brapi.dev - cotacao e historico B3 (token gratis)
def brapi_closes(symbol, rng="3mo"):
    token = os.environ.get("BRAPI_TOKEN", "")
    url = f"https://brapi.dev/api/quote/{symbol}?range={rng}&interval=1d"
    if token:
        url += "&token=" + token
    j = get_json(url)
    r = j["results"][0]
    hist = r.get("historicalDataPrice") or []
    closes = [h["close"] for h in hist if h.get("close") is not None]
    return closes or [r["regularMarketPrice"]]

# ----------------------------------------------------------------------
# Helpers de tendencia e z-score
# ----------------------------------------------------------------------
def sig_from_change(diff, tol=1e-9):
    if diff > tol:  return 1
    if diff < -tol: return -1
    return 0

def trend_sig(series, lookback):
    """+1 se a serie subiu na janela, -1 se caiu, 0 estavel."""
    if not series or len(series) < 2:
        return 0
    k = min(lookback, len(series) - 1)
    return sig_from_change(series[-1] - series[-1 - k])

def zscores(series):
    """z-score da variacao mais recente em 1/5/22/66 observacoes."""
    out = {}
    for key, k in (("d1", 1), ("d5", 5), ("d30", 22), ("m3", 66)):
        if not series or len(series) < k + 10:
            out[key] = 0.0
            continue
        ch = [series[i] - series[i - k] for i in range(k, len(series))]
        m = statistics.fmean(ch)
        sd = statistics.pstdev(ch) or 1.0
        out[key] = round((ch[-1] - m) / sd, 2)
    return out

def arrow(diff):
    return "up" if diff > 1e-9 else "down" if diff < -1e-9 else "flat"

# ----------------------------------------------------------------------
# Coleta principal
# ----------------------------------------------------------------------
def build():
    ano = dt.date.today().year
    out = {
        "as_of": dt.datetime.now().isoformat(timespec="seconds"),
        "tickers": {}, "metrics": {}, "blocks": {}, "drivers": [],
        "manual_blocks": [], "notes": {}
    }

    # ---- BCB: SELIC, dolar, IPCA 12m ------------------------------------
    try:
        v = sgs(432, 1)[-1]
        out["tickers"]["selic"] = {"v": v["valor"].replace(".", ",") + "%", "arr": "flat", "date": v["data"]}
        out["notes"]["selic_ref"] = v["data"]
    except Exception as e:
        out["notes"]["selic_err"] = str(e)

    try:
        usd = sgs_valores(1, 400)              # diaria, ~1,5 ano
        last, prev20 = usd[-1], usd[-21] if len(usd) > 21 else usd[0]
        out["tickers"]["usd"] = {"v": f"{last:,.2f}".replace(".", ","), "arr": arrow(last - prev20)}
        out["metrics"]["m-usd"] = {"v": f"{last:,.2f}".replace(".", ","), "arr": arrow(last - prev20), "src": "SGS 1"}
        out["blocks"]["camb"] = sig_from_change(last - prev20)   # real depreciando = +1 (hawkish)
        out["drivers"].append({"nm": "USD/BRL", "live": True, "adverseUp": True, "z": zscores(usd)})
    except Exception as e:
        out["notes"]["usd_err"] = str(e)

    try:
        ipca = sgs_valores(13522, 6)           # IPCA acum 12m, mensal
        out["tickers"]["ipca"] = {"v": f"{ipca[-1]:,.2f}".replace(".", ",") + "%", "arr": arrow(ipca[-1] - ipca[-4] if len(ipca) >= 4 else 0)}
        out["blocks"]["infl"] = trend_sig(ipca, 3)               # inflacao subindo em 3m = +1
    except Exception as e:
        out["notes"]["ipca_err"] = str(e)

    # ---- BCB Focus: expectativas SELIC e IPCA ---------------------------
    try:
        fs = focus_anual("Selic", ano, 2)
        if fs:
            cur = fs[0]["Mediana"]; prev = fs[1]["Mediana"] if len(fs) > 1 else cur
            out["tickers"]["focus_selic"] = {"v": f"{cur:,.2f}".replace(".", ",") + "%", "arr": arrow(cur - prev)}
            # DI curto proxy: expectativa de Selic subindo = curva abrindo
            out["blocks"]["di"] = sig_from_change(cur - prev)
    except Exception as e:
        out["notes"]["focus_selic_err"] = str(e)

    try:
        fi = focus_anual("IPCA", ano, 2)
        if fi:
            cur = fi[0]["Mediana"]; prev = fi[1]["Mediana"] if len(fi) > 1 else cur
            out["blocks"]["exp"] = sig_from_change(cur - prev)   # Focus IPCA subindo = +1
    except Exception as e:
        out["notes"]["focus_ipca_err"] = str(e)

    # ---- BCB: divida bruta / PIB (proxy fiscal) -------------------------
    # 13762 = Divida Bruta do Governo Geral (% PIB). Confirme o codigo se necessario.
    try:
        dbgg = sgs_valores(13762, 6)
        out["blocks"]["fisc"] = trend_sig(dbgg, 3)               # divida subindo = deteriora = +1
    except Exception as e:
        out["manual_blocks"].append("fisc")
        out["notes"]["fisc_err"] = str(e)

    # ---- Exterior: Treasury 10Y + DXY (Yahoo) ---------------------------
    ext_signals = []
    try:
        tnx = yahoo_closes("^TNX")             # 10Y * 10
        out["metrics"]["m-ust"] = {"v": f"{tnx[-1]/10:,.2f}".replace(".", ",") + "%",
                                   "arr": arrow(tnx[-1] - tnx[-6] if len(tnx) > 6 else 0), "src": "Yahoo"}
        out["drivers"].append({"nm": "Treasury 10Y", "live": True, "adverseUp": True, "z": zscores(tnx)})
        ext_signals.append(trend_sig(tnx, 5))
    except Exception as e:
        out["notes"]["tnx_err"] = str(e)
    try:
        dxy = yahoo_closes("DX-Y.NYB")
        out["metrics"]["m-dxy"] = {"v": f"{dxy[-1]:,.1f}".replace(".", ","),
                                   "arr": arrow(dxy[-1] - dxy[-6] if len(dxy) > 6 else 0), "src": "Yahoo"}
        out["drivers"].append({"nm": "DXY", "live": True, "adverseUp": True, "z": zscores(dxy)})
        ext_signals.append(trend_sig(dxy, 5))
    except Exception as e:
        out["notes"]["dxy_err"] = str(e)
    if ext_signals:
        out["blocks"]["ext"] = sig_from_change(sum(ext_signals))  # aperto externo = +1

    # ---- Commodities: Brent (Yahoo) -------------------------------------
    try:
        brent = yahoo_closes("BZ=F")
        out["blocks"]["comm"] = trend_sig(brent, 5)
        out["drivers"].append({"nm": "Petroleo (Brent)", "live": True, "adverseUp": True, "z": zscores(brent)})
    except Exception as e:
        out["notes"]["brent_err"] = str(e)

    # ---- Tecnico/Breadth: Ibovespa vs media 200 dias --------------------
    try:
        try:
            ibov = brapi_closes("^BVSP", "1y")
        except Exception:
            ibov = yahoo_closes("^BVSP", "1y")
        out["metrics"]["m-ibov"] = {"v": f"{ibov[-1]/1000:,.1f}".replace(".", ",") + "k",
                                    "arr": arrow(ibov[-1] - ibov[-6] if len(ibov) > 6 else 0), "src": "brapi"}
        mm200 = statistics.fmean(ibov[-200:]) if len(ibov) >= 200 else statistics.fmean(ibov)
        out["blocks"]["tec"] = 1 if ibov[-1] > mm200 else -1     # acima da MM200 = breadth +
        out["drivers"].append({"nm": "Ibovespa", "live": True, "adverseUp": False, "z": zscores(ibov)})
    except Exception as e:
        out["notes"]["ibov_err"] = str(e)

    # ---- IFIX (brapi) ----------------------------------------------------
    try:
        ifix = brapi_closes("^IFIX", "3mo") if os.environ.get("BRAPI_TOKEN") else None
        if ifix:
            out["metrics"]["m-ifix"] = {"v": f"{ifix[-1]:,.0f}".replace(",", "."),
                                        "arr": arrow(ifix[-1] - ifix[-6] if len(ifix) > 6 else 0), "src": "brapi"}
    except Exception as e:
        out["notes"]["ifix_err"] = str(e)

    # ---- Blocos sem fonte gratuita: continuam manuais -------------------
    for b in ("risk", "ativ", "empr", "cred", "flux"):
        out["manual_blocks"].append(b)
    out["notes"]["manual"] = ("Sem feed gratuito: DI futuro, CDS/EMBI, fluxo estrangeiro, "
                              "breadth setorial. Ajuste manual no radar ou conecte um provider.")

    return out


if __name__ == "__main__":
    data = build()
    os.makedirs("data", exist_ok=True)
    with open("data/radar.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("data/radar.json gravado:", data["as_of"])
    print("blocos automaticos:", sorted(data["blocks"].keys()))
    print("drivers ao vivo:", [d["nm"] for d in data["drivers"]])
    if data.get("notes"):
        print("notas/erros:", {k: v for k, v in data["notes"].items() if k.endswith("_err")})

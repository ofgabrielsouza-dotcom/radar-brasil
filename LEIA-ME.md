# Radar Brasil — automatizado

Pipeline gratuito: GitHub Actions roda `ingest.py` no horario marcado,
gera `data/radar.json` e o GitHub Pages publica o site (`index.html`) que le esse JSON.

## Estrutura
- `index.html` ............ o radar (le `data/radar.json`)
- `ingest.py` ............. coletor de dados (so biblioteca padrao do Python)
- `data/radar.json` ....... saida do coletor (ja vem um exemplo)
- `.github/workflows/update.yml` .. agendador (dias uteis, 11h e 21h UTC)
- `requirements.txt` ...... vazio (sem dependencias)

## Passo a passo: ver no proprio arquivo de instrucoes enviado no chat.

## Segredos opcionais (Settings > Secrets and variables > Actions)
- BRAPI_TOKEN  -> Ibovespa / IFIX / acoes B3 (token gratis em brapi.dev)
- FRED_API_KEY -> Treasury / Fed (chave gratis em fred.stlouisfed.org)
Sem eles, o resto continua funcionando (Treasury e DXY ja vem via Yahoo).

## Lacunas sem fonte gratuita (ajuste manual no radar)
DI futuro, CDS/EMBI, fluxo estrangeiro, breadth setorial.

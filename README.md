# AI Market Decision Prototype — FIX

Correzione del primo MVP. Il problema più probabile del primo pacchetto era legato a una versione vecchia di yfinance. Il progetto ora blocca `yfinance==1.7.0`, release del 26 agosto 2026, e usa `Ticker.history()` anziché il vecchio percorso `yf.download()`.

## Installazione pulita Windows

Nella cartella del progetto:

```cmd
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Se avevi già una vecchia `.venv`, cancellala e ricreala.

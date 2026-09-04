import os
import re
import json
import time
import threading
import queue
import webbrowser
import pandas as pd
from pypdf import PdfReader
from flask import Flask, request, render_template_string, Response

app = Flask(__name__)

# ==========================================
# 1. MOTORE DI ESTRAZIONE LOCALE POTENZIATO
# ==========================================
def estrai_dati_locale(percorso_file, categoria, q):
    nome_file = os.path.basename(percorso_file)
    q.put(f" -> Analisi: {nome_file}")
    
    testo = ""
    try:
        reader = PdfReader(percorso_file)
        for page in reader.pages:
            estratto = page.extract_text()
            if estratto:
                testo += estratto + "\n"
    except Exception as e:
        q.put(f"   [Errore] Impossibile leggere il file.")
        return None
        
    if not testo.strip():
        q.put(f"   [Avviso] Il PDF sembra una scansione/immagine. Verrà inserito 0.")
        
    testo_lower = testo.lower()
    
    # --- TROVA MESE E ANNO ---
    mesi = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 
            'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
    mese_trovato = "Sconosciuto"
    anno_trovato = 2026 # Anno di default
    
    for m in mesi:
        if m in nome_file.lower() or m in testo_lower:
            mese_trovato = m.capitalize()
            break
            
    match_anno = re.search(r"(202\d)", nome_file) or re.search(r"(202\d)", testo_lower)
    if match_anno:
        anno_trovato = int(match_anno.group(1))

    # --- IMPOSTAZIONI E REGEX PER CATEGORIA ---
    consumo = 0.0
    unita = ""
    kwh_conv = 0.0
    
    if categoria == 'energia_elettrica':
        unita = "kWh"
        patterns = [
            r"(?:energia attiva|prelevata|fatturat[oa]|totale|consum[oi])[\s\S]{0,50}?([\d\.,]+)\s*kwh", 
            r"([\d\.,]+)\s*kwh"
        ]
    elif categoria == 'gas' or categoria == 'riscaldamento':
        unita = "sm3"
        patterns = [
            r"(?:consum[oi]|metano|fatturat[oa])[\s\S]{0,50}?([\d\.,]+)\s*(?:sm3|m3|mc)", 
            r"([\d\.,]+)\s*(?:sm3|m3|mc)"
        ]
    elif categoria == 'trasporti':
        # Ottimizzazione specifica per Gasolio/Benzina!
        unita = "litri"
        patterns = [
            r"(?:quantit[aà]|q\.t[aà]|litri|volume|erogat[oa])[\s\S]{0,40}?([\d\.,]+)",
            r"([\d\.,]+)\s*(?:lt|litri|l|l\.)"
        ]
    elif categoria == 'rifiuti':
        unita = "kg"
        patterns = [r"(?:quantit[aà]|peso|totale)[\s\S]{0,40}?([\d\.,]+)\s*(?:kg|ton|t)"]
    elif categoria == 'acqua':
        unita = "mc"
        patterns = [r"(?:consum[oi]|fatturat[oa]|metri cubi)[\s\S]{0,40}?([\d\.,]+)\s*(?:mc|m3)"]
    elif categoria == 'formazione':
        unita = "ore"
        patterns = [r"(?:ore|durata|totale)[\s\S]{0,40}?([\d\.,]+)"]
    else:
        patterns = [r"([\d\.,]+)"]

    # --- APPLICAZIONE REGEX E PULIZIA NUMERI ---
    for pat in patterns:
        matches = re.findall(pat, testo_lower)
        if matches:
            val_str = matches[0]
            if isinstance(val_str, tuple): val_str = val_str[0] # Se ci sono più gruppi
            # Converte il formato italiano (es. 1.234,56 o 1234,56) nel formato PC (1234.56)
            val_str = val_str.replace('.', '').replace(',', '.')
            try:
                consumo = float(val_str)
                break
            except:
                pass

    # --- CALCOLO CONVERSIONE IN KWH ---
    if categoria == 'energia_elettrica':
        kwh_conv = consumo
    elif categoria in ['gas', 'riscaldamento']:
        kwh_conv = round(consumo * 10.5, 2)
    elif categoria == 'trasporti':
        kwh_conv = round(consumo * 10.0, 2) # Stima approssimativa litri -> kWh
    else:
        kwh_conv = 0.0 # Acqua, Rifiuti, Formazione non hanno senso in kWh
        
    q.put(f"   [OK] Estratto: {consumo} {unita}")
    
    return {
        "File": nome_file,
        "Mese": mese_trovato,
        "Anno": anno_trovato,
        "Quantita/Consumo": consumo,
        "Unita_Misura": unita,
        "Valore_Convertito_kWh": kwh_conv if kwh_conv > 0 else ""
    }

# ==========================================
# 2. INTERFACCIA WEB (DASHBOARD A GRIGLIA)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard ESG</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333;}
        .container { max-width: 1100px; margin: 0 auto; }
        .header-panel { background: white; padding: 20px 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; gap: 20px; align-items: center;}
        .header-panel input { flex: 1; padding: 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 1em; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .card { background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); border: 1px solid #eaeaea; display: flex; flex-direction: column; text-align: center; transition: transform 0.2s;}
        .card:hover { transform: translateY(-3px); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        .card h3 { margin-top: 0; color: #202124; font-size: 1.25em; margin-bottom: 15px;}
        .card p { color: #5f6368; font-size: 0.95em; line-height: 1.4; flex-grow: 1; margin-bottom: 25px;}
        .btn { background: #1a73e8; color: white; border: none; padding: 12px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 1em; width: 100%; transition: background 0.3s;}
        .btn:hover { background: #1557b0; }
        h1 { text-align: center; color: #2e7d32; margin-bottom: 5px;}
        .subtitle { text-align: center; color: #666; margin-bottom: 30px;}
    </style>
    <script>
        function elabora(categoria) {
            const cliente = document.getElementById('cliente').value;
            const percorso = document.getElementById('percorso').value;
            
            if(!cliente || !percorso) {
                alert("Inserisci Nome Cliente e Percorso Cartella prima di cliccare sui bottoni.");
                return;
            }
            
            document.getElementById('form_cliente').value = cliente;
            document.getElementById('form_percorso').value = percorso;
            document.getElementById('form_categoria').value = categoria;
            document.getElementById('hiddenForm').submit();
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>Motore di Estrazione ESG</h1>
        <p class="subtitle">Elaborazione 100% Locale, Veloce e Sicura.</p>

        <div class="header-panel">
            <input type="text" id="cliente" placeholder="Nome Azienda / Cliente (es. Cecconato)" required>
            <input type="text" id="percorso" placeholder="Incolla qui il percorso della cartella con i PDF..." required>
        </div>

        <div class="grid">
            <div class="card">
                <h3>Ambiente - Rifiuti</h3>
                <p>Carica qui i file relativi allo scarico dei rifiuti.</p>
                <button class="btn" onclick="elabora('rifiuti')">Cartella Drive "RIFIUTI"</button>
            </div>
            
            <div class="card">
                <h3>Ambiente - Energia Elettrica</h3>
                <p>Carica qui le bollette dell'energia elettrica.</p>
                <button class="btn" onclick="elabora('energia_elettrica')">Cartella Drive "E. E."</button>
            </div>
            
            <div class="card">
                <h3>Ambiente - Gas</h3>
                <p>Carica qui le bollette del gas.</p>
                <button class="btn" onclick="elabora('gas')">Cartella Drive "GAS"</button>
            </div>
            
            <div class="card">
                <h3>Ambiente - Gasolio/Benzina</h3>
                <p>Carica qui le fatture del gasolio o della benzina per autotrazione (esempio furgoni o autovetture).</p>
                <button class="btn" onclick="elabora('trasporti')">Cartella Drive "TRASPORTI"</button>
            </div>
            
            <div class="card">
                <h3>Ambiente - Riscaldamento</h3>
                <p>Carica qui le fatture del metano/GPL per riscaldamento.</p>
                <button class="btn" onclick="elabora('riscaldamento')">Cartella Drive "RISCALDAMENTO"</button>
            </div>
            
            <div class="card">
                <h3>Ambiente - Acqua</h3>
                <p>Carica qui le fatture relative al consumo d'acqua.</p>
                <button class="btn" onclick="elabora('acqua')">Cartella Drive "ACQUA"</button>
            </div>
            
            <div class="card" style="grid-column: 1 / -1; max-width: 400px; margin: 0 auto;">
                <h3>Social - Formazione personale</h3>
                <p>Carica qui le ore di formazione relative al personale.</p>
                <button class="btn" onclick="elabora('formazione')">Cartella Drive "FORMAZIONE"</button>
            </div>
        </div>

        <!-- Form Nascosto per l'invio dati -->
        <form id="hiddenForm" action="/avvia" method="POST" style="display: none;">
            <input type="hidden" name="nome_cliente" id="form_cliente">
            <input type="hidden" name="percorso_root" id="form_percorso">
            <input type="hidden" name="categoria" id="form_categoria">
        </form>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_PAGE)

@app.route('/avvia', methods=['POST'])
def avvia_processo():
    nome_cliente = request.form['nome_cliente'].strip()
    percorso = request.form['percorso_root'].strip('"\'').strip()
    categoria = request.form['categoria'].strip()
    q = queue.Queue()

    def background_worker():
        try:
            if not os.path.exists(percorso):
                q.put(f"ERRORE: La cartella non esiste: {percorso}")
                q.put(("DONE", None))
                return

            q.put(f"Inizializzazione estrazione per categoria: {categoria.upper()}")
            
            file_paths = [os.path.join(percorso, f) for f in os.listdir(percorso) if f.lower().endswith('.pdf')]

            if not file_paths:
                q.put("ATTENZIONE: Nessun file PDF trovato in questa cartella.")
                q.put(("DONE", None))
                return

            q.put(f"Trovati {len(file_paths)} file. Inizio lettura istantanea...")

            dati_estratti = []
            for p in file_paths:
                res = estrai_dati_locale(p, categoria, q)
                if res: dati_estratti.append(res)

            q.put("\n--- Generazione file Excel sul Desktop ---")
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            # Crea un nome file specifico per categoria!
            nome_file_excel = os.path.join(desktop_path, f"Report_{categoria.capitalize()}_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_estratti:
                    # Rinomina il foglio in base alla categoria
                    pd.DataFrame(dati_estratti).to_excel(writer, sheet_name=categoria.capitalize(), index=False)
                else:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: File Excel pronto!")
            q.put(("DONE", nome_file_excel))
        except Exception as err:
            import traceback
            q.put(f"ERRORE CRITICO:\n{traceback.format_exc()}")
            q.put(("DONE", None))

    threading.Thread(target=background_worker).start()

    def generate():
        yield f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Elaborazione in corso...</title>
            <style>
                body {{ font-family: Arial; padding: 40px; background: #f4f6f8; }}
                .box {{ background: white; padding: 25px; border-radius: 8px; max-width: 800px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 0 auto; }}
                #log-box {{ background: #1e1e1e; color: #00ff66; padding: 15px; border-radius: 5px; height: 350px; overflow-y: scroll; font-family: monospace; font-size: 0.95em; margin-top: 15px; white-space: pre-wrap; }}
                h2 {{ color: #1a73e8; text-align: center; margin-top: 0; }}
                .btn {{ display: inline-block; margin-top: 20px; background: #1a73e8; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; }}
            </style>
            <script>
                function appendLog(text) {{
                    const box = document.getElementById('log-box');
                    box.innerHTML += text + "\\n";
                    box.scrollTop = box.scrollHeight;
                }}
            </script>
        </head>
        <body>
            <div class="box">
                <h2>Elaborazione {categoria.replace('_', ' ').title()}</h2>
                <div id="log-box"></div>
                <div id="result-area"></div>
            </div>
        </body>
        </html>
        """
        
        while True:
            item = q.get()
            if isinstance(item, tuple) and item[0] == "DONE":
                file_path = item[1]
                if file_path:
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #2e7d32; text-align:center;\">Processo Completato!</h3><p style=\"text-align:center;\">File generato sul Desktop:<br><b>{file_path}</b></p><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                else:
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #c62828; text-align:center;\">Terminato con errori.</h3><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                break
            else:
                safe_msg = str(item).replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                yield f"<script>appendLog('{safe_msg}');</script>\n"

    return Response(generate(), mimetype='text/html')

if __name__ == '__main__':
    threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, port=5000)

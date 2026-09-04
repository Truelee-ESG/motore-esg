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
# 1. RICERCA CARTELLE
# ==========================================
def trova_e_memorizza_cartelle(percorso_root, nome_cliente, q):
    nome_file_config = f"config_{nome_cliente}.json"
    percorso_root = percorso_root.strip('"\'').strip()
    config = {"ee": None, "gas": None, "root": percorso_root}
    
    if os.path.exists(nome_file_config):
        try:
            with open(nome_file_config, 'r') as f:
                config_salvata = json.load(f)
                if config_salvata.get("root") == percorso_root:
                    config = config_salvata
        except Exception:
            pass

    if not config.get("ee") or not config.get("gas") or not os.path.exists(str(config.get("ee", ""))):
        q.put("Scansione delle cartelle in corso sul PC...")
        for root, dirs, files in os.walk(percorso_root):
            for directory in dirs:
                nome_dir = directory.lower()
                if "energia" in nome_dir or "elettric" in nome_dir or "e.e" in nome_dir or "luce" in nome_dir:
                    config["ee"] = os.path.join(root, directory)
                    q.put(f"[Trovata] Cartella EE: {config['ee']}")
                elif "gas" in nome_dir or "metano" in nome_dir or "gpl" in nome_dir:
                    config["gas"] = os.path.join(root, directory)
                    q.put(f"[Trovata] Cartella Gas: {config['gas']}")

    with open(nome_file_config, 'w') as f:
        json.dump(config, f, indent=4)
        
    return config

# ==========================================
# 2. MOTORE 100% LOCALE (REGEX)
# ==========================================
def estrai_dati_locale(percorso_file, tipo_bolletta, q):
    nome_file = os.path.basename(percorso_file)
    q.put(f" -> Elaborazione istantanea: {nome_file}")
    
    testo = ""
    try:
        reader = PdfReader(percorso_file)
        for page in reader.pages:
            estratto = page.extract_text()
            if estratto:
                testo += estratto + "\n"
    except Exception as e:
        q.put(f"   [Errore Lettura] Impossibile aprire il file.")
        return None
        
    if not testo.strip():
        q.put(f"   [Avviso] Il PDF sembra una scansione/immagine. Verrà inserito Consumo 0.")
        
    testo_lower = testo.lower()
    
    # 1. Trova Mese e Anno (Cerca prima nel nome file, poi nel testo)
    mesi = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 
            'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
    mese_trovato = "sconosciuto"
    anno_trovato = 2026 # Anno di default
    
    for m in mesi:
        if m in nome_file.lower():
            mese_trovato = m
            break
    
    match_anno = re.search(r"(202\d)", nome_file)
    if match_anno:
        anno_trovato = int(match_anno.group(1))
        
    if mese_trovato == "sconosciuto":
        for m in mesi:
            if m in testo_lower:
                mese_trovato = m
                break
    if not match_anno:
        match_anno_testo = re.search(r"(202\d)", testo_lower)
        if match_anno_testo:
            anno_trovato = int(match_anno_testo.group(1))

    # 2. Determina Unità e Tipo Gas
    unita = "kwh" if "elettrica" in tipo_bolletta else "sm3"
    tipo_gas = "" if "elettrica" in tipo_bolletta else "metano"
    
    if "gasolio" in nome_file.lower() or "gasolio" in tipo_bolletta:
        tipo_gas = "gasolio"
        unita = "litri"
    elif "gpl" in nome_file.lower() or "gpl" in tipo_bolletta:
        tipo_gas = "gpl"
        unita = "litri"

    # 3. Estrazione del Consumo tramite pattern intelligenti
    consumo = 0.0
    
    # Pattern: cerca parole chiave seguite da numeri e unità, oppure solo numeri e unità
    patterns = [
        rf"(?:consum[oi]|fatturato|totale|energia attiva|prelevata)[\s\S]{{0,60}}?([\d\.,]+)\s*({unita})",
        rf"([\d\.,]+)\s*({unita})"
    ]
    
    for pat in patterns:
        matches = re.findall(pat, testo_lower)
        if matches:
            val_str = matches[0][0]
            # Converte formato italiano in float (es. 1.234,56 -> 1234.56)
            val_str = val_str.replace('.', '').replace(',', '.')
            try:
                consumo = float(val_str)
                break
            except:
                pass
                
    # 4. Calcolo conversione kWh
    kwh_conv = consumo
    if unita != 'kwh':
        fattore = 1.0
        if 'metano' in tipo_gas and unita in ['sm3', 'm3']:
            fattore = 10.5
        elif 'gpl' in tipo_gas and unita in ['litri', 'l']:
            fattore = 7.0
        kwh_conv = round(consumo * fattore, 2)
        
    q.put(f"   [OK] Estratto: {consumo} {unita} ({kwh_conv} kWh)")
    
    return {
        "nome_file": nome_file,
        "mese": mese_trovato.capitalize(),
        "anno": anno_trovato,
        "consumo": consumo,
        "unita_misura": unita,
        "tipo_gas": tipo_gas.capitalize(),
        "consumo_kwh_convertito": kwh_conv
    }

# ==========================================
# 3. INTERFACCIA WEB E WORKER
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Motore ESG 100% Locale</title>
    <style>
        body { font-family: Arial; padding: 40px; background: #f4f6f8; }
        .box { background: white; padding: 25px; border-radius: 8px; max-width: 500px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 0 auto; }
        input { width: 100%; padding: 10px; margin: 8px 0 16px 0; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 12px 20px; background: #2e7d32; color: white; border: none; border-radius: 4px; cursor: pointer; width: 100%; font-weight: bold; font-size: 1.1em; }
        button:hover { background: #1b5e20; }
        h2 { color: #2e7d32; text-align: center; margin-top: 0; }
        label { font-weight: bold; font-size: 0.9em; color: #333; }
        .badge { background: #e8f5e9; color: #2e7d32; padding: 5px 10px; border-radius: 4px; font-size: 0.8em; display: inline-block; margin-bottom: 20px; border: 1px solid #c8e6c9;}
    </style>
</head>
<body>
    <div class="box">
        <h2>Motore ESG <br><small style="font-size: 0.6em; color: #666;">Elaborazione Locale</small></h2>
        <div style="text-align: center;"><span class="badge">Nessuna API richiesta - Massima Privacy</span></div>
        <form action="/avvia" method="POST">
            <label>Nome Azienda/Cliente (senza spazi):</label>
            <input type="text" name="nome_cliente" placeholder="es. ditta_rossi" required>
            <label>Percorso Server/Cartella Principale (Root):</label>
            <input type="text" name="percorso_root" placeholder="es. C:\\Archivio_Dati" required>
            <button type="submit">Genera Excel Istantaneamente</button>
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
    percorso_root = request.form['percorso_root'].strip()
    q = queue.Queue()

    def background_worker():
        try:
            if not os.path.exists(percorso_root):
                q.put(f"ERRORE: La cartella non esiste: {percorso_root}")
                q.put(("DONE", None))
                return

            q.put("Inizializzazione motore di estrazione LOCALE...")
            config = trova_e_memorizza_cartelle(percorso_root, nome_cliente, q)

            file_ee_paths = []
            if config.get("ee") and os.path.exists(config["ee"]):
                file_ee_paths = [os.path.join(config["ee"], f) for f in os.listdir(config["ee"]) if f.lower().endswith('.pdf')]
                        
            file_gas_paths = []
            if config.get("gas") and os.path.exists(config["gas"]):
                file_gas_paths = [os.path.join(config["gas"], f) for f in os.listdir(config["gas"]) if f.lower().endswith('.pdf')]

            if not file_ee_paths and not file_gas_paths:
                q.put("ATTENZIONE: Nessun file PDF trovato.")
                q.put(("DONE", None))
                return

            q.put(f"Trovati {len(file_ee_paths)} file Energia Elettrica e {len(file_gas_paths)} file Gas.")
            q.put("--- Inizio elaborazione istantanea senza server ---")

            dati_ee = []
            dati_gas = []

            for p in file_ee_paths:
                res = estrai_dati_locale(p, "energia elettrica", q)
                if res: dati_ee.append(res)
                
            for p in file_gas_paths:
                res = estrai_dati_locale(p, "gas", q)
                if res: dati_gas.append(res)

            q.put("\n--- Generazione file Excel sul Desktop ---")
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            nome_file_excel = os.path.join(desktop_path, f"Report_Consumi_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_ee: pd.DataFrame(dati_ee).to_excel(writer, sheet_name='Energia_Elettrica', index=False)
                if dati_gas: pd.DataFrame(dati_gas).to_excel(writer, sheet_name='Gas', index=False)
                if not dati_ee and not dati_gas:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: File Excel pronto: {nome_file_excel}")
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
            <title>Elaborazione Consumi ESG</title>
            <style>
                body {{ font-family: Arial; padding: 40px; background: #f4f6f8; }}
                .box {{ background: white; padding: 25px; border-radius: 8px; max-width: 800px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 0 auto; }}
                #log-box {{ background: #1e1e1e; color: #00ff66; padding: 15px; border-radius: 5px; height: 400px; overflow-y: scroll; font-family: monospace; font-size: 0.9em; margin-top: 15px; white-space: pre-wrap; }}
                h2 {{ color: #2e7d32; text-align: center; margin-top: 0; }}
                .btn {{ display: inline-block; margin-top: 20px; background: #2e7d32; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; }}
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
                <h2>Elaborazione Consumi ESG <br><small style="color:#666; font-size:0.6em;">Offline & Istantanea</small></h2>
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
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #2e7d32; text-align:center;\">Processo Completato in 1 Secondo!</h3><p style=\"text-align:center;\">File salvato sul Desktop:<br><b>{file_path}</b></p><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
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
